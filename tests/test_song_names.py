"""bot/core/song_names.py — YouTube adlarının təmizlənməsi: adın real hissəsi silinmir, "zibil" silinir."""
import pytest

from bot.core import song_names as N
from bot.plugins import music_plugin as MP


def S(title):
    return N.split_title(title)


# ── köhnə düzgün davranış dəyişməməlidir ──
@pytest.mark.parametrize("title,expected", [
    ("Eminem - Lose Yourself (Official Video) [HD] 12.05.2024", ("Eminem", "Lose Yourself")),
    ("Tural Sadigli - Gəl Mənimlə | Official Music Video", ("Tural Sadigli", "Gəl Mənimlə")),
    ("Elnur Valeh & Samir Şahin — Sən Gedəndən Sonra (Rəsmi Klip) 2024", ("Elnur Valeh & Samir Şahin", "Sən Gedəndən Sonra")),
    ("Rəhim Rəhimov – Yar Yar (Lyrics) | Sözləri", ("Rəhim Rəhimov", "Yar Yar")),
    ("Zeynəb Həsəni - Hardasan?? | Yeni Klip 2023", ("Zeynəb Həsəni", "Hardasan??")),
    ("Kenan Doğulu - Yaz Bunu Bir Kenara (Official Video) | 4K", ("Kenan Doğulu", "Yaz Bunu Bir Kenara")),
    ("Бони - Не ревнуй (Official video) 2022", ("Бони", "Не ревнуй")),
    ("Miri Yusif - Sevgilim (Mahnı sözləri) (Lyrics) (Official Audio)", ("Miri Yusif", "Sevgilim")),
    ("Sevil Sevinc - Bu Gecə (Remix) (Prod. by DJ Ruslan)", ("Sevil Sevinc", "Bu Gecə (Remix)")),
    ("Aygün Kazımova - Mənim Sevgilim: Official Video", ("Aygün Kazımova", "Mənim Sevgilim")),
    ("Fuad Qasimov - Xəyal | Hit 2022", ("Fuad Qasimov", "Xəyal")),
    ("Kamran Quliyev - Sən Gedəndən Sonra  (Official Video 4K) [Prod. by Kamran]", ("Kamran Quliyev", "Sən Gedəndən Sonra")),
    ("Aygün - Qalıb (Prod. Dir)", ("Aygün", "Qalıb")),
    ("Xəzər - Ana (2019)", ("Xəzər", "Ana")),
    ("Bush - In A Lonely Place (Tricky Mix / Remastered)", ("Bush", "In A Lonely Place (Tricky Mix)")),
])
def test_noise_is_still_removed(title, expected):
    assert S(title) == expected


# ── 1) adın real hissəsi qalır ──
@pytest.mark.parametrize("title,expected", [
    ("Elçin Cəfərov - Sən Mənimsən (Yeni Başlanğıc)", ("Elçin Cəfərov", "Sən Mənimsən (Yeni Başlanğıc)")),
    ("Nigar Cabbarlı - Sevgi Yeni", ("Nigar Cabbarlı", "Sevgi Yeni")),
    ("Colbie Caillat - Old and New", ("Colbie Caillat", "Old and New")),
    ("Elnur - Yeni", ("Elnur", "Yeni")),
    ("Kylie Minogue - Light Years (Live in New York)", ("Kylie Minogue", "Light Years (Live in New York)")),
    ("Dj X - Song (TikTok Remix)", ("Dj X", "Song (TikTok Remix)")),
    ("Joker - Darbe (New School Remix)", ("Joker", "Darbe (New School Remix)")),
])
def test_real_title_words_survive(title, expected):
    assert S(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("Dj X - Song (Official Remix)", ("Dj X", "Song (Remix)")),                       # "Remix" itmir
    ("Otilia - Bilionera (Rino Aqua & Md DJ Official Remix)", ("Otilia", "Bilionera (Rino Aqua & Md DJ Remix)")),
    ("Push - Strange World (Remastered Tranquilo's Underwater Kingdom Mix) (feat. Tranquilo)",
     ("Push", "Strange World (Tranquilo's Underwater Kingdom Mix) (feat. Tranquilo)")),
])
def test_remix_name_stays_when_only_the_promo_word_goes(title, expected):
    assert S(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("Artist - Song (Yeni)", ("Artist", "Song")),
    ("Artist - Song (New Hit)", ("Artist", "Song")),
    ("Artist - Song (Yeni Versiya)", ("Artist", "Song")),
    ("Artist - Song (Full Version)", ("Artist", "Song")),
    ("Artist - Song (Full Length Version)", ("Artist", "Song")),
    ("Artist - Song - Yeni", ("Artist", "Song")),
    ("Artist - Song | New", ("Artist", "Song")),
])
def test_promo_only_brackets_and_separated_markers_are_dropped(title, expected):
    assert S(title) == expected


def test_leftover_junk_is_tidied():
    assert S("Paul Wall - My Money Gets Jealous ( Remix )") == ("Paul Wall", "My Money Gets Jealous (Remix)")
    assert S("Showtek - Booyah (Radio -) (feat. X)") == ("Showtek", "Booyah (Radio) (feat. X)")
    assert S("Major Lazer - Jump (feat. Busy Signal) (feat. Busy Signal)") == ("Major Lazer", "Jump (feat. Busy Signal)")


# ── 5) "- Topic" ──
def test_topic_suffix_is_removed_from_the_title_but_artist_alone_is_kept():
    assert S("Rəşad Dağlı - Vur Məni (Yeni 2024) - Topic") == ("Rəşad Dağlı", "Vur Məni")
    assert N.clean_artist("Tural Sadigli - Topic") == "Tural Sadigli"


# ── 4) Artist “Ad” ──
@pytest.mark.parametrize("title", [
    "Samir Piriyev “Sən Yoxsan” Official Music Video",
    'Samir Piriyev "Sən Yoxsan" (Rəsmi Klip)',
    "Samir Piriyev «Sən Yoxsan» | Official Video",
])
def test_quoted_title_without_separator(title):
    assert S(title) == ("Samir Piriyev", "Sən Yoxsan")


def test_only_a_quoted_title_has_no_artist():
    assert S("“Sən Yoxsan”")[0] == "YouTube"


# ── 2-4) kanal adı ilə təsdiq ──
@pytest.mark.parametrize("raw,artist,track,channel,expected", [
    # tərs sıra: kanal "ad" hissəsidir
    ("Sevgilim - Miri Yusif", "Sevgilim", "Miri Yusif", "Miri Yusif Official", ("Miri Yusif", "Sevgilim")),
    ("Sevgilim - Miri Yusif (Official Video)", "Sevgilim", "Miri Yusif", "Miri Yusif - Topic", ("Miri Yusif", "Sevgilim")),
    # düzgün sıra və ya kanal heç birinə uyğun deyil: toxunulmur
    ("Miri Yusif - Sevgilim", "Miri Yusif", "Sevgilim", "Miri Yusif", ("Miri Yusif", "Sevgilim")),
    ("Miri Yusif - Sevgilim", "Miri Yusif", "Sevgilim", "Mahnı Dünyası", ("Miri Yusif", "Sevgilim")),
    ("Sevgilim - Miri Yusif", "Sevgilim", "Miri Yusif", "Mahnı Dünyası", ("Sevgilim", "Miri Yusif")),
    # "Ad | Artist | Official Video"
    ("Sevgilim | Miri Yusif | Official Video", "YouTube", "Sevgilim", "Miri Yusif", ("Miri Yusif", "Sevgilim")),
    ("Miri Yusif | Sevgilim", "YouTube", "Miri Yusif", "Miri Yusif Official", ("Miri Yusif", "Sevgilim")),
    ("Sevgilim | Miri Yusif | Official Video", "YouTube", "Sevgilim", "Mahnı Dünyası", ("YouTube", "Sevgilim")),
    # ayırıcısız: ad kanal adı ilə başlayır
    ("Samir Piriyev Sən Yoxsan Official Video", "YouTube", "Samir Piriyev Sən Yoxsan", "Samir Piriyev",
     ("Samir Piriyev", "Sən Yoxsan")),
    ("Samir Piriyev - Sən Yoxsan", "YouTube", "Sən Yoxsan", "", ("YouTube", "Sən Yoxsan")),
    ("Samir Piriyev", "YouTube", "Samir Piriyev", "Samir Piriyev", ("YouTube", "Samir Piriyev")),   # ad qalmır
])
def test_refine_with_channel(raw, artist, track, channel, expected):
    assert N.refine_with_channel(raw, artist, track, channel) == expected


def test_a_song_that_merely_shares_a_word_with_the_channel_is_not_swapped():
    # kanal "Sevgi TV" → "Sevgi"; ad "Sevgi" olsa da artist kanalla uyğundursa toxunulmur
    assert N.refine_with_channel("Elnur Valeh - Sevgi", "Elnur Valeh", "Sevgi", "Elnur Valeh") == ("Elnur Valeh", "Sevgi")
    assert N.refine_with_channel("Elnur - Sevgi Gəlir", "Elnur", "Sevgi Gəlir", "Sevgi") == ("Elnur", "Sevgi Gəlir")


# ── music_plugin: həqiqi yol (youtube_fallback_meta) ──
@pytest.fixture
def meta(monkeypatch):
    store = {}
    monkeypatch.setattr(MP.yt_handler, "VIDEO_META", store, raising=False)
    return store


def test_fallback_meta_uses_channel_to_fix_reversed_order(meta):
    meta["v1"] = {"title": "Sevgilim - Miri Yusif", "channel": "Miri Yusif Official", "artist": None, "track": None}
    artist, track, src = MP.resolve_meta("Sevgilim - Miri Yusif")
    assert MP.youtube_fallback_meta("v1", artist, track, src) == ("Miri Yusif", "Sevgilim", "youtube+kanal")


def test_fallback_meta_prefers_youtube_music_fields_and_songlink(meta):
    meta["v2"] = {"title": "Sevgilim - Miri Yusif", "channel": "Miri Yusif", "artist": "Miri Yusif", "track": "Sevgilim"}
    assert MP.youtube_fallback_meta("v2", "Sevgilim", "Miri Yusif", "youtube")[2] == "youtube-music"
    assert MP.youtube_fallback_meta("v2", "A", "B", "songlink") == ("A", "B", "songlink")


def test_fallback_meta_keeps_old_channel_fallback(meta):
    meta["v3"] = {"title": "Sən Yoxsan", "channel": "Samir Piriyev - Topic", "artist": None, "track": None}
    assert MP.youtube_fallback_meta("v3", "YouTube", "Sən Yoxsan", "youtube") == ("Samir Piriyev", "Sən Yoxsan", "kanal")
    assert MP.youtube_fallback_meta("zzz", "YouTube", "X", "youtube") == ("Naməlum", "X", "youtube")
