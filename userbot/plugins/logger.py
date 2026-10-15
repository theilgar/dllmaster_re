"""🛡 logger.py — qrup auditi (silinən / redaktə / ban / üzvlər / admin və qrup hadisələri) və loq hədəfləri.

⏺ Səsli yayım qeydi burada YOXDUR — yayımı yalnız 🎙 canlı yayım asistanı qeyd edir (bot/core/va_record.py).
.rec kartının ⏺ bölməsi asistanın qeydini idarə edir (/vrec və .vcrec ilə eyni): ⏺ / ⏹ · ⚡ avto · panel.
Əvvəlki ayrıca qeydçi (əsas hesab yayıma girirdi) silinib — iki hesab eyni yayıma qoşulurdu.

Panel (.rec və .menu → 🎙 Səs & Audit — eyni ekranlar, eyni düymələr):
  🏠 Əsas     ⏺ Qeydə başla / ⏹ Saxla · ⚡ Avto · ⏺ Qeyd paneli (botda)
              🛡 Audit · 🌐 Bütün / 🎯 Seçilmiş çatlar (➕/➖ Bu çat) · 📤 Hədəflər · 📜 Jurnal · ❓
  📤 Hədəflər hər axın / hadisə üzrə ayrıca hədəf (qrup, kanal, forum topiki, Saved Messages)
  📜 Jurnal   🗑 Silinən · ✏️ Redaktə · ⛔ Ban · 🚪 Üzvlər · 👮 Əməllər · 🧹 Təmizlə

Komandalar:
  .rec                          — panel
  .rec start|stop [@qrup]       — ⏺ asistanın qeydi (qrupda: komanda silinir, nəticə botun panelində)
  .rec auto [on|off]            — ⚡ asistan yayım başlayanda özü qeyd etsin
  .rec target [axın] <hədəf> [topic] — hədəf: @kanal · -100… · t.me/c/…/42 · here · me · reset
  .rec audit on|off|all|here    — audit
  .rec target del|ed|ban|mbr|adm on|off — audit axınını ayrıca aç / söndür (jurnalda qalır)
  .deleted · .edited · .bans · .members · .actions [say] — jurnal
"""
import asyncio
import collections
import contextlib
import contextvars
import json
import logging
import re
import time
from html import escape, unescape

from telethon import events, types, utils
from telethon.errors import FloodWaitError

from common.destinations import extract_topic_id, parse_destination_target as parse_target_string
from common.utilities import format_clock as fmt_dur, tg_media_label as get_media_label, tg_name
from userbot.core.userbot_api import ff

logger = logging.getLogger(__name__)

# Keş və jurnal strukturları
MAX_MSG_CACHE = 5000
_msg_cache = collections.OrderedDict()        # (chat_id, msg_id) -> dict
_mid_index: dict = {}                          # msg_id -> chat_id (yalnız şəxsi / basic qrup: ID-lər hesab üzrə unikaldır)
_deleted_history = collections.defaultdict(lambda: collections.deque(maxlen=60))  # chat_id -> deque
_edited_history = collections.defaultdict(lambda: collections.deque(maxlen=60))   # chat_id -> deque
_bans_history = collections.defaultdict(lambda: collections.deque(maxlen=60))     # chat_id -> deque
_members_history = collections.defaultdict(lambda: collections.deque(maxlen=60))  # chat_id -> deque
_admin_history = collections.defaultdict(lambda: collections.deque(maxlen=60))    # chat_id -> deque
_recent_events = {}         # (chat_id, user_id, növ) -> vaxt: servis mesajı + UpdateChannelParticipant təkrarı olmasın
DEDUP_SECONDS = 20


def _seen(key) -> bool:
    """Bu hadisə son DEDUP_SECONDS ərzində artıq loqa düşübsə True; yoxdursa qeyd edir və False."""
    now = time.time()
    for k in [k for k, t in _recent_events.items() if now - t > DEDUP_SECONDS]:
        _recent_events.pop(k, None)
    if key in _recent_events:
        return True
    _recent_events[key] = now
    return False



# ───────────────────────── ⏺ Yayım qeydi — yalnız 🎙 asistan (bot/core/va_record.py) ─────────────────────────
# Əvvəl burada əsas hesabın ayrıca qeydçisi (öz avtomatik rejimi, keyfiyyəti, transkripti) var idi —
# eyni yayıma iki hesab qoşulurdu. İndi .rec kartı yalnız asistanın qeydini idarə edir (/vrec ilə eyni).
def _va(ub):
    va = getattr(ub.context, "voice_assistant", None)
    return va if va is not None and getattr(va, "rec", None) is not None else None


def _plain(note: str) -> str:
    """Asistanın HTML qeydi → kart üçün düz mətn (ff() özü escape edir)."""
    return unescape(re.sub(r"<[^>]+>", "", note or ""))


async def assistant_rec(ub, cid: int, act: str) -> str:
    """⏺ start · ⏹ stop · 📍 mark · ⚡ auto · ✋ manual → asistanın qeydçisi; nəticə düz mətn."""
    va = _va(ub)
    if va is None:
        return "⚠️ 🎙 Canlı yayım asistanı yüklənməyib (bot rejimi söndürülüb?)"
    if cid is None or cid > 0:
        return "⏺ Qeyd qrupda idarə olunur — qrupda .rec və ya botda /vrec"
    from bot.core import va_record
    title = (va.rec.sessions[cid].title if cid in va.rec.sessions
             else va_record.auto_chats().get(cid) or va_record.known_groups().get(cid) or str(cid))
    try:
        return _plain(await va.rec.action(cid, title, act, by="userbot"))
    except Exception as e:
        return f"❌ {e}"

# İstifadəçi mətn daxiletmə növbəsi: chat_id -> dict
_WAIT_INPUT = {}


# ───────────────────────── 🎙 Yüksək Keyfiyyətli Səs Qeydiyyatçısı ─────────────────────────



# ───────────────────────── 🛠 Köməkçi Funksiyalar ─────────────────────────


def get_user_name(user) -> str:
    return tg_name(user, "Bilinmir")


_group_titles: dict = {}          # chat_id -> ad (yalnız qrup / kanallar) — qrup üzrə ayarlar siyahısı üçün
_groups_dirty = False


def _remember_group(cid, title):
    global _groups_dirty
    if cid is None or cid >= 0 or _group_titles.get(cid) == title:
        return
    _group_titles[cid] = title
    _groups_dirty = True


def known_groups(ub) -> dict:
    """{chat_id: ad} — userbot / asistanın mesaj gördüyü BÜTÜN qruplar (adlar üçün). Siyahıda göstərilən deyil —
    siyahı yalnız əlavə olunmuş qruplardır (listed_groups)."""
    global _groups_dirty
    out = {}
    try:
        out.update({int(k): v for k, v in json.loads(ub.db.kv_get("logger", "group_titles", "{}")).items()})
    except Exception as _e:
        logger.debug("udulan xəta [userbot/plugins/logger.py:126]: %r", _e)
    out.update(_group_titles)
    for c in _audit_chats(ub):
        out.setdefault(c, str(c))
    if _groups_dirty:
        _groups_dirty = False
        with contextlib.suppress(Exception):
            ub.db.kv_set("logger", "group_titles",
                         json.dumps({str(k): v for k, v in out.items()}, ensure_ascii=False)[:60000])
    return out


def listed_groups(ub) -> dict:
    """Siyahıda görünən qruplar: yalnız ➕ ilə əlavə olunanlar (audit açarı təyin olunmuş) — mesaj gəlməsi siyahıya salmır."""
    names = known_groups(ub)
    ids = set(_audit_chats(ub))
    for k in ub.db.kv_all("logger"):
        m = re.fullmatch(r"g(-?\d+):on_au", k)
        if m:
            ids.add(int(m.group(1)))
    return {c: names.get(c) or str(c) for c in ids}


def discovered_groups(ub) -> dict:
    """Görülmüş, amma siyahıya əlavə olunmamış qruplar (bir toxunuşla əlavə etmək üçün)."""
    listed = listed_groups(ub)
    return {c: n for c, n in known_groups(ub).items() if c not in listed and c < 0}


class U:
    """Audit kartında kliklənə bilən istifadəçi: ad → tg://user?id=… linki, @username → toxunulan mention."""
    __slots__ = ("name", "uid", "username")

    def __init__(self, name, uid=None, username=None):
        self.name, self.uid, self.username = name, uid, username

    def html(self) -> str:
        name = escape(str(self.name or "Bilinmir")[:40])
        out = f'<a href="tg://user?id={int(self.uid)}">{name}</a>' if self.uid else name
        uname = str(self.username or "").lstrip("@")
        if uname:
            out += f" @{escape(uname)}"
        elif self.uid:
            out += f" <code>{int(self.uid)}</code>"
        return out


def _plain_v(v) -> str:
    if isinstance(v, U):
        return str(v.name or "Bilinmir") + (f" @{str(v.username).lstrip('@')}" if v.username else "")
    if isinstance(v, (list, tuple)):
        return " ".join(_plain_v(x) for x in v)
    return str(v)


def acard(rows, icon: str = "") -> str:
    """Audit kartı: köhnə hizalanmış monospace blok (artıq başlıq / xətt / footer olmadan).
    İstifadəçi sətirləri (U) Telegram <pre> içində link işlətmədiyi üçün blokun altında ayrıca verilir —
    Ad profilə, @username isə istifadəçiyə toxunulur."""
    import textwrap
    title, block, users = "", [], []
    for k, v in rows:
        if isinstance(k, str) and k.startswith("# "):
            title = k[2:]
        elif isinstance(v, U) and k:
            users.append(f"👤 {escape(str(k))}: {v.html()}")
        elif k or v:
            block.append((str(k), _plain_v(v)))
    kw = min(max((len(k) for k, _ in block), default=0), 10)
    out = [title] if title else []
    for k, v in block:
        lines = textwrap.wrap(v, 34, break_long_words=True) or ["—"]
        if not k:                                  # açarsız sətir (məs. purge siyahısı) — girintisiz
            out += [x for x in lines[:2]]
            continue
        out.append(f"{k.ljust(kw)}  {lines[0]}".rstrip())
        out += [" " * (kw + 2) + x for x in lines[1:6]]
    from common.menu import wrap_ui_text
    text = wrap_ui_text(escape(chr(10).join(out)))                    # 📋 Görünüş üslubuna uyğun (kod bloku / sitat)
    return text + ("\n" + "\n".join(users) if users else "")


def cache_message(event):
    """Gələn və gedən mesajları silinmə və redaktə yoxlanışı üçün yaddaşda saxlayır."""
    if not event.chat_id or not event.message:
        return
    msg = event.message
    cid, mid = event.chat_id, msg.id
    
    sender = getattr(event, "sender", None)
    uname = getattr(sender, "username", None) or ""
    sname = get_user_name(sender) if sender else ""
    chat_title = getattr(event.chat, "title", None) or getattr(event.chat, "first_name", None) or str(cid)

    data = {
        "chat_id": cid,
        "chat_title": chat_title,
        "msg_id": mid,
        "sender_id": msg.sender_id or getattr(sender, "id", None) or 0,
        "sender_name": sname,
        "username": uname,
        "text": msg.raw_text or "",
        "media_label": get_media_label(msg),
        "time": msg.date.timestamp() if msg.date else time.time(),
    }

    _remember_group(cid, chat_title)
    _msg_cache[(cid, mid)] = data
    if not str(cid).startswith("-100"):
        _mid_index[mid] = cid
    if len(_msg_cache) > MAX_MSG_CACHE:
        (old_cid, old_mid), _ = _msg_cache.popitem(last=False)
        if _mid_index.get(old_mid) == old_cid:
            _mid_index.pop(old_mid, None)




STREAM_INFO = {
    "vc":  {"name": "Səs (MP3)",        "icon": "🎙", "parent": None},
    "au":  {"name": "Audit (Ümumi)",    "icon": "🛡", "parent": None},
    "del": {"name": "Silinən mesajlar", "icon": "🗑", "parent": "au"},
    "ed":  {"name": "Redaktə olunan",   "icon": "✏️", "parent": "au"},
    "ban": {"name": "Ban və cəzalar",   "icon": "⛔", "parent": "au"},
    "mbr": {"name": "Üzvlər (giriş/çıxış)", "icon": "🚪", "parent": "au"},
    "adm": {"name": "Admin və qrup",    "icon": "👮", "parent": "au"},
    "tr":  {"name": "Transkript TXT",   "icon": "📝", "parent": "vc"},
    "spl": {"name": "Ayrı Treklər ZIP", "icon": "🗣", "parent": "vc"},
    "report": {"name": "HTML Hesabat",  "icon": "📄", "parent": "au"},
    # ── ayrı-ayrı hadisələr (hər biri öz qrupunun altında: hədəf və açar oradan miras qalır) ──
    "del1":  {"name": "Tək mesaj silindi",     "icon": "🗑", "parent": "del"},
    "purge": {"name": "Kütləvi silinmə",       "icon": "🧹", "parent": "del"},
    "kick":  {"name": "Ban / qovulma",         "icon": "⛔", "parent": "ban"},
    "mute":  {"name": "Məhdudlaşdırma",        "icon": "🔇", "parent": "ban"},
    "unban": {"name": "Ban götürüldü",         "icon": "🔓", "parent": "ban"},
    "join":  {"name": "Qoşuldu / əlavə edildi", "icon": "📥", "parent": "mbr"},
    "left":  {"name": "Çıxdı",                 "icon": "📤", "parent": "mbr"},
    "promo": {"name": "Admin hüquqları",       "icon": "👮", "parent": "adm"},
    "pin":   {"name": "Sancaq",                "icon": "📌", "parent": "adm"},
    "title": {"name": "Qrup adı",              "icon": "🏷", "parent": "adm"},
    "photo": {"name": "Qrup şəkli",            "icon": "🖼", "parent": "adm"},
}
AUDIT_GROUPS = ("del", "ed", "ban", "mbr", "adm")
GEVENTS_SET = frozenset(("del1", "purge", "ed", "kick", "mute", "unban", "join", "left", "promo", "pin", "title", "photo"))


def audit_events(group: str) -> list:
    """Qrupun ayrı hadisələri (ed — tək hadisədir, alt bölməsi yoxdur)."""
    return [k for k, v in STREAM_INFO.items() if v["parent"] == group and k not in ("tr", "spl", "report")]


def stream_chain(stream: str) -> list:
    """purge → [purge, del, au] — hədəf və açar bu sıra ilə axtarılır."""
    out, s = [], stream
    while s and s not in out:
        out.append(s)
        s = STREAM_INFO.get(s, {}).get("parent")
    return out

_STREAM_NORM = {
    "vc": "vc", "audio": "vc", "ses": "vc", "səs": "vc",
    "au": "au", "audit": "au", "casus": "au", "spy": "au",
    "del": "del", "delete": "del", "deleted": "del", "silinen": "del", "silinən": "del",
    "ed": "ed", "edit": "ed", "edited": "ed", "redakte": "ed", "redaktə": "ed",
    "ban": "ban", "bans": "ban", "qovulma": "ban",
    "mbr": "mbr", "member": "mbr", "members": "mbr", "uzv": "mbr", "üzv": "mbr", "uzvler": "mbr", "üzvlər": "mbr", "joins": "mbr",
    "adm": "adm", "admin": "adm", "actions": "adm", "emeller": "adm", "əməllər": "adm", "hadiseler": "adm", "hadisələr": "adm",
    "tr": "tr", "transkript": "tr", "transcript": "tr", "txt": "tr",
    "spl": "spl", "split": "spl", "trek": "spl", "treklər": "spl", "treki": "spl",
    "report": "report", "hesabat": "report", "html": "report",
    **{k: k for k in ("del1", "purge", "kick", "mute", "unban", "join", "left", "promo", "pin", "title", "photo")},
    "all": "all", "her": "all", "hər": "all", "hamisi": "all", "hamısı": "all",
}


def _stream_db_prefix(stream: str) -> str:
    s = _STREAM_NORM.get((stream or "").lower(), (stream or "").lower())
    if s in ("au", "audit"):
        return "audit"
    return s or "target"


def _all_target_keys() -> list:
    keys = ["target_chat", "target_topic"]
    for s in STREAM_INFO:
        pref = _stream_db_prefix(s)
        keys.append(f"{pref}_target_chat")
        keys.append(f"{pref}_target_topic")
    return list(dict.fromkeys(keys))


def has_explicit_target(ub, stream: str) -> bool:
    s = _STREAM_NORM.get((stream or "").lower(), (stream or "").lower())
    pref = _stream_db_prefix(s)
    return bool(ub.db.kv_get("logger", f"{pref}_target_chat"))


def reset_stream_target(ub, stream: str):
    s = _STREAM_NORM.get((stream or "").lower(), (stream or "").lower())
    pref = _stream_db_prefix(s)
    ub.db.kv_del("logger", f"{pref}_target_chat")
    ub.db.kv_del("logger", f"{pref}_target_topic")


def _gk(chat_id, key: str) -> str:
    """Qrup üzrə ayarın açarı: g<chat_id>:<açar> (ns «logger»)."""
    return f"g{int(chat_id)}:{key}"


def has_group_target(ub, chat_id, stream: str) -> bool:
    return bool(ub.db.kv_get("logger", _gk(chat_id, f"{_stream_db_prefix(stream)}_target_chat")))


def set_group_target(ub, chat_id, stream: str, chat, topic=None, title: str = ""):
    """Yalnız bu qrupun bu axını (hadisəsi) üçün hədəf."""
    pref = _stream_db_prefix(stream)
    ub.db.kv_set("logger", _gk(chat_id, f"{pref}_target_chat"), str(chat))
    if topic and str(topic).isdigit():
        ub.db.kv_set("logger", _gk(chat_id, f"{pref}_target_topic"), str(topic))
    else:
        ub.db.kv_del("logger", _gk(chat_id, f"{pref}_target_topic"))
    remember_target(ub, chat, topic if topic and str(topic).isdigit() else None, title)


def reset_group_stream(ub, chat_id, stream: str):
    """Qrupun bu axın üçün həm hədəfini, həm açarını sil → ümumi ayarlara qayıdır."""
    pref = _stream_db_prefix(stream)
    for k in (f"{pref}_target_chat", f"{pref}_target_topic", f"on_{stream}"):
        ub.db.kv_del("logger", _gk(chat_id, k))


def daily_on(ub, chat_id) -> bool:
    """Bu qrup üçün 23:59 avtomatik HTML hesabat (defolt: açıq — yalnız auditi açıq qruplarda göndərilir)."""
    return ub.db.kv_get("logger", _gk(chat_id, "daily"), "1") != "0"


def set_daily(ub, chat_id, on: bool):
    ub.db.kv_set("logger", _gk(chat_id, "daily"), "1" if on else "0")


def daily_groups(ub) -> dict:
    """{chat_id: ad} — 23:59 hesabatı gedəcək qruplar (audit açıq + hesabat açıq)."""
    return {c: n for c, n in listed_groups(ub).items() if is_audit_active_for(ub, c) and daily_on(ub, c)}


def group_settings(ub, chat_id) -> dict:
    """Qrupun ayarları: {açar_hissəsi: dəyər} (audit açarı on_au daxil DEYİL — o qrupun öz vəziyyətidir)."""
    pref = f"g{int(chat_id)}:"
    return {k[len(pref):]: v for k, v in ub.db.kv_all("logger").items()
            if k.startswith(pref) and k[len(pref):] != "on_au"}


def copy_group_settings(ub, src, dst) -> int:
    """src qrupunun hadisə açarlarını, hədəflərini, izləyən hesabını və hesabat ayarını dst qrupuna kopyalayır
    (dst-nin əvvəlki həmin ayarları silinir; auditin açıq / bağlı vəziyyəti dəyişmir). Kopyalanan ayar sayı."""
    src, dst = int(src), int(dst)
    if src == dst:
        return 0
    for suffix in group_settings(ub, dst):
        ub.db.kv_del("logger", _gk(dst, suffix))
    data = group_settings(ub, src)
    for suffix, value in data.items():
        ub.db.kv_set("logger", _gk(dst, suffix), value)
    return len(data)


def copy_to_all(ub, src) -> int:
    """src qrupunun ayarlarını siyahıdakı bütün digər qruplara tətbiq edir. Təsirlənən qrup sayı."""
    n = 0
    for cid in list(listed_groups(ub)):
        if int(cid) != int(src):
            copy_group_settings(ub, src, cid)
            n += 1
    return n


def forget_all(ub, only_inactive: bool = True) -> int:
    """Siyahını təmizlə: yalnız izlənməyənləri (⚪) və ya hamısını. Silinən sayı."""
    n = 0
    for c in list(listed_groups(ub)):
        if only_inactive and is_audit_active_for(ub, c):
            continue
        forget_group(ub, c)
        n += 1
    return n


def ub_topic(ub, stream: str, chat_id=None):
    pref = _stream_db_prefix(stream)
    key = f"{pref}_target_topic" if chat_id is None else _gk(chat_id, f"{pref}_target_topic")
    return ub.db.kv_get("logger", key)


def clear_topic(ub, stream: str, chat_id=None):
    """Hədəf çat qalır, yalnız topic silinir (General-a getsin)."""
    pref = _stream_db_prefix(stream)
    key = f"{pref}_target_topic" if chat_id is None else _gk(chat_id, f"{pref}_target_topic")
    ub.db.kv_del("logger", key)


def forget_group(ub, chat_id):
    """Qrupu siyahıdan sil: bütün qrup ayarları, ad və «seçilmiş qruplar» qeydi təmizlənir."""
    for st in STREAM_INFO:
        reset_group_stream(ub, chat_id, st)
    ub.db.kv_del("logger", _gk(chat_id, "on_au"))
    ub.db.kv_del("logger", _gk(chat_id, "source"))
    ub.db.kv_del("logger", _gk(chat_id, "daily"))
    _group_titles.pop(int(chat_id), None)
    with contextlib.suppress(Exception):
        names = json.loads(ub.db.kv_get("logger", "group_titles", "{}"))
        names.pop(str(int(chat_id)), None)
        ub.db.kv_set("logger", "group_titles", json.dumps(names, ensure_ascii=False))
    with contextlib.suppress(Exception):
        ub.db.kv_set("logger", "audit_chats",
                     json.dumps([c for c in _audit_chats(ub) if c != int(chat_id)]))


def ub_group_flag(ub, chat_id, stream: str) -> bool:
    """Qrupun bu axın üçün ayrıca açar (on_<axın>) təyin edib-etmədiyi."""
    return ub.db.kv_get("logger", _gk(chat_id, f"on_{stream}")) is not None


def group_has_overrides(ub, chat_id) -> bool:
    return any(ub.db.kv_get("logger", _gk(chat_id, f"on_{s}")) is not None for s in ("au",) + TOGGLE_STREAMS) or \
        any(has_group_target(ub, chat_id, s) for s in STREAM_INFO)


def get_stream_target(ub, stream: str = "vc", chat_id=None) -> tuple[str | None, str | None, str]:
    """Hər axın üçün hədəfi qaytarır (qrupun öz hədəfi → fərdi → valideyn → ümumi fallback).
    chat_id verilsə, əvvəl həmin qrup üçün ayrıca təyin olunmuş hədəflər axtarılır."""
    s = _STREAM_NORM.get((stream or "").lower(), (stream or "").lower())

    tc = tt = None
    inherited = False
    grouped = False
    if chat_id is not None:
        for i, link in enumerate(stream_chain(s) or [s]):
            p = _stream_db_prefix(link)
            tc = ub.db.kv_get("logger", _gk(chat_id, f"{p}_target_chat"))
            if tc:
                tt = ub.db.kv_get("logger", _gk(chat_id, f"{p}_target_topic"))
                inherited = i > 0
                grouped = True
                break
    if not tc:
        for i, link in enumerate(stream_chain(s) or [s]):    # fərdi → valideyn → onun valideyni ...
            p = _stream_db_prefix(link)
            tc = ub.db.kv_get("logger", f"{p}_target_chat")
            if tc:
                tt = ub.db.kv_get("logger", f"{p}_target_topic")
                inherited = i > 0
                break

    # 2. Hələ də yoxdursa, ümumi hədəfə fallback
    if not tc:
        tc = ub.db.kv_get("logger", "target_chat")
        tt = ub.db.kv_get("logger", "target_topic")
        if tc:
            inherited = True

    if not tc or tc == "me":
        label = "Saved Messages (Özünə)"
        if inherited and tc:
            label += " [miras]"
        return None, None, label

    label = f"{tc}"
    if tt:
        label += f" · Topic: {tt}"
    if grouped:
        label += " [qrup]"
    elif inherited:
        label += " [miras]"
    return tc, tt, label


# Ayrıca söndürülə bilən audit axınları: qruplar (əsas açar) və onların hadisələri
TOGGLE_STREAMS = AUDIT_GROUPS + tuple(e for g in AUDIT_GROUPS for e in audit_events(g))


def own_on(ub, stream: str, chat_id=None) -> bool:
    """Axının öz açarı (qrupun açarına baxmadan). chat_id verilsə, qrupun öz üstünlüyü (g<id>:on_<axın>) önəmlidir."""
    if stream not in TOGGLE_STREAMS:
        return True
    if chat_id is not None:
        v = ub.db.kv_get("logger", _gk(chat_id, f"on_{stream}"))
        if v in ("0", "1"):
            return v == "1"
    return ub.db.kv_get("logger", f"on_{stream}", "1") == "1"


def stream_on(ub, stream: str, chat_id=None) -> bool:
    """Həqiqətən göndərilirmi: özü və bütün valideyn qrupları açıqdır."""
    s = _STREAM_NORM.get((stream or "").lower(), stream)
    return all(own_on(ub, x, chat_id) for x in stream_chain(s))


def set_group_event_on(ub, chat_id, stream: str, on: bool):
    """Qrup üçün hadisəni aç / bağla. Açanda bağlı valideyn bölməsi də (yalnız bu qrup üçün) açılır."""
    ub.db.kv_set("logger", _gk(chat_id, f"on_{stream}"), "1" if on else "0")
    if on:
        for x in stream_chain(stream)[1:]:
            if x in TOGGLE_STREAMS and not own_on(ub, x, chat_id):
                ub.db.kv_set("logger", _gk(chat_id, f"on_{x}"), "1")


def set_stream_on(ub, stream: str, on: bool):
    ub.db.kv_set("logger", f"on_{stream}", "1" if on else "0")


def toggle_stream(ub, stream: str) -> bool:
    on = not own_on(ub, stream)
    set_stream_on(ub, stream, on)
    return on


RECENT_TARGETS = 6


def recent_targets(ub) -> list:
    """Son istifadə olunan hədəflər: [[chat, topic, ad], ...] — panellərdə bir toxunuşla seçmək üçün."""
    try:
        items = json.loads(ub.db.kv_get("logger", "recent_targets", "[]"))
        return [x for x in items if isinstance(x, list) and len(x) == 3][:RECENT_TARGETS]
    except Exception:
        return []


def remember_target(ub, chat, topic=None, title: str = ""):
    if not chat or str(chat) == "me":
        return
    chat, topic = str(chat), (str(topic) if topic else None)
    old = [x for x in recent_targets(ub) if not (x[0] == chat and x[1] == topic)]
    prev = next((x[2] for x in recent_targets(ub) if x[0] == chat and x[2] != chat), "")
    ub.db.kv_set("logger", "recent_targets",
                 json.dumps([[chat, topic, title or prev or chat]] + old, ensure_ascii=False)[:4000])


# ⏳ FloodWait: Telegram "A wait of N seconds" deyəndə eyni hədəfə göndərmələr SIRA ilə gözləyir (bir hədəfə bir kilid)
# və gözləmədən sonra təkrar cəhd olunur — əvvəl hər mesaj dərhal ikinci dəfə vurub flood-u uzadırdı və
# "Saved Messages-ə yönləndirmə" ilə audit məzmununu yanlış çata atırdı.
FLOOD_MAX_WAIT = 600
_FLOOD_UNTIL: dict = {}
_SEND_LOCKS: dict = {}


async def send_to_target(ub, client, stream: str = "vc", text=None, file=None, caption=None, attributes=None,
                         silent: bool = False, chat_id=None):
    """Loq və qeydləri təyin olunmuş hədəf kanal, qrup və ya topic-ə göndərir.
    Söndürülmüş audit axını (stream_on) — heç nə göndərilmir, None. silent — bildirişsiz.
    FloodWait olarsa hədəf üzrə gözləyir (FLOOD_MAX_WAIT-dən uzundursa mesaj atılır)."""
    if not stream_on(ub, stream, chat_id):
        return None
    tc, _tt, _ = get_stream_target(ub, stream, chat_id)
    key = str(tc or "me")
    lock = _SEND_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        for attempt in (1, 2):
            wait = _FLOOD_UNTIL.get(key, 0) - time.time()
            if wait > FLOOD_MAX_WAIT:
                logger.warning(f"⏳ Hədəf ({key}) {int(wait)} san. flood-dadır — mesaj atıldı")
                return None
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await _send_to_target(ub, client, stream, text, file, caption, attributes, silent, chat_id)
            except FloodWaitError as e:
                _FLOOD_UNTIL[key] = time.time() + e.seconds + 1
                logger.warning(f"⏳ FloodWait ({key}): {e.seconds} san." + (" — gözlənilir və təkrar göndərilir" if attempt == 1 else " — mesaj atıldı"))
        return None


async def _send_to_target(ub, client, stream, text, file, caption, attributes, silent, chat_id):
    tc, tt, _ = get_stream_target(ub, stream, chat_id)
    dest = tc if tc else "me"

    try:
        if str(dest).lstrip("-").isdigit():
            entity = await client.get_entity(int(dest))
        else:
            entity = await client.get_entity(dest)
    except Exception as e:
        logger.warning(f"⚠️ Hədəf entity tapılmadı ({dest}): {e} — Saved Messages-ə göndərilir")
        entity = "me"

    kwargs = {}
    if tt and str(tt).isdigit():
        kwargs["reply_to"] = int(tt)

    try:
        if file:
            return await client.send_file(
                entity,
                file,
                caption=caption or text,
                attributes=attributes,
                parse_mode="html",
                silent=silent,
                **kwargs
            )
        elif text:
            return await client.send_message(
                entity,
                text,
                parse_mode="html",
                silent=silent,
                link_preview=False,
                **kwargs
            )
    except FloodWaitError:
        raise                                       # send_to_target gözləyib təkrar edəcək
    except Exception as e:
        logger.warning(f"⚠️ Hədəfə göndərmə xətası ({dest}, topic={tt}): {e} — reply_to olmadan təkrar cəhd edilir")
        try:
            if file:
                return await client.send_file(
                    entity,
                    file,
                    caption=caption or text,
                    attributes=attributes,
                    parse_mode="html",
                    silent=silent,
                )
            elif text:
                return await client.send_message(
                    entity,
                    text,
                    parse_mode="html",
                    silent=silent,
                    link_preview=False,
                )
        except FloodWaitError:
            raise
        except Exception as ex2:
            logger.error(f"❌ Hədəfə göndərmə tam xətası ({dest}): {ex2}")
            if entity != "me":
                try:
                    alert_txt = f"⚠️ <i>Hədəfə ({dest}) göndərmək mümkün olmadı, audio buraya yönləndirildi.</i>\n\n"
                    if file:
                        return await client.send_file(
                            "me",
                            file,
                            caption=alert_txt + (caption or text or ""),
                            attributes=attributes,
                            parse_mode="html",
                            silent=silent,
                        )
                    elif text:
                        return await client.send_message(
                            "me",
                            alert_txt + text,
                            parse_mode="html",
                            silent=silent,
                            link_preview=False,
                        )
                except Exception as _e:
                    logger.debug("udulan xəta [userbot/plugins/logger.py:432]: %r", _e)
            return None


def is_audit_active_for(ub, chat_id: int) -> bool:
    """Audit yalnız bota əlavə edilmiş (🏘 Qrup üzrə ayarlar) qruplarda işləyir — defolt hamısı SÖNÜLÜDÜR."""
    if ub.db.kv_get("logger", "audit_enabled", "1") != "1":
        return False
    own = ub.db.kv_get("logger", _gk(chat_id, "on_au"))      # qrup üzrə açıq / bağlı
    if own in ("0", "1"):
        return own == "1"
    return chat_id in _audit_chats(ub)                       # köhnə «seçilmiş çatlar» siyahısı (.rec audit here)


def group_source(ub, chat_id) -> str:
    """Bu qrupu kim izləyir: assistant | userbot (qrupun öz seçimi, yoxdursa ümumi default)."""
    v = ub.db.kv_get("logger", _gk(chat_id, "source")) if chat_id is not None else None
    return v if v in ("assistant", "userbot") else audit_source(ub)


def set_group_source(ub, chat_id, src: str):
    ub.db.kv_set("logger", _gk(chat_id, "source"), "userbot" if src == "userbot" else "assistant")


def add_group(ub, chat_id, title: str = ""):
    """Qrupu auditə əlavə et (aktiv) və siyahıda adını yadda saxla."""
    cid = int(chat_id)
    ub.db.kv_set("logger", _gk(cid, "on_au"), "1")
    if title:
        _group_titles[cid] = title
        with contextlib.suppress(Exception):
            names = json.loads(ub.db.kv_get("logger", "group_titles", "{}"))
            names[str(cid)] = title
            ub.db.kv_set("logger", "group_titles", json.dumps(names, ensure_ascii=False)[:60000])


# ───────────────────────── 🎙 Səs Sessiyası İdarəetmə Funksiyaları ─────────────────────────



# ───────────────────────── 💻 Panel: bir görünüş, iki adapter ─────────────────────────
# Kart (.rec) və .menu → 🎙 bölməsi EYNİ ekranları və EYNİ əməliyyatları işlədir:
#   view(ub, ekran, cid, qeyd)            → (mətn, [[(düymə, action), ...], ...])
#   do_action(ub, client, action, cid)    → (növbəti ekran, qeyd)
# Ekranlar: main · tgt (hədəflər) · jr:del|ed|ban (jurnal) · help
# Nəticə / xəta düymənin altında deyil, kartın özündə (ℹ️ qeyd) göstərilir: kart düyməsinə host
# artıq cavab verib, ikinci cb.answer() Telegram tərəfindən rədd olunur (bildiriş görünmürdü).
# Kartın 🔄 / ❌ düymələri Out-un özündədir — burada təkrarlanmır.

INPUT_TTL = 300                       # ✍️ hədəf yazılmasını bu qədər gözlə, sonra unut (mesajları "udmasın")
JOURNAL_LIMIT = 10
ON_WORDS = ("on", "1", "aktiv", "aç", "ac", "yes")
OFF_WORDS = ("off", "0", "deaktiv", "bağla", "bagla", "no")
_STREAM_KEY = {s: _stream_db_prefix(s) for s in STREAM_INFO}
_TARGET_KEYS = _all_target_keys()
JOURNAL = {
    "del": ("🗑", "Silinən", _deleted_history),
    "ed":  ("✏️", "Redaktə", _edited_history),
    "ban": ("⛔", "Ban", _bans_history),
    "mbr": ("🚪", "Üzvlər", _members_history),
    "adm": ("👮", "Əməllər", _admin_history),
}
INPUT_HINT = ("✍️ Bu çata hədəfi yaz ({min} dəq.):\n"
              "@kanal · -100… [topic] · t.me/c/…/42 · me")



def _on(ub, key: str, default: str = "0") -> bool:
    return ub.db.kv_get("logger", key, default) == "1"


def _set_flag(ub, key: str, arg: str = "", default: str = "0") -> bool:
    """on / off / boş (= tərsinə çevir) → yeni vəziyyət."""
    arg = (arg or "").lower()
    val = True if arg in ON_WORDS else False if arg in OFF_WORDS else not _on(ub, key, default)
    ub.db.kv_set("logger", key, "1" if val else "0")
    return val


def _mark(on: bool) -> str:
    return "✅" if on else "❌"


def _audit_chats(ub) -> list:
    try:
        return [int(x) for x in json.loads(ub.db.kv_get("logger", "audit_chats", "[]"))]
    except Exception:
        return []


def set_target(ub, stream: str, chat, topic=None, title: str = ""):
    """stream: all | vc | au | del | ed | ban | mbr | adm | tr | spl."""
    s = _STREAM_NORM.get((stream or "").lower(), (stream or "").lower())
    if s == "all":
        for k in _all_target_keys():
            ub.db.kv_del("logger", k)
        prefix = ""
    else:
        prefix = f"{_stream_db_prefix(s)}_"
    ub.db.kv_set("logger", f"{prefix}target_chat", str(chat))
    if topic and str(topic).isdigit():
        ub.db.kv_set("logger", f"{prefix}target_topic", str(topic))
    else:
        ub.db.kv_del("logger", f"{prefix}target_topic")
    remember_target(ub, chat, topic if topic and str(topic).isdigit() else None, title)


async def resolve_target(client, raw: str, here: int):
    """Yazılmış hədəf → (chat, topic, xəta). chat: "me" | "-100..." | istifadəçi ID."""
    target, topic, _ = parse_target_string(raw or "")
    if not target:
        return None, None, "Boş dəyər"
    if target == "me":
        return "me", None, ""
    if target.lower() in ("here", "bura"):
        return str(here), topic, ""
    try:
        ent = await client.get_entity(int(target) if target.lstrip("-").isdigit() else target)
        return str(utils.get_peer_id(ent)), topic, ""
    except Exception as e:
        if target.startswith("-100") and target[1:].isdigit():
            return target, topic, ""
        return None, None, f"Tapılmadı: {str(e)[:40]}"


async def _peer_arg(client, arg: str):
    """.rec start/stop <@qrup | -100...> → peer ID (kanal / qrup üçün mənfi)."""
    if not arg:
        return None
    try:
        ent = await client.get_entity(int(arg) if arg.lstrip("-").isdigit() else arg)
        return utils.get_peer_id(ent)              # ent.id müsbətdir → əvvəl "yalnız qrup" xətası verirdi
    except Exception:
        return None


# ── ekranlar ──

def _main_view(ub, cid: int, note: str):
    audit = _on(ub, "audit_enabled", "1")

    va = _va(ub)
    body = ["# ⏺ Yayım qeydi (🎙 asistan)"]
    sess, auto = None, False
    if va is None:
        body.append(("Asistan", "⚠️ yüklənməyib"))
    else:
        from bot.core import va_record
        if cid < 0:
            sess = va.rec.sessions.get(cid)
            auto = va_record.auto_on(cid)
            body.append(("Qeyd", f"🔴 {fmt_dur(sess.now())} · 🗣 {len(sess.voices)} · 🎵 {len(sess.tracks)}"
                         if sess else "⚪ getmir"))
            body.append(("Rejim", "⚡ avtomatik" if auto else "✋ manual"))
        else:
            n = len(va.rec.sessions)
            body.append(("Qeyd", f"🔴 {n} qrupda gedir" if n else "⚪ getmir"))
            body.append(("Avto", f"⚡ {len(va_record.auto_chats())} qrupda"))
        body.append(("Hədəf", get_stream_target(ub, "vc")[2][:28]))
    body += [
        "# 🛡 Audit (ayarlar botda: /menu → 📱 Userbot & Audit)",
        ("Vəziyyət", ("🟢 aktiv" if audit else "🔴 sönülü") + " · "
         + ("🛡 bu qrup izlənir" if cid < 0 and is_audit_active_for(ub, cid) else "bu qrup izlənmir" if cid < 0 else "qruplar botda")),
    ]
    if cid < 0:
        n = {k: len(store.get(cid, ())) for k, (_, _, store) in JOURNAL.items()}
        body.append(("Bu çat", f"{n['del']} sil · {n['ed']} red · {n['ban']} ban · {n['mbr']} üzv · {n['adm']} əməl"))
    if note:
        body += ["", f"ℹ️ {note}"]

    rows = []
    if va is not None and cid < 0:
        rows.append([("⏹ Saxla və göndər", "stop") if sess else ("⏺ Qeydə başla", "start"),
                     (f"⚡ Avto {_mark(auto)}", "auto")])
    if va is not None:
        rows.append([("⏺ Qeyd paneli (botda)", "vp")])
    rows.append([("📤 Səs hədəfləri", "tgt"), ("📜 Jurnal", "jr:del"), ("❓ Kömək", "help")])
    return ff(ub.title("rec"), body, footer=".rec help — bütün komandalar"), rows


def _tgt_btn(ub, stream: str, name: str):
    return (f"{STREAM_INFO[stream]['icon']} {name}" + ("" if stream_on(ub, stream) else " 🔴"), f"t:cfg:{stream}")


def _tgt_view(ub, cid: int, note: str):
    body = [
        "# 📤 Səs qeydləri hara göndərilsin",
        ("🎙 Səs (MP3)", get_stream_target(ub, "vc")[2][:28]),
        ("📝 Transkript", get_stream_target(ub, "tr")[2][:28]),
        ("🗣 Ayrı Treklər", get_stream_target(ub, "spl")[2][:28]),
        "",
        "Audit hədəfləri: botda /menu → 📱 Userbot & Audit",
    ]
    if note:
        body += ["", f"ℹ️ {note}"]
    rows = [
        [("🎙 Səs", "t:cfg:vc")],
        [("📝 Transkript", "t:cfg:tr"), ("🗣 Treklər", "t:cfg:spl")],
        [("♻️ Səs hədəflərini sıfırla", "t:reset")],
        [("⬅️ Əsas Menyu", "main")],
    ]
    return ff(ub.title("targets"), body, footer="Axını seçərək fərdi hədəf / topik təyin edin"), rows


def _tgt_stream_view(ub, cid: int, stream: str, note: str = ""):
    info = STREAM_INFO.get(stream, {"name": stream, "icon": "📤", "parent": None})
    _, _, desc = get_stream_target(ub, stream)
    is_exp = has_explicit_target(ub, stream)
    parent_key = info.get("parent")
    parent_info = STREAM_INFO.get(parent_key) if parent_key else None

    body = [
        f"# {info['icon']} {info['name']} — Hədəf",
        ("Cari Hədəf", desc[:30]),
        ("Status", "🎯 Fərdi təyin edilib" if is_exp else (f"🔗 Miras: {parent_info['name']}" if parent_info else "🌐 Ümumi standart")),
    ]
    if stream in TOGGLE_STREAMS:
        body.append(("Loq", "🟢 göndərilir" if stream_on(ub, stream) else
                     ("🔴 söndürülüb (jurnalda qalır)" if not own_on(ub, stream) else "🔴 qrupu söndürülüb")))
    events_ = audit_events(stream)
    if events_:
        body.append("# Hadisələr (hər biri ayrıca)")
        for e in events_:
            body.append((f"{STREAM_INFO[e]['icon']} {STREAM_INFO[e]['name'][:14]}",
                         ("🟢 " if own_on(ub, e) else "🔴 ") + get_stream_target(ub, e)[2][:20]))
    if note:
        body += ["", f"ℹ️ {note}"]

    here = cid < 0
    rows = []
    r1 = []
    if here:
        r1.append(("📍 Bu çat", f"t:{stream}:here"))
    r1.append(("💾 Saved", f"t:{stream}:me"))
    r1.append(("✍️ Yaz", f"t:{stream}:in"))
    rows.append(r1)

    if stream in TOGGLE_STREAMS:
        rows.append([(f"{'✅ Aktiv' if stream_on(ub, stream) else '❌ Söndürülüb'} — dəyiş", f"t:{stream}:tog")])
    if is_exp:
        rows.append([("🔗 Mirasa Qaytar", f"t:{stream}:reset")])
    for e in events_:                                 # hadisə: ✅/❌ açar + 🎯 öz hədəfi
        rows.append([(f"{'✅' if own_on(ub, e) else '❌'} {STREAM_INFO[e]['icon']} {STREAM_INFO[e]['name']}",
                      f"t:{e}:tog"), ("🎯", f"t:cfg:{e}")])
    parent = info.get("parent")
    back = [("⬅️ " + STREAM_INFO[parent]["name"], f"t:cfg:{parent}")] if parent in AUDIT_GROUPS else []
    rows.append(back + [("⬅️ Bütün Hədəflər", "tgt"), ("🏠 Əsas", "main")])
    return ff(ub.title("target_stream"), body, footer="@kanal · -100... · t.me/c/.../42 · topik dəstəklənir"), rows


def _journal_items(store, cid: int, limit: int) -> list:
    """Qrupda — həmin çat; Saved / .menu-da — bütün çatlar birlikdə (çat adı ilə)."""
    if cid < 0:
        items = [dict(x) for x in store.get(cid, ())]
    else:
        items = [dict(x) for dq in store.values() for x in dq]
    items.sort(key=lambda m: m.get("del_time") or m.get("edit_time") or m.get("time") or 0)
    return items[-limit:][::-1]


def _journal_view(ub, cid: int, tab: str, note: str, limit: int = JOURNAL_LIMIT):
    icon, name, store = JOURNAL[tab]
    items = _journal_items(store, cid, limit)
    where = "bu çat" if cid < 0 else "bütün çatlar"
    body = [f"# {icon} {name} — {where}"]
    if not items:
        body.append(("—", "hələ heç nə yoxdur (yalnız bot işləyəndən sonrakılar)"))
    for m in items:
        ts = m.get("del_time") or m.get("edit_time") or m.get("time") or time.time()
        when = time.strftime("%H:%M", time.localtime(ts))
        chat = "" if cid < 0 else f"[{(m.get('chat_title') or '?')[:10]}] "
        if tab == "del":
            who = m.get("sender_name") or "?"
            val = f"{chat}{who[:12]}: {m.get('text') or m.get('media_label') or 'media'}"
        elif tab == "ed":
            who = m.get("sender_name") or "?"
            val = f"{chat}{who[:12]}: {(m.get('old_text') or '—')[:25]} → {(m.get('new_text') or '—')[:25]}"
        elif tab == "ban":
            val = f"{chat}{(m.get('user_name') or '?')[:14]} ← {(m.get('admin_name') or 'admin')[:12]}"
        elif tab == "mbr":
            act = m.get("action") or "Üzv"
            who = (m.get("user_name") or "?")[:12]
            by = f" (by {m.get('admin_name')[:10]})" if m.get("admin_name") else ""
            val = f"{chat}{act}: {who}{by}"
        elif tab == "adm":
            act = m.get("action") or "Hadisə"
            who = (m.get("user_name") or m.get("admin_name") or "")[:12]
            val = f"{chat}{act}" + (f" ({who})" if who else "")
        body.append((when, val))
    if note:
        body += ["", f"ℹ️ {note}"]

    def tab_btn(k):
        i, n, st = JOURNAL[k]
        cnt = len(_journal_items(st, cid, 10 ** 6))
        return (f"{'• ' if k == tab else ''}{i} {n} {cnt}", f"jr:{k}")

    rows = [
        [tab_btn("del"), tab_btn("ed"), tab_btn("ban")],
        [tab_btn("mbr"), tab_btn("adm")],
        [("🧹 Təmizlə", f"jrc:{tab}"), ("⬅️ Geri", "main")],
    ]
    return ff(ub.title("journal"), body, footer=".deleted · .edited · .bans · .members · .actions [say]"), rows


def _help_view(ub, cid: int, note: str):
    body = [
        "# ⏺ Yayım qeydi (🎙 asistan)",
        (".rec", "panel"),
        ("start/stop", ".rec start · .rec stop [@qrup]"),
        ("auto", ".rec auto on|off — yayım başlayanda özü"),
        ("panel", "/vrec · .vcrec — tam ayarlar"),
        "# 📤 Hədəflər (Bütün axınlar)",
        ("target", ".rec target [axın] <hədəf> [topic]"),
        ("axınlar", "vc | au | del | ed | ban | mbr | adm | tr | spl | all"),
        ("hədəf", "@kanal · -100… · t.me/c/…/42 · here · me · reset"),
        ("aç/sön", ".rec target del|ed|ban|mbr|adm on|off"),
        "# 🛡 Audit & Jurnal",
        ("audit", ".rec audit on|off|all|here"),
        ("jurnal", ".deleted · .edited · .bans · .members · .actions [say]"),
        "",
        "Qrupda .rec start/stop yazılan kimi silinir,",
        "   nəticə botun ⏺ panelində görünür",
    ]
    return ff(ub.title("help"), body), [[("⬅️ Geri", "main")]]


def view(ub, screen: str, cid: int, note: str = "", limit: int = JOURNAL_LIMIT):
    if screen == "tgt":
        return _tgt_view(ub, cid, note)
    if screen.startswith("t:cfg:") and screen[6:] in STREAM_INFO:
        return _tgt_stream_view(ub, cid, screen[6:], note)
    if screen == "help":
        return _help_view(ub, cid, note)
    if screen.startswith("jr:") and screen[3:] in JOURNAL:
        return _journal_view(ub, cid, screen[3:], note, limit)
    return _main_view(ub, cid, note)


# ── əməliyyatlar ──

async def do_action(ub, client, action: str, cid: int, topic=None) -> tuple:
    """Düymə → (növbəti ekran, qeyd). ✍️ (…:in) burada deyil — adapter gözləməni özü qurur."""
    if action in ("main", "tgt", "help") or action.startswith("jr:") or action.startswith("t:cfg:"):
        return action, ""

    if action.startswith("jrc:"):
        tab = action[4:]
        if tab in JOURNAL:
            store = JOURNAL[tab][2]
            if cid < 0:
                store.pop(cid, None)
            else:
                store.clear()
            return f"jr:{tab}", "🧹 Təmizləndi"
        return "main", ""

    if action in ("start", "stop"):
        return "main", await assistant_rec(ub, cid, action)
    if action == "auto":                               # ⚡ ↔ ✋ (asistanın bu qrup üçün rejimi)
        from bot.core import va_record
        return "main", await assistant_rec(ub, cid, "manual" if va_record.auto_on(cid) else "auto")
    if action == "vp":                                 # asistanın tam paneli — botun şəxsi çatında
        fn = getattr(ub.context, "vrec_command", None)
        va = _va(ub)
        if cid < 0 and fn is not None:
            from bot.core import va_record
            await fn(cid, va_record.known_groups().get(cid) or str(cid), "", by="userbot")
            return "main", "⏺ Panel botda açıldı (👁 ayarına görə şəxsi çatda və ya qrupda)"
        if va is not None and getattr(ub.context, "bot", None) is not None:
            text, kb = va.rec.groups_view()
            await ub.context.bot.send_message(ub.creator_id, text, parse_mode="HTML", reply_markup=kb)
            return "main", "⏺ Qruplar paneli botun şəxsi çatına göndərildi"
        return "main", "⚠️ Asistan yüklənməyib"

    if action.startswith("t:"):
        parts = action.split(":")
        if action == "t:reset":
            for st in ("vc", "tr", "spl"):
                reset_stream_target(ub, st)
            return "tgt", "♻️ Səs hədəfləri sıfırlandı — Saved Messages-ə"

        if len(parts) >= 3:
            stream, what = parts[1], parts[2]
            info = STREAM_INFO.get(stream, {"name": stream, "icon": "📤"})
            label = f"{info['icon']} {info['name']}"
            if what == "me":
                set_target(ub, stream, "me")
                return f"t:cfg:{stream}", f"{label} → Saved Messages"
            if what == "here" and cid < 0:
                set_target(ub, stream, cid, topic)
                return f"t:cfg:{stream}", f"{label} → bu çat" + (f" · topic {topic}" if topic else "")
            if what == "tog" and stream in TOGGLE_STREAMS:
                on = toggle_stream(ub, stream)
                parent = STREAM_INFO[stream]["parent"]
                screen = f"t:cfg:{parent}" if parent in AUDIT_GROUPS else f"t:cfg:{stream}"
                return screen, (f"{label} loqu açıldı" if on
                                else f"{label} loqu söndürüldü — jurnalda yenə görünəcək")
            if what == "reset":
                reset_stream_target(ub, stream)
                _, _, desc = get_stream_target(ub, stream)
                return f"t:cfg:{stream}", f"🔗 {label} mirasa qaytarıldı → {desc}"
    return "main", ""


async def apply_input(ub, client, info: dict, raw: str, cid: int) -> str:
    """✍️ ilə yazılmış hədəfi tətbiq edir → qeyd."""
    chat, topic, err = await resolve_target(client, raw, cid)
    if err:
        return f"⚠️ {err}"
    stream = info["stream"]
    set_target(ub, stream, chat, topic)
    s_info = STREAM_INFO.get(stream, {"name": stream, "icon": "📤"})
    label = f"{s_info['icon']} {s_info['name']}"
    return f"{label} → {'Saved Messages' if chat == 'me' else chat}" + (f" · topic {topic}" if topic else "")


def _wait_input(cid: int, stream: str, done):
    _WAIT_INPUT[cid] = {"stream": stream, "done": done, "ts": time.time()}


# ── adapter 1: kart (.rec və köməkçi komandalar) ──

async def open_card(ub, event, cid: int, screen: str = "main", note: str = "", limit: int = JOURNAL_LIMIT):
    topic = extract_topic_id(getattr(event, "message", None))
    state = {"screen": screen}
    text, _ = view(ub, screen, cid, note, limit)
    out = await ub.out(event, text)

    async def render(n: str = ""):
        t, spec = view(ub, state["screen"], cid, n, limit)
        await out.update(t, rows=[[out.btn(label, act) for label, act in row] for row in spec])

    async def handle(o, cb, action):
        if action.startswith("t:") and action.endswith(":in"):
            stream = action.split(":")[1]
            state["screen"] = f"t:cfg:{stream}"

            async def done(n):
                state["screen"] = f"t:cfg:{stream}"
                await render(n)
            _wait_input(cid, stream, done)
            await render(INPUT_HINT.format(min=INPUT_TTL // 60))
            return
        _WAIT_INPUT.pop(cid, None)                    # başqa düymə — yazı gözləməsi ləğv
        state["screen"], n = await do_action(ub, o.client, action, cid, topic)
        await render(n)

    class ActionDict(dict):
        def __init__(self, handler):
            super().__init__()
            self._handler = handler
        def __contains__(self, key):
            return True
        def __getitem__(self, key):
            return self._handler(key)
        def get(self, key, default=None):
            return self._handler(key)

    out.actions = ActionDict(lambda act: lambda o, cb: handle(o, cb, act))
    out.refresh = lambda o: render()
    await render(note)
    return out


# ───────────────────────── 🧩 PLUGIN QEYDİYYATI ─────────────────────────

# ───────────────────────── 🎙 Auditi kim izləyir: asistan hesabı / userbot ─────────────────────────
# Audit dinləyiciləri (silinən, redaktə, üzv, ban, admin, qrup hadisələri) hər iki hesabın client-inə qoşula bilər.
# Hansının işləyəcəyini «audit_source» seçir: assistant (default) | userbot. Asistan işləmirsə userbot əvəz edir.
# Loqlar hədəfə həmişə əsas userbot client-i ilə göndərilir (hədəflər onun üçün seçilib); o yoxdursa izləyən hesabla.
_CUR_CLIENT = contextvars.ContextVar("audit_client", default=None)    # asistan client-i (userbot-da None)
AUDIT_HANDLERS: list = []        # [(funksiya, event_builder)] — register() doldurur
_AUDIT_UB = None


def audit_source(ub) -> str:
    return "userbot" if ub.db.kv_get("logger", "audit_source", "assistant") == "userbot" else "assistant"


def _assistant_slots(ub) -> list:
    va = getattr(getattr(ub, "context", None), "voice_assistant", None)
    return [sl for sl in getattr(va, "slots", []) if getattr(sl, "running", False) and getattr(sl, "client", None)
            and getattr(sl, "voice_enabled", False)]


def audit_here(ub, chat_id=None) -> bool:
    """Bu dinləyici (asistan / userbot) bu çatı izləməlidirmi?
    Auditə əlavə olunmamış çatlarda yalnız userbot işləyir (köhnə 📜 jurnal üçün; heç nə göndərilmir)."""
    from_assistant = _CUR_CLIENT.get() is not None
    if chat_id is None or not is_audit_active_for(ub, chat_id):
        return not from_assistant
    if group_source(ub, chat_id) == "assistant" and _assistant_slots(ub):
        return from_assistant
    return not from_assistant


def _ev_cid(ev):
    """Hadisənin çat ID-si (Raw UpdateChannelParticipant üçün də)."""
    cid = getattr(ev, "chat_id", None)
    if cid is None and getattr(ev, "channel_id", None):
        with contextlib.suppress(Exception):
            cid = utils.get_peer_id(types.PeerChannel(ev.channel_id))
    return cid


def cur_client(ub):
    return _CUR_CLIENT.get() or ub.client


def out_client(ub, fallback):
    """Loqları göndərən client: əsas userbot (qoşuludursa), yoxsa hadisəni görən hesab."""
    c = getattr(ub, "client", None)
    return c if c is not None and getattr(c, "is_connected", lambda: False)() else fallback


class _AuditOnlyHost:
    """Userbot yüklənməyəndə (python init.py bot) audit dinləyicilərini yenə də 🎙 asistan hesabına qoşmaq üçün
    yüngül «host». register() yalnız dekorator çağırır — onlar burada heç nə qeyd etmir (komandalar / panellər yoxdur)."""

    def __init__(self, context):
        from userbot.core.userbot_db import get_udb
        self.db = get_udb()
        self.context = context
        self.client = None                          # əsas userbot yoxdur — loqlar izləyən hesabla göndərilir
        self.creator_id = getattr(context, "creator_id", 0)
        self.state = {}

    def title(self, name):
        return name

    def __getattr__(self, name):                    # command / section / on_section / raw / add_help ...
        def _noop(*args, **kwargs):
            if len(args) == 1 and callable(args[0]) and not kwargs:
                return args[0]
            return lambda fn: fn
        return _noop


def ensure_audit_host(context) -> bool:
    """Audit dinləyiciləri hələ hazır deyilsə (userbot yoxdur / yüklənməyib) yüngül hostla hazırlayır."""
    if _AUDIT_UB is not None:
        return True
    try:
        register(_AuditOnlyHost(context))
        logger.info("🛡 Audit yalnız asistan rejimində hazırlandı (userbot yoxdur)")
        return _AUDIT_UB is not None
    except Exception as e:
        logger.warning(f"🛡 Audit hostu hazırlanmadı: {e}")
        return False


_MODULE_TOKEN = object()          # hər modul yüklənməsi (reload daxil) üçün unikal


def attach_audit(client, context=None):
    """Asistan slotunun client-inə audit dinləyicilərini qoşur (bot/voice/engine.py çağırır).
    Vəziyyət client-in üzərində saxlanır (client._dlm_audit): plugin reload olunanda köhnə modulun dinləyiciləri
    avtomatik götürülür, təkrar qoşulma olmur."""
    if _AUDIT_UB is None and context is not None:
        ensure_audit_host(context)
    if _AUDIT_UB is None:
        return False
    cur = getattr(client, "_dlm_audit", None)
    if cur is not None:
        if cur["token"] is _MODULE_TOKEN:
            return False                                  # bu modul artıq qoşub
        detach_audit(client)                              # reload-dan əvvəlki modulun köhnə dinləyiciləri

    def mk(fn):
        async def wrapper(event):
            tok = _CUR_CLIENT.set(client)
            try:
                await fn(event)
            finally:
                _CUR_CLIENT.reset(tok)
        return wrapper

    items = []
    for fn, builder in AUDIT_HANDLERS:
        w = mk(fn)
        client.add_event_handler(w, builder)
        items.append((w, builder))
    client._dlm_audit = {"token": _MODULE_TOKEN, "items": items}
    logger.info(f"🛡 Audit dinləyiciləri asistan hesabına qoşuldu ({len(items)})")
    return True


def detach_audit(client):
    cur = getattr(client, "_dlm_audit", None)
    if cur is None:
        return
    for w, builder in cur["items"]:
        with contextlib.suppress(Exception):
            client.remove_event_handler(w, builder)
    with contextlib.suppress(Exception):
        del client._dlm_audit


def refresh_assistants(ub):
    """Plugin (yenidən) yüklənəndə artıq işləyən asistan hesablarına yeni dinləyiciləri qoşur."""
    for sl in _assistant_slots(ub):
        with contextlib.suppress(Exception):
            attach_audit(sl.client)


def detach_all_assistants(ub):
    """Plugin götürülərkən (reload / silmə) bütün asistan hesablarından dinləyiciləri çıxarır."""
    va = getattr(getattr(ub, "context", None), "voice_assistant", None)
    for sl in getattr(va, "slots", []):
        cl = getattr(sl, "client", None)
        if cl is not None:
            detach_audit(cl)


def register(ub):

    # ── 1. ƏSAS KOMANDA: .rec (və ya .record / .logger) ──

    @ub.command(
        "rec",
        pattern=r"^\.(?:rec|record|səs|casus|audit|logger)(?:\s+(start|qoşul|stop|end|dayan|auto|avto|stealth|gizli|quality|bitrate|keyfiyyet|target|hedef|audit|casus|deleted|edited|bans|silinenler|redakteler|banlar|members|uzvler|üzvlər|actions|emeller|əməllər|hadiseler|hadisələr|status|vəziyyət|guide|beledci|bələdçi|help|kömək|komek))?(?:\s+(.+))?$",
        help=(
            "qrup auditi və yayım qeydi",
            "Qrup auditi (silinən, redaktə, ban, üzvlər, admin) və 🎙 asistanın yayım qeydinin idarəsi.\n\n"
            "• <code>.rec</code> — panel\n"
            "• <code>.rec start|stop [@qrup]</code> — ⏺ asistan qeydə başlasın / saxlasın\n"
            "• <code>.rec auto on|off</code> — ⚡ yayım başlayanda asistan özü qeyd etsin\n"
            "• <code>.rec target [axın] &lt;hədəf&gt; [topic]</code> — hədəf (@kanal · -100… · here · me · reset)\n"
            "• <code>.rec audit on|off|all|here</code> — audit\n"
            "• <code>.rec target del|ed|ban|mbr|adm on|off</code> — axını ayrıca aç / söndür\n"
            "• <code>.deleted</code> · <code>.edited</code> · <code>.bans</code> · <code>.members</code> · <code>.actions</code> [say] — jurnal"
        )
    )
    async def cmd_rec_hub(event):
        sub = (event.pattern_match.group(1) or "").lower()
        arg = (event.pattern_match.group(2) or "").strip()
        cid = event.chat_id

        if sub in ("guide", "beledci", "bələdçi", "help", "kömək", "komek"):
            await open_card(ub, event, cid, "help")
            return

        if sub in ("start", "qoşul", "stop", "end", "dayan"):
            # ⏺ qeydi 🎙 asistan edir (.vcrec / /vrec ilə eyni)
            act = "start" if sub in ("start", "qoşul") else "stop"
            fn = getattr(ub.context, "vrec_command", None)
            if cid < 0 and not arg and fn is not None:
                with contextlib.suppress(Exception):
                    await event.delete()                 # qrupda iz qalmasın — nəticə botun panelində
                chat = await event.get_chat()
                await fn(cid, getattr(chat, "title", None) or str(cid), act, by="userbot")
                return
            peer = await _peer_arg(event.client, arg) or cid   # .rec start @qrup — Saved-dən başqa qrup
            await open_card(ub, event, cid, "main", await assistant_rec(ub, peer, act))
            return

        if sub in ("auto", "avto"):
            a = arg.lower()
            act = "auto" if a in ON_WORDS else "manual" if a in OFF_WORDS else None
            if act is None:
                from bot.core import va_record
                act = "manual" if va_record.auto_on(cid) else "auto"
            await open_card(ub, event, cid, "main", await assistant_rec(ub, cid, act))
            return

        if sub in ("stealth", "gizli", "quality", "bitrate", "keyfiyyet"):
            await open_card(ub, event, cid, "main", "ℹ️ Bu ayar artıq yoxdur — yayımı 🎙 asistan qeyd edir: "
                                                     "keyfiyyət və digər ayarlar ⏺ Qeyd panelindədir (/vrec)")
            return

        if sub in ("target", "hedef"):
            await target_cmd(event, arg, cid)
            return

        if sub in ("audit", "casus"):
            a = arg.lower()
            if a in ("here", "cari"):
                if cid < 0:
                    add_group(ub, cid, _group_titles.get(cid, ""))
                    note = "🛡 Bu qrup auditə əlavə edildi (ayarlar: botda 🏘 Qrup üzrə ayarlar)"
                else:
                    note = "⚠️ Bu komanda qrupda yazılmalıdır"
            elif a in ("all", "bütün"):
                note = "ℹ️ Audit defolt sönülüdür — qrupları botda 🏘 Qrup üzrə ayarlar bölməsində əlavə et"
            else:
                on = _set_flag(ub, "audit_enabled", a, default="1")
                note = "🛡 Audit aktiv" if on else "🛡 Audit söndürüldü"
            await open_card(ub, event, cid, "main", note)
            return

        for tab, names in (
            ("del", ("del", "deleted", "silinenler")),
            ("ed", ("ed", "edited", "redakteler")),
            ("ban", ("ban", "bans", "banlar")),
            ("mbr", ("mbr", "member", "members", "uzvler", "üzvlər")),
            ("adm", ("adm", "admin", "actions", "emeller", "əməllər", "hadiseler", "hadisələr")),
        ):
            if sub in names:
                limit = max(1, min(int(arg), 30)) if arg.isdigit() else JOURNAL_LIMIT
                await open_card(ub, event, cid, f"jr:{tab}", limit=limit)
                return

        await open_card(ub, event, cid, "main")

    async def target_cmd(event, arg: str, cid: int):
        """.rec target / .logtarget [axın] <hədəf> [topic] — hədəfsiz: hədəflər ekranı."""
        parts = arg.split(maxsplit=1)
        stream, rest = "all", arg
        if parts:
            first = parts[0].lower()
            if first in _STREAM_NORM:
                stream = _STREAM_NORM[first]
                rest = parts[1] if len(parts) > 1 else ""

        if not rest.strip():
            if stream != "all" and stream in STREAM_INFO:
                await open_card(ub, event, cid, f"t:cfg:{stream}")
            else:
                await open_card(ub, event, cid, "tgt")
            return

        r_lower = rest.strip().lower()
        if stream in TOGGLE_STREAMS and r_lower in ON_WORDS + OFF_WORDS:
            on = r_lower in ON_WORDS
            ub.db.kv_set("logger", f"on_{stream}", "1" if on else "0")
            info = STREAM_INFO[stream]
            await open_card(ub, event, cid, f"t:cfg:{stream}",
                            f"{info['icon']} {info['name']} loqu " + ("açıldı" if on else "söndürüldü"))
            return
        if r_lower in ("reset", "sifirla", "sıfırla", "miras"):
            if stream == "all":
                for k in _all_target_keys():
                    ub.db.kv_del("logger", k)
                note = "♻️ Bütün axınların hədəfləri sıfırlandı → Saved Messages"
            else:
                reset_stream_target(ub, stream)
                info = STREAM_INFO.get(stream, {"name": stream})
                _, _, desc = get_stream_target(ub, stream)
                note = f"🔗 {info['name']} mirasa qaytarıldı → {desc}"
            await open_card(ub, event, cid, "tgt", note)
            return

        if r_lower in ("here", "bura") and cid > 0:
            await open_card(ub, event, cid, "tgt", "⚠️ 'here' yalnız qrup / kanalda — şəxsi çata log göndərilmir")
            return

        chat, topic, err = await resolve_target(event.client, rest, cid)
        if err:
            await open_card(ub, event, cid, "tgt", f"⚠️ {err}")
            return
        if r_lower in ("here", "bura") and not topic:
            topic = extract_topic_id(event.message)

        set_target(ub, stream, chat, topic)
        if stream == "all":
            label = "🌐 Bütün axınlar"
        else:
            info = STREAM_INFO.get(stream, {"name": stream, "icon": "📤"})
            label = f"{info['icon']} {info['name']}"
        dest = "Saved Messages" if chat == "me" else chat
        screen = f"t:cfg:{stream}" if stream in STREAM_INFO else "tgt"
        await open_card(ub, event, cid, screen, f"{label} → {dest}" + (f" · topic {topic}" if topic else ""))

    # ── 2. HƏDƏF (.logtarget) ──

    @ub.command(
        "logtarget",
        pattern=r"^\.(?:logtarget|vclog)(?:\s+(.+))?$",
        help=(
            "loq və qeyd hədəfləri",
            "• <code>.logtarget</code> — hədəflər ekranı\n"
            "• <code>.logtarget [axın] &lt;hədəf&gt; [topic]</code> — fərdi axın hədəfi\n"
            "• <code>.logtarget &lt;hədəf&gt;</code> — bütün axınlar üçün\n"
            "• Axınlar: vc · au · del · ed · ban · mbr · adm · tr · spl · all\n"
            "• Hədəf: @kanal · -100… · t.me/c/…/42 · here · me · reset"
        )
    )
    async def cmd_logtarget(event):
        await target_cmd(event, (event.pattern_match.group(1) or "").strip(), event.chat_id)

    # ── 3. JURNAL: .deleted · .edited · .bans · .members · .actions [say] ──

    def journal_cmd(name, tab, aliases, short):
        @ub.command(name, pattern=rf"^\.(?:{aliases})(?:\s+(\d+))?$", help=(short, f"{short} — jurnal kartı (sekmələrlə)."))
        async def _(event):
            limit = max(1, min(int(event.pattern_match.group(1) or JOURNAL_LIMIT), 30))
            await open_card(ub, event, event.chat_id, f"jr:{tab}", limit=limit)

    journal_cmd("deleted", "del", "deleted|silinenler", "son silinən mesajlar")
    journal_cmd("edited", "ed", "edited|redakteler", "son redaktə olunan mesajlar")
    journal_cmd("bans", "ban", "bans|banlar", "son ban / qovulmalar")
    journal_cmd("members", "mbr", "members|uzvler|üzvlər|joins", "son qrup üzv hərəkətləri")
    journal_cmd("actions", "adm", "actions|emeller|əməllər|hadiseler|hadisələr|adminler", "son admin və qrup hadisələri")

    @ub.command("audit_report", pattern=r"^\.(?:audit_report|hesabat|audit_html)(?:\s+(\S+))?$",
                help=("günlük audit HTML hesabatı", "Günün bütün admin hərəkətlərini HTML faylı kimi göndərir."))
    async def on_audit_report(event):
        from common.audit import send_daily_audit_report
        import os
        from userbot.core.userbot_api import temp_edit
        d_arg = (event.pattern_match.group(1) or "").strip() or None
        await temp_edit(event, "⏳ <i>HTML audit hesabatı hazırlanır...</i>", 2)
        try:
            fp, total, dest = await send_daily_audit_report(ub.context, date_str=d_arg, force=True, target_chat=event.chat_id)
            if fp:
                await event.reply(f"✅ <b>Audit hesabatı göndərildi!</b>\n• Cəmi əməl: <b>{total}</b>\n• Fayl: <code>{os.path.basename(fp)}</code>")
            else:
                await event.reply("ℹ️ Göstərilən tarix üçün heç bir audit qeydi tapılmadı.")
        except Exception as e:
            await event.reply(f"❌ Hesabat xətası: <code>{e}</code>")

    # ── 4. .menu → 🎙 Səs & Audit (kartla eyni ekranlar) ──

    def _menu_cid(sctx) -> int:
        return sctx.menu.get("chat_id") or ub.creator_id

    def section_rows(sctx, screen: str, cid: int, note: str = ""):
        text, spec = view(ub, screen, cid, note)
        rows = [[sctx.btn(label, *act.split(":")) for label, act in row] for row in spec]
        if screen == "main":
            rows.append(sctx.nav())
        return text, rows

    @ub.section("logger", "🎙 Səs & Audit", order=42)
    async def render_section(sctx):
        return section_rows(sctx, "main", _menu_cid(sctx))

    @ub.on_section("logger")
    async def handle_section(sctx):
        cid = _menu_cid(sctx)
        action = ":".join([sctx.action, *sctx.args])
        if action.startswith("t:") and action.endswith(":in"):
            stream = action.split(":")[1]
            await sctx.answer()

            async def done(n):
                await sctx.show(*section_rows(sctx, f"t:cfg:{stream}", cid, n))
            _wait_input(cid, stream, done)
            await sctx.show(*section_rows(sctx, f"t:cfg:{stream}", cid, INPUT_HINT.format(min=INPUT_TTL // 60)))
            return
        _WAIT_INPUT.pop(cid, None)
        await sctx.answer("⏳" if action in ("start", "stop") else None)
        screen, note = await do_action(ub, ub.client, action, cid)
        await sctx.show(*section_rows(sctx, screen, cid, note))

    # ── 5. XAM TELETHON HADİSƏ DİNLƏYİCİLƏRİ ──

    # A) ✍️ hədəf yazılması (kart və ya .menu "✍️ Yaz" basılandan sonra, INPUT_TTL ərzində)
    @ub.raw(events.NewMessage(outgoing=True))
    async def on_user_val_input(event):
        info = _WAIT_INPUT.get(event.chat_id)
        if not info:
            return
        if time.time() - info["ts"] > INPUT_TTL:
            _WAIT_INPUT.pop(event.chat_id, None)      # vaxt bitib — mesaja toxunma
            return
        raw = (event.raw_text or "").strip()
        if not raw or raw.startswith((".", "/")):
            return
        _WAIT_INPUT.pop(event.chat_id, None)
        with contextlib.suppress(Exception):
            await event.delete()
        note = await apply_input(ub, event.client, info, raw, event.chat_id)
        with contextlib.suppress(Exception):
            await info["done"](note)

    ADMIN_CMD_ACTIONS = {
        "ban": "ban", "sban": "ban", "tban": "ban", "dban": "ban", "fban": "ban",
        "unban": "unban", "unfban": "unban",
        "mute": "mute", "smute": "mute", "tmute": "mute", "dmute": "mute",
        "unmute": "unmute",
        "kick": "kick", "skick": "kick", "dkick": "kick",
        "warn": "warn", "swarn": "warn", "dwarn": "warn", "rmwarn": "unwarn", "unwarn": "unwarn",
        "promote": "promote", "fullpromote": "promote",
        "demote": "demote",
        "pin": "pin", "unpin": "unpin", "unpinall": "unpin",
        "purge": "purge", "del": "del", "clean": "purge",
        "lock": "lock", "unlock": "unlock",
    }

    async def _check_and_log_admin_command(event):
        """Qruplarda adminlər tərəfindən yazılan moderasiya komandalarını audit bazasında qeydə alır."""
        if not event.is_group or not event.raw_text:
            return
        text = event.raw_text.strip()
        if not text or text[0] not in ("/", "!", "."):
            return
        cmd = text[1:].split()[0].split("@")[0].lower()
        if cmd not in ADMIN_CMD_ACTIONS:
            return

        cid = event.chat_id
        admin_id = event.sender_id
        if not admin_id:
            return

        # İcra edən şəxs admindirmi?
        if not event.out:
            try:
                perms = await event.client.get_permissions(cid, admin_id)
                if not getattr(perms, "is_admin", False):
                    return
            except Exception as _e:
                logger.debug("udulan xəta [userbot/plugins/logger.py:1454]: %r", _e)

        action = ADMIN_CMD_ACTIONS[cmd]
        admin = await event.get_sender()
        admin_name = get_user_name(admin) if admin else str(admin_id)
        admin_username = getattr(admin, "username", "") or ""

        target_id = None
        target_name = ""
        target_username = ""

        if event.is_reply:
            with contextlib.suppress(Exception):
                rep = await event.get_reply_message()
                if rep and rep.sender_id:
                    target_id = rep.sender_id
                    target_user = await rep.get_sender()
                    if target_user:
                        target_name = get_user_name(target_user)
                        target_username = getattr(target_user, "username", "") or ""
        else:
            parts = text.split()
            if len(parts) > 1:
                arg = parts[1]
                if arg.startswith("@"):
                    target_username = arg[1:]
                    target_name = arg
                elif arg.lstrip("-").isdigit():
                    target_id = int(arg)
                    target_name = f"ID:{arg}"

        chat = await event.get_chat()
        chat_title = getattr(chat, "title", str(cid))

        with contextlib.suppress(Exception):
            from common.audit import record_audit_event
            record_audit_event(
                chat_id=cid,
                chat_title=chat_title,
                admin_id=admin_id,
                admin_name=admin_name,
                admin_username=admin_username,
                target_id=target_id,
                target_name=target_name,
                target_username=target_username,
                action=action,
                details=f"Komanda: {text[:150]}",
                ts=time.time()
            )

    # B) Yeni mesajlar dinləyicisi (keşləmə, admin komandaları və transkript)
    @ub.raw(events.NewMessage())
    async def on_new_msg(event):
        if not audit_here(ub, _ev_cid(event)):
            return
        try:
            cache_message(event)
        except Exception as e:
            logger.debug(f"on_new_msg xətası: {e}")
        if _CUR_CLIENT.get() is not None:          # asistan: yalnız keş — .komandalar userbotun işidir
            return
        try:
            await _check_and_log_admin_command(event)
        except Exception as e:
            logger.debug(f"_check_and_log_admin_command xətası: {e}")

    # C) Silinən mesajlar dinləyicisi
    @ub.raw(events.MessageDeleted())
    async def on_msg_deleted(event):
        if not audit_here(ub, _ev_cid(event)):
            return
        try:
            cid = event.chat_id
            del_ids = getattr(event, "deleted_ids", None) or ([event.deleted_id] if getattr(event, "deleted_id", None) else [])
            if not cid:
                # Şəxsi çat / basic qrup: Telegram (UpdateDeleteMessages) çatı göstərmir → keşdən tap
                cid = next((_mid_index[m] for m in del_ids if m in _mid_index), None)
                if not cid:
                    return
                del_ids = [m for m in del_ids if _mid_index.get(m) == cid]
            for m in del_ids:
                if _mid_index.get(m) == cid:
                    _mid_index.pop(m, None)

            now = time.time()
            now_str = time.strftime("%d.%m %H:%M:%S", time.localtime(now))
            deleted_items = []

            for mid in del_ids:
                data = _msg_cache.pop((cid, mid), None)
                if data:
                    data["del_time"] = now
                    _deleted_history[cid].append(data)
                    deleted_items.append(data)

            if not deleted_items:
                return

            # Audit aktivdirsə hədəfə göndər (del axınına)
            if is_audit_active_for(ub, cid):
                chat_title = deleted_items[0].get("chat_title") or f"ID: {cid}"

                # Tək mesaj silinib
                if len(deleted_items) == 1:
                    m = deleted_items[0]
                    sent_str = time.strftime("%d.%m %H:%M:%S", time.localtime(m.get("time", now)))
                    body = escape(m.get("text") or "")
                    media = m.get("media_label")

                    card_lines = [
                        ("# Silinən Mesaj Qeydə Alındı", ""),
                        ("Qrup", f"{chat_title[:18]} ({cid})"),
                        ("Müəllif", U(m.get("sender_name"), m.get("sender_id"), m.get("username"))),
                        ("Yazılma", sent_str),
                        ("Silinmə", now_str),
                    ]
                    if media:
                        card_lines.append(("Media", media))
                    card_lines.append(("Mətn", body[:300] if body else "Mətnsiz media"))
                    text = acard(card_lines, "🗑")
                    await send_to_target(ub, out_client(ub, event.client), stream="del1", text=text, chat_id=cid)
                else:
                    # Kütləvi silinmə
                    card_lines = [
                        ("# Kütləvi Silinmə (Purge)", ""),
                        ("Qrup", f"{chat_title[:18]} ({cid})"),
                        ("Silinən Sayı", f"{len(deleted_items)} ədəd"),
                        ("Tarix", now_str),
                        ("", ""),
                    ]
                    for idx, m in enumerate(deleted_items[:10], 1):
                        sn = m.get("sender_name") or "İstifadəçi"
                        b = (m.get("text") or m.get("media_label") or "Media")[:25]
                        card_lines.append(("", [f"{idx}.", U(sn, m.get("sender_id"), m.get("username")), f"— {b}"]))
                    text = acard(card_lines, "🧹")
                    await send_to_target(ub, out_client(ub, event.client), stream="purge", text=text, chat_id=cid)

                    with contextlib.suppress(Exception):
                        from common.audit import record_audit_event
                        record_audit_event(
                            chat_id=cid, chat_title=chat_title,
                            admin_id=None, admin_name="Admin", admin_username="",
                            target_id=None, target_name="", target_username="",
                            action="purge", details=f"Kütləvi silinmə: {len(deleted_items)} mesaj",
                            ts=now
                        )
        except Exception as e:
            logger.debug(f"on_msg_deleted xətası: {e}")

    # D) Redaktə edilən mesajlar dinləyicisi
    @ub.raw(events.MessageEdited())
    async def on_msg_edited(event):
        if not audit_here(ub, _ev_cid(event)):
            return
        try:
            if not getattr(event, "message", None):
                return
            cid, mid = event.chat_id, event.message.id
            if not cid:
                return

            cached = _msg_cache.get((cid, mid))
            old_text = cached.get("text") if cached else None
            new_text = event.raw_text or ""

            # Mesajın mətni həqiqətən dəyişibmi?
            if old_text is not None and old_text != new_text:
                now = time.time()
                sender = getattr(event, "sender", None)
                s_name = get_user_name(sender) if sender else cached.get("sender_name", "Bilinmir")
                u_name = getattr(sender, "username", None) or cached.get("username", "")
                chat_title = getattr(event.chat, "title", None) or cached.get("chat_title", str(cid))

                ed_data = {
                    "chat_id": cid,
                    "chat_title": chat_title,
                    "msg_id": mid,
                    "sender_name": s_name,
                    "username": u_name,
                    "sender_id": event.message.sender_id or cached.get("sender_id", 0),
                    "old_text": old_text,
                    "new_text": new_text,
                    "edit_time": now,
                }
                _edited_history[cid].append(ed_data)
                cached["text"] = new_text

                if is_audit_active_for(ub, cid):
                    t_str = time.strftime("%d.%m %H:%M:%S", time.localtime(now))
                    card_lines = [
                        ("# Mesaj Redaktəsi Qeydə Alındı", ""),
                        ("Qrup", f"{chat_title[:18]} ({cid})"),
                        ("Müəllif", U(s_name, ed_data.get("sender_id"), u_name)),
                        ("Tarix", t_str),
                        ("Əvvəl", old_text[:300] or "—"),
                        ("Sonra", new_text[:300] or "—"),
                    ]
                    text = acard(card_lines, "✏️")
                    await send_to_target(ub, out_client(ub, event.client), stream="ed", text=text, chat_id=cid)
            else:
                cache_message(event)
        except Exception as e:
            logger.debug(f"on_msg_edited xətası: {e}")

    # E) Qrup hərəkətləri dinləyicisi (Ban, üzv giriş/çıxış, admin, qrup dəyişiklikləri)
    @ub.raw(events.ChatAction())
    async def on_chat_action(event):
        if not audit_here(ub, _ev_cid(event)):
            return
        try:
            cid = event.chat_id
            if not cid:
                return

            now = time.time()
            chat_title = getattr(event.chat, "title", str(cid))
            t_str = time.strftime("%d.%m %H:%M:%S", time.localtime(now))

            # 1. Qovulma / Ban
            if event.user_kicked:
                user = None
                with contextlib.suppress(Exception):
                    user = await event.get_user()
                target_name = get_user_name(user)
                target_id = event.user_id or (getattr(user, "id", 0) if user else 0)

                admin = None
                with contextlib.suppress(Exception):
                    admin = await event.get_added_by()
                admin_name = get_user_name(admin) if admin else "Admin"
                admin_id = getattr(admin, "id", 0) if admin else 0
                if _seen((cid, target_id, "ban")):
                    return

                b_data = {
                    "chat_id": cid,
                    "chat_title": chat_title,
                    "action": "Qovulma / Ban",
                    "user_name": target_name,
                    "user_id": target_id,
                    "admin_name": admin_name,
                    "admin_id": admin_id,
                    "time": now,
                }
                _bans_history[cid].append(b_data)

                with contextlib.suppress(Exception):
                    from common.audit import record_audit_event
                    record_audit_event(
                        chat_id=cid, chat_title=chat_title,
                        admin_id=admin_id, admin_name=admin_name, admin_username=getattr(admin, "username", "") or "",
                        target_id=target_id, target_name=target_name, target_username=getattr(user, "username", "") or "",
                        action="kick", details="Qrupdan qovulma / ban", ts=now
                    )

                if is_audit_active_for(ub, cid):
                    card_lines = [
                        ("# Qrupdan Qovulma / Ban", ""),
                        ("Qrup", f"{chat_title[:18]} ({cid})"),
                        ("Cəzalandırılan", U(target_name, target_id, getattr(user, "username", None))),
                        ("İcra Edən", U(admin_name, admin_id, getattr(admin, "username", None))),
                        ("Tarix", t_str),
                    ]
                    text = acard(card_lines, "⛔")
                    await send_to_target(ub, out_client(ub, event.client), stream="kick", text=text, chat_id=cid)

            # 2. Üzv Qoşuldu / Əlavə Edildi
            elif event.user_joined or event.user_added:
                user = None
                with contextlib.suppress(Exception):
                    user = await event.get_user()
                target_name = get_user_name(user)
                target_id = event.user_id or (getattr(user, "id", 0) if user else 0)

                added_by = None
                with contextlib.suppress(Exception):
                    added_by = await event.get_added_by()
                admin_name = get_user_name(added_by) if added_by else ""

                if _seen((cid, target_id, "join")):
                    return
                act_name = "Əlavə edildi" if event.user_added else "Qoşuldu"
                m_data = {
                    "chat_id": cid,
                    "chat_title": chat_title,
                    "action": act_name,
                    "user_name": target_name,
                    "user_id": target_id,
                    "admin_name": admin_name,
                    "time": now,
                }
                _members_history[cid].append(m_data)

                if is_audit_active_for(ub, cid):
                    card_lines = [
                        (f"# Üzv {act_name}", ""),
                        ("Qrup", f"{chat_title[:18]} ({cid})"),
                        ("Üzv", U(target_name, target_id, getattr(user, "username", None))),
                    ]
                    if admin_name:
                        card_lines.append(("Əlavə Edən", U(admin_name, getattr(added_by, "id", None),
                                                           getattr(added_by, "username", None))))
                    card_lines.append(("Tarix", t_str))
                    text = acard(card_lines, "📥")
                    await send_to_target(ub, out_client(ub, event.client), stream="join", text=text, chat_id=cid)

            # 3. Üzv Çıxdı
            elif event.user_left:
                user = None
                with contextlib.suppress(Exception):
                    user = await event.get_user()
                target_name = get_user_name(user)
                target_id = event.user_id or (getattr(user, "id", 0) if user else 0)

                if _seen((cid, target_id, "left")):
                    return
                m_data = {
                    "chat_id": cid,
                    "chat_title": chat_title,
                    "action": "Çıxdı",
                    "user_name": target_name,
                    "user_id": target_id,
                    "admin_name": "",
                    "time": now,
                }
                _members_history[cid].append(m_data)

                if is_audit_active_for(ub, cid):
                    card_lines = [
                        ("# Üzv Qrupdan Çıxdı", ""),
                        ("Qrup", f"{chat_title[:18]} ({cid})"),
                        ("İstifadəçi", U(target_name, target_id, getattr(user, "username", None))),
                        ("Tarix", t_str),
                    ]
                    text = acard(card_lines, "📤")
                    await send_to_target(ub, out_client(ub, event.client), stream="left", text=text, chat_id=cid)

            # 5. Mesaj Bərkidildi / Bərkitmə Ləğv Edildi
            elif getattr(event, "new_pin", False) or getattr(event, "unpin", False):
                pin_act = "Mesaj Bərkidildi" if getattr(event, "new_pin", False) else "Bərkitmə Ləğv Edildi"
                user = None
                with contextlib.suppress(Exception):
                    user = await event.get_user()
                u_name = get_user_name(user)
                a_data = {
                    "chat_id": cid,
                    "chat_title": chat_title,
                    "action": pin_act,
                    "user_name": u_name,
                    "user_id": event.user_id or 0,
                    "time": now,
                }
                _admin_history[cid].append(a_data)

                with contextlib.suppress(Exception):
                    from common.audit import record_audit_event
                    record_audit_event(
                        chat_id=cid, chat_title=chat_title,
                        admin_id=event.user_id or 0, admin_name=u_name, admin_username=getattr(user, "username", "") or "",
                        target_id=None, target_name="", target_username="",
                        action="pin" if getattr(event, "new_pin", False) else "unpin", details=pin_act, ts=now
                    )

                if is_audit_active_for(ub, cid):
                    card_lines = [
                        (f"# {pin_act}", ""),
                        ("Qrup", f"{chat_title[:18]} ({cid})"),
                        ("İcraçı", U(u_name, event.user_id, getattr(user, "username", None))),
                        ("Tarix", t_str),
                    ]
                    text = acard(card_lines, "📌")
                    await send_to_target(ub, out_client(ub, event.client), stream="pin", text=text, chat_id=cid)

            # 6. Başlıq dəyişdirildi
            elif getattr(event, "new_title", None):
                nt = str(event.new_title)
                a_data = {
                    "chat_id": cid,
                    "chat_title": chat_title,
                    "action": f"Ad: {nt[:15]}",
                    "user_name": "",
                    "user_id": 0,
                    "time": now,
                }
                _admin_history[cid].append(a_data)

                with contextlib.suppress(Exception):
                    from common.audit import record_audit_event
                    record_audit_event(
                        chat_id=cid, chat_title=chat_title,
                        admin_id=event.user_id or 0, admin_name="", admin_username="",
                        target_id=None, target_name="", target_username="",
                        action="title", details=f"Yeni ad: {nt}", ts=now
                    )

                if is_audit_active_for(ub, cid):
                    card_lines = [
                        ("# Qrup Adı Dəyişdirildi", ""),
                        ("Köhnə Ad", f"{chat_title[:18]}"),
                        ("Yeni Ad", f"{nt[:18]}"),
                        ("Tarix", t_str),
                    ]
                    text = acard(card_lines, "🏷")
                    await send_to_target(ub, out_client(ub, event.client), stream="title", text=text, chat_id=cid)

            # 7. Şəkil dəyişdirildi
            elif getattr(event, "new_photo", False):
                a_data = {
                    "chat_id": cid,
                    "chat_title": chat_title,
                    "action": "Şəkil dəyişdi",
                    "user_name": "",
                    "user_id": 0,
                    "time": now,
                }
                _admin_history[cid].append(a_data)

                with contextlib.suppress(Exception):
                    from common.audit import record_audit_event
                    record_audit_event(
                        chat_id=cid, chat_title=chat_title,
                        admin_id=event.user_id or 0, admin_name="", admin_username="",
                        target_id=None, target_name="", target_username="",
                        action="photo", details="Qrup şəkli dəyişdirildi", ts=now
                    )

                if is_audit_active_for(ub, cid):
                    card_lines = [
                        ("# Qrup Şəkli Dəyişdirildi", ""),
                        ("Qrup", f"{chat_title[:18]} ({cid})"),
                        ("Tarix", t_str),
                    ]
                    text = acard(card_lines, "🖼")
                    await send_to_target(ub, out_client(ub, event.client), stream="photo", text=text, chat_id=cid)

        except Exception as e:
            logger.debug(f"on_chat_action xətası: {e}")

    # F) Üzv statusu dəyişikliyi (UpdateChannelParticipant) — admin təyini / alınması, ban, məhdudiyyət,
    #    ban götürülməsi. Supergrup / kanalda bunlar üçün servis mesajı (ChatAction) gəlmir;
    #    Telegram bu yeniləməni yalnız hesabın admin olduğu çatlarda göndərir.
    @ub.raw(events.Raw(types.UpdateChannelParticipant))
    async def on_participant(update):
        if not audit_here(ub, _ev_cid(update)):
            return
        try:
            cid = utils.get_peer_id(types.PeerChannel(update.channel_id))
            prev, new = update.prev_participant, update.new_participant
            is_adm = lambda p: isinstance(p, (types.ChannelParticipantAdmin, types.ChannelParticipantCreator))  # noqa: E731
            is_ban = lambda p: isinstance(p, types.ChannelParticipantBanned)                                     # noqa: E731
            full_ban = lambda p: is_ban(p) and bool(getattr(p.banned_rights, "view_messages", False))          # noqa: E731

            if is_adm(new) and not is_adm(prev):
                stream, kind, act = "adm", "adm+", "Admin təyin edildi"
            elif is_adm(new) and is_adm(prev):
                stream, kind, act = "adm", "adm~", "Admin hüquqları dəyişdi"
            elif is_adm(prev):
                stream, kind, act = "adm", "adm-", "Adminlikdən alındı"
            elif full_ban(new):
                stream, kind, act = "ban", "ban", "Ban"
            elif is_ban(new):
                stream, kind, act = "ban", "mute", "Məhdudlaşdırıldı"
            elif is_ban(prev):
                stream, kind, act = "ban", "unban", "Ban götürüldü" if full_ban(prev) else "Məhdudiyyət götürüldü"
            elif prev is None and new is not None:
                stream, kind, act = "mbr", "join", "Qoşuldu"
            elif prev is not None and (new is None or isinstance(new, types.ChannelParticipantLeft)):
                stream, kind, act = "mbr", "left", "Çıxdı"
            else:
                return
            if _seen((cid, update.user_id, kind)):
                return

            client = cur_client(ub)

            async def _ent(peer):
                with contextlib.suppress(Exception):
                    return await client.get_entity(peer)
                return None

            user, actor, chat = await _ent(update.user_id), await _ent(update.actor_id), await _ent(cid)
            u_name = get_user_name(user) if user else str(update.user_id)
            a_name = (get_user_name(actor) if actor else str(update.actor_id)) if update.actor_id != update.user_id else ""
            chat_title = chat.title if isinstance(chat, (types.Channel, types.Chat)) else str(cid)
            now = time.time()
            item = {"chat_id": cid, "chat_title": chat_title, "action": act, "user_name": u_name,
                    "user_id": update.user_id, "admin_name": a_name, "admin_id": update.actor_id, "time": now}
            {"adm": _admin_history, "ban": _bans_history, "mbr": _members_history}[stream][cid].append(item)

            if stream in ("adm", "ban"):
                det_parts = []
                if kind in ("adm+", "adm~") and getattr(new, "admin_rights", None):
                    r = new.admin_rights
                    rights = [n for f, n in (("delete_messages", "silmə"), ("ban_users", "ban"), ("pin_messages", "sancaq"),
                                             ("invite_users", "dəvət"), ("change_info", "info"),
                                             ("manage_call", "yayım"), ("add_admins", "admin")) if getattr(r, f, False)]
                    det_parts.append(f"Hüquqlar: {', '.join(rights) or '—'}")
                    if getattr(new, "rank", None):
                        det_parts.append(f"Titul: {new.rank}")
                if kind == "mute" and getattr(new, "banned_rights", None):
                    br = new.banned_rights
                    blocked = [n for f, n in (("send_messages", "mesaj"), ("send_media", "media"), ("send_stickers", "stiker"),
                                              ("send_gifs", "gif"), ("embed_links", "link"), ("send_polls", "sorğu"))
                               if getattr(br, f, False)]
                    det_parts.append(f"Qadağa: {', '.join(blocked) or '—'}")
                if is_ban(new) and getattr(new.banned_rights, "until_date", None):
                    until = new.banned_rights.until_date
                    if until and until.year < 2037:
                        det_parts.append(f"Bitir: {until.astimezone().strftime('%d.%m.%Y %H:%M')}")

                with contextlib.suppress(Exception):
                    from common.audit import record_audit_event
                    record_audit_event(
                        chat_id=cid, chat_title=chat_title,
                        admin_id=update.actor_id if update.actor_id != update.user_id else None,
                        admin_name=a_name, admin_username=getattr(actor, "username", "") or "",
                        target_id=update.user_id, target_name=u_name, target_username=getattr(user, "username", "") or "",
                        action=kind, details=" | ".join(det_parts), ts=now
                    )

            if not is_audit_active_for(ub, cid):
                return
            card = [(f"# {act}", ""), ("Qrup", f"{chat_title[:18]} ({cid})"), ("Üzv", U(u_name, update.user_id, getattr(user, "username", None)))]
            if a_name:
                card.append(("İcra edən", U(a_name, update.actor_id, getattr(actor, "username", None))))
            if kind in ("adm+", "adm~") and getattr(new, "admin_rights", None):
                r = new.admin_rights
                rights = [n for f, n in (("delete_messages", "silmə"), ("ban_users", "ban"), ("pin_messages", "sancaq"),
                                         ("invite_users", "dəvət"), ("change_info", "info"),
                                         ("manage_call", "yayım"), ("add_admins", "admin")) if getattr(r, f, False)]
                card.append(("Hüquqlar", ", ".join(rights) or "—"))
                if getattr(new, "rank", None):
                    card.append(("Titul", new.rank[:18]))
            if kind == "mute" and getattr(new, "banned_rights", None):
                br = new.banned_rights
                blocked = [n for f, n in (("send_messages", "mesaj"), ("send_media", "media"), ("send_stickers", "stiker"),
                                          ("send_gifs", "gif"), ("embed_links", "link"), ("send_polls", "sorğu"))
                           if getattr(br, f, False)]
                card.append(("Qadağa", ", ".join(blocked) or "—"))
            if is_ban(new) and getattr(new.banned_rights, "until_date", None):
                until = new.banned_rights.until_date
                if until and until.year < 2037:
                    card.append(("Bitir", until.astimezone().strftime("%d.%m %H:%M")))
            card.append(("Tarix", time.strftime("%d.%m %H:%M:%S", time.localtime(now))))
            ev_stream = {"adm+": "promo", "adm~": "promo", "adm-": "promo", "ban": "kick"}.get(kind, kind)
            await send_to_target(ub, out_client(ub, client), stream=ev_stream, text=acard(card, {"promote": "👮", "demote": "👤", "adm+": "👮", "adm-": "👤", "adm~": "⚙️", "ban": "⛔", "mute": "🔇", "unban": "🔓", "join": "📥", "left": "📤"}.get(kind, "🛡")), chat_id=cid)
        except Exception as e:
            logger.debug(f"on_participant xətası: {e}")

    # 🎙 Asistan hesabına da qoşula bilən audit dinləyiciləri
    global _AUDIT_UB
    _AUDIT_UB = ub
    AUDIT_HANDLERS[:] = [
        (on_new_msg, events.NewMessage()),
        (on_msg_deleted, events.MessageDeleted()),
        (on_msg_edited, events.MessageEdited()),
        (on_chat_action, events.ChatAction()),
        (on_participant, events.Raw(types.UpdateChannelParticipant)),
    ]
    ub.on_remove(lambda: detach_all_assistants(ub))        # plugin reload / silmə → asistandakı dinləyicilər çıxsın
    refresh_assistants(ub)                                 # artıq işləyən asistan varsa yeni dinləyicilər dərhal qoşulsun
