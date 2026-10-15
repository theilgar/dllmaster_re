from bot.core import song_names as sn


def test_news_and_trailers_flagged():
    assert sn.non_music_reason("Son Dakika Haber Bülteni", 300)
    assert sn.non_music_reason("Yeni Film Fragman 2026", 120)
    assert sn.non_music_reason("Magazin Xəbərləri", 400)


def test_songs_pass_and_query_words_respected():
    assert not sn.non_music_reason("Rəsul Gunəşli - Sevgi (Official Video)", 210, "rəsul sevgi")
    assert not sn.non_music_reason("Kino - Gruppa Krovi", 280, "kino gruppa krovi")   # sorğuda özü var
    assert sn.non_music_reason("Reklam", 20, "", min_sec=60)
    assert not sn.non_music_reason("Qısa mahnı", 20, "")
