"""
💎 Premium / Free / 👑 Creator imkanları — common/tiers.py (bütün plugin-lər üçün tək mənbə)

Hər imkan hər rol üçün ayrıca açılıb-bağlanır: 👑 Creator, 💎 Premium, 🆓 Free.
  /menu → 💎 Premium → 🎛 İmkanlar (bot/plugins/features_plugin.py) — hər üç rol + limitlər
  👑 Creator default: hamısı açıq
settings cədvəlində saxlanır: tier:<imkan>:<rol>, limitlər: tierlim:<limit>:<rol>
"via | song.link" yazısının creator dəyəri köhnə kimi creator_caption ayarıdır.

  allowed(imkan, user_id)  — bu istifadəçi imkanı işlədə bilərmi
  limit_for(limit, uid)    — rəqəmsal limit (0 = limitsiz)
  perks() / perks_html()   — Premium-un Free-dən ARTIQ verdiyi imkanlar (satış / bildiriş mətnləri üçün)
"""
from common.database import (creator_caption_enabled, get_db, is_creator, is_premium,
                             set_creator_caption)

# açar: (ad, izah, default {premium, free}, "bu imkan premium-a nə verir" — perks siyahısında görünən mətn)
# creator default-u: həmişə True (CREATOR_DEFAULT)
FEATURES = {
    "spotify_links":     ("🟢 Spotify mahnı/albom", "tək mahnı və ya albom linkini yükləmək",
                          {"premium": True, "free": True},
                          "🟢 <b>Spotify mahnıları</b> — mahnı və albom linklərindən yükləmə"),
    "spotify_playlists": ("🟢 Spotify playlistləri", "Spotify playlist linklərini yükləmək",
                          {"premium": True, "free": False},
                          "🟢 <b>Spotify playlistləri</b> — bütün Spotify playlistləri bir toxunuşla yüklənsin"),
    "youtube_links":     ("▶️ YouTube mahnı/video", "tək video və ya mahnı linkini yükləmək",
                          {"premium": True, "free": True},
                          "▶️ <b>YouTube linkləri</b> — video və musiqi linklərindən yükləmə"),
    "youtube_playlists": ("▶️ YouTube playlistləri", "YouTube playlist linklərini yükləmək",
                          {"premium": True, "free": False},
                          "▶️ <b>YouTube playlistləri</b> — bütün YouTube playlisti bir toxunuşla yüklənsin"),
    "vk_links":          ("🔵 VK mahnı/video", "VK mahnı və ya video linkini yükləmək",
                          {"premium": True, "free": True},
                          "🔵 <b>VK musiqi və video</b> — vk.com və vk.ru linklərindən yükləmə"),
    "vk_playlists":      ("🔵 VK playlistləri", "VK playlist/albom linklərini yükləmək",
                          {"premium": True, "free": False},
                          "🔵 <b>VK playlistləri</b> — bütün VK playlistləri bir toxunuşla yüklənsin"),
    "mix":               ("🔀 Mix (Oxşar mahnılar)", "/mix komandası və YouTube Mix / Radio",
                          {"premium": True, "free": True},
                          "🔀 <b>Mix (Oxşar mahnılar)</b> — sevdiyin mahnıya uyğun avtomatik playlist"),
    "dlall":             ("⬇️ Hamısını yüklə", "axtarış / siyahıdakı bütün mahnıları birdən yükləmək",
                          {"premium": True, "free": False},
                          "⬇️ <b>Hamısını yüklə</b> — siyahıdakı bütün mahnılar bir toxunuşla"),
    "artist_search":     ("🎙 İfaçı axtarışı", "/artist — ifaçının bütün mahnılarını tapıb yükləmək",
                          {"premium": True, "free": True},
                          "🎙 <b>İfaçı axtarışı</b> — ifaçının bütün mahnıları bir siyahıda"),
    "playlists":         ("🎶 Playlistlər", "öz playlistlərini yaratmaq, mahnı əlavə etmək, dinləmək",
                          {"premium": True, "free": True},
                          "🎶 <b>Playlistlər</b> — sevdiyin mahnıları öz siyahılarına yığ"),
    "playlist_import":   ("💾 Linkdən playlist", "YouTube / Spotify playlistini öz playlisti kimi saxlamaq",
                          {"premium": True, "free": False},
                          "💾 <b>Linkdən playlist</b> — YouTube / Spotify playlistini bir toxunuşla özünə saxla"),
    "playlist_dlall":    ("▶️ Playlisti birdən yüklə", "playlistdəki bütün mahnıları bir toxunuşla almaq",
                          {"premium": True, "free": False},
                          "▶️ <b>Playlisti birdən yüklə</b> — bütün playlist bir toxunuşla"),
    "audio_edit":        ("✏️ Metadata redaktə", "audio faylının adını, artistini və şəklini dəyişmək",
                          {"premium": True, "free": True},
                          "✏️ <b>Metadata redaktə</b> — mahnının adını, artistini və şəklini dəyiş"),
    "songlink":          ("🔗 via | song.link yazısı", "mahnının altında \"via | song.link\" yazısı",
                          {"premium": True, "free": True},
                          "🎵 Mahnılar <b>\"via | song.link\" yazısı olmadan</b>, təmiz gəlir"),
    "broadcast":         ("📢 Broadcast mesajları", "elan / reklam mesajlarını almaq",
                          {"premium": True, "free": True},
                          "🔕 <b>Elan / reklam mesajları gəlmir</b>"),
}
ORDER = [
    "spotify_links", "spotify_playlists",
    "youtube_links", "youtube_playlists",
    "vk_links", "vk_playlists",
    "mix",
    "dlall", "artist_search", "playlists", "playlist_import", "playlist_dlall",
    "audio_edit", "songlink", "broadcast"
]
# "yazı / mesaj" tipli imkanlar: Premium-da BAĞLI olmaq üstünlükdür (perks məntiqi tərsdir)
NEGATIVE = {"songlink", "broadcast"}
TIERS = ("premium", "free")                          # 🎛 İmkanlar panelinin sütunları
ROLES = ("creator", "premium", "free")               # bütün rollar
TIER_LABEL = {"premium": "💎 Premium", "free": "🆓 Free"}
ROLE_LABEL = {"creator": "👑 Creator", **TIER_LABEL}
CREATOR_DEFAULT = True

# 🔢 rəqəmsal limitlər: açar → (ad, default {rol: dəyər}, seçim pillələri). 0 = limitsiz
LIMITS = {
    "playlists":      ("🎶 Playlist sayı", {"creator": 0, "premium": 50, "free": 3},
                       (1, 3, 5, 10, 20, 50, 100, 0)),
    "playlist_songs": ("🎵 Bir playlistdə mahnı", {"creator": 0, "premium": 1000, "free": 50},
                       (10, 25, 50, 100, 200, 500, 1000, 2000, 0)),
}


# ───────────── imkanlar (açıq / bağlı) ─────────────
def get(feature: str, tier: str) -> bool:
    if tier == "creator" and feature == "songlink":
        return creator_caption_enabled()
    default = FEATURES[feature][2].get(tier, CREATOR_DEFAULT)
    try:
        v = get_db().get_setting(f"tier:{feature}:{tier}")
    except Exception:
        v = None
    return default if v is None else v == "1"


def put(feature: str, tier: str, value: bool):
    if tier == "creator" and feature == "songlink":
        set_creator_caption(value)
        return
    get_db().set_setting(f"tier:{feature}:{tier}", "1" if value else "0")


def toggle(feature: str, tier: str) -> bool:
    new = not get(feature, tier)
    put(feature, tier, new)
    return new


def reset(include_creator: bool = False):
    """Premium / Free imkanları və limitləri default-a qaytarır (creator-unkuna toxunmur, istənməsə)."""
    roles = ROLES if include_creator else TIERS
    for f in FEATURES:
        for t in roles:
            if t == "creator" and f == "songlink":
                continue
            get_db().set_setting(f"tier:{f}:{t}", None)
    for name in LIMITS:
        for t in roles:
            get_db().set_setting(f"tierlim:{name}:{t}", None)
    get_db().set_setting(SHOW_LOCKED_KEY, None)


def role_of(user_id) -> str:
    if user_id and is_creator(user_id):
        return "creator"
    return tier_of(user_id)


def tier_of(user_id) -> str:
    return "premium" if user_id and is_premium(user_id) else "free"


def allowed(feature: str, user_id) -> bool:
    return get(feature, role_of(user_id))


# ───────────── 👁 görünmə: bağlı imkan istifadəçiyə göstərilsinmi ─────────────
SHOW_LOCKED_KEY = "tier_show_locked"


def show_locked() -> bool:
    """True — Free-yə bağlı, amma Premium-da açıq imkanlar 💎 nişanı ilə görünür (satış üçün).
    False (default) — bağlı imkanlar heç yerdə göstərilmir."""
    try:
        return get_db().get_setting(SHOW_LOCKED_KEY) == "1"
    except Exception:
        return False


def set_show_locked(value: bool):
    get_db().set_setting(SHOW_LOCKED_KEY, "1" if value else "0")


def visible(feature: str, user_id) -> bool:
    """Düymə / mətn sətri bu istifadəçiyə göstərilsinmi."""
    role = role_of(user_id)
    if get(feature, role):
        return True
    return role == "free" and show_locked() and get(feature, "premium")


def locked(feature: str, user_id) -> bool:
    """Görünür, amma işləmir (💎 nişanı lazımdır)."""
    return visible(feature, user_id) and not allowed(feature, user_id)


# Mətnlərdə (salam, bələdçilər — bütün dillərdə) imkana aid sətirlər sətrin əvvəlindəki emoji ilə tanınır
LINE_MARKS = {
    "audio_edit": ("✏️",),
    "playlists": ("🎶",),
    "artist_search": ("🎙",),
    "mix": ("🔀",),
    "_links": ("🔗",),                     # Spotify və YouTube linklərinin heç biri görünmürsə
}


def strip_hidden(text: str, user_id) -> str:
    """Bu istifadəçiyə görünməyən imkanların sətirlərini mətndən çıxarır."""
    hidden = []
    for key, marks in LINE_MARKS.items():
        if key == "_links":
            off = not (
                visible("spotify_links", user_id)
                or visible("spotify_playlists", user_id)
                or visible("youtube_links", user_id)
                or visible("youtube_playlists", user_id)
                or visible("vk_links", user_id)
                or visible("vk_playlists", user_id)
            )
        else:
            off = not visible(key, user_id)
        if off:
            hidden += marks
    if not hidden:
        return text
    out = [line for line in text.split("\n") if not line.lstrip().startswith(tuple(hidden))]
    return "\n".join(out)


# ───────────── rəqəmsal limitlər ─────────────
def get_limit(name: str, role: str) -> int:
    default = LIMITS[name][1].get(role, 0)
    try:
        v = get_db().get_setting(f"tierlim:{name}:{role}")
        return int(v) if v is not None else default
    except Exception:
        return default


def set_limit(name: str, role: str, value: int):
    get_db().set_setting(f"tierlim:{name}:{role}", str(max(0, int(value))))


def step_limit(name: str, role: str, direction: int = 1) -> int:
    """Pillə üzrə artırır (+1) və ya azaldır (-1)."""
    steps = LIMITS[name][2]
    cur = get_limit(name, role)
    if cur in steps:
        idx = steps.index(cur) + direction
    else:
        def _val(x):
            return float("inf") if x == 0 else x
        cur_v = _val(cur)
        closest_idx = min(range(len(steps)), key=lambda i: abs(_val(steps[i]) - cur_v))
        idx = closest_idx + direction
    idx = max(0, min(len(steps) - 1, idx))
    new_val = steps[idx]
    set_limit(name, role, new_val)
    return new_val


def limit_for(name: str, user_id) -> int:
    return get_limit(name, role_of(user_id))


def fmt_limit(v: int) -> str:
    return "∞" if not v else str(v)


def _bigger(p: int, f: int) -> bool:
    """Premium limiti Free-dən böyükdürmü (0 = limitsiz)."""
    if p == f:
        return False
    return p == 0 or (f != 0 and p > f)


# ───────────── mətnlər ─────────────
def perks() -> list:
    """Premium-un Free-dən fərqli verdiyi imkanlar (mətn sətirləri) — 🌐 istifadəçinin dilində."""
    from common.i18n import _
    out = []
    for f in ORDER:
        p, fr = get(f, "premium"), get(f, "free")
        if f in NEGATIVE:
            if not p and fr:                 # free-də var, premium-da yox → üstünlük
                out.append(_(FEATURES[f][3]))
        elif p and not fr:
            out.append(_(FEATURES[f][3]))
    if get("playlists", "premium"):
        pc, fc = get_limit("playlists", "premium"), get_limit("playlists", "free")
        ps, fs = get_limit("playlist_songs", "premium"), get_limit("playlist_songs", "free")
        if get("playlists", "free") and (_bigger(pc, fc) or _bigger(ps, fs)):
            out.append(_("🎶 <b>Daha çox playlist</b> — {pc} playlist, hər birində {ps} mahnıya qədər "
                         "(Free: {fc} × {fs})", pc=fmt_limit(pc), ps=fmt_limit(ps), fc=fmt_limit(fc), fs=fmt_limit(fs)))
    return out


def perks_html(bullet: str = "• ") -> str:
    from common.i18n import _
    items = perks()
    if not items:
        return _("<i>Hazırda Premium və Free eyni imkanlara malikdir.</i>")
    return "\n".join(bullet + p for p in items)


def gate_text(feature: str, user_id=None) -> str:
    """Bağlı imkana toxunanda göstərilən mesaj."""
    from common.i18n import _
    name = _(FEATURES[feature][0])
    if user_id and is_creator(user_id):
        return _("👑 <b>{name}</b> sənin üçün bağlıdır — /menu → 💎 Premium → 🎛 İmkanlar",
                 name=name)
    if get(feature, "premium"):
        return (_("💎 <b>{name} — yalnız Premium üçündür</b>", name=name) + "\n\n"
                + _("✨ <b>Premium ilə əldə edəcəklərin:</b>") + f"\n{perks_html()}\n\n"
                + _("⭐ Telegram Stars ilə bir neçə saniyəyə aktivləşir — /premium"))
    return _("⛔ <b>{name}</b> hazırda bağlıdır.", name=name)
