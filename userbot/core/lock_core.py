"""
🔒 lock_core.py — .lock plugininin məntiqi: mesaj növlərinin tanınması, kilid vəziyyəti (bazada), qaydalar.
(Plugin faylı userbot/plugins/lock.py komandaları və handler-ləri qoşur; inline kart userbot/core/lock_card.py.)
"""
import re
from html import escape

from telethon.tl.types import Chat, MessageEntityTextUrl, MessageEntityUrl, User

from userbot.core.userbot_api import logger
from userbot.core.userbot_db import get_udb, run_bg

NS, KEY = "lock", "cfg"
SERVICE_IDS = {777000}
ADMIN_TTL = 300.0                 # san. — «göndərən admindir?» cavabı keşdə
NOTICE_GAP = 30.0                 # san. — eyni çat / şəxs üçün bildiriş bu qədər tez-tez deyil
NOTICE_LIFETIME = 6               # san. — bildiriş özü silinir

KINDS = {                         # açar → (emoji, ad)
    "sticker": ("🎭", "stiker"), "gif": ("🎞", "GIF"), "photo": ("🖼", "şəkil"), "video": ("🎬", "video"),
    "voice": ("🎤", "səsli mesaj"), "videonote": ("⏺", "dairəvi video"), "audio": ("🎵", "musiqi"),
    "document": ("📎", "fayl"), "contact": ("👤", "kontakt"), "location": ("📍", "yer"), "poll": ("📊", "sorğu"),
    "dice": ("🎲", "zər"), "game": ("🎮", "oyun"), "link": ("🔗", "link"), "forward": ("↪️", "yönləndirmə"),
    "inline": ("🤖", "inline bot"), "text": ("💬", "mətn"),
}
MEDIA = ("sticker", "gif", "photo", "video", "voice", "videonote", "audio", "document")
ALIASES = {
    "sticker": "sticker", "stickers": "sticker", "stiker": "sticker", "stikerlər": "sticker", "stikerler": "sticker",
    "gif": "gif", "gifs": "gif", "animation": "gif",
    "photo": "photo", "photos": "photo", "foto": "photo", "şəkil": "photo", "sekil": "photo", "image": "photo",
    "video": "video", "videos": "video",
    "voice": "voice", "səs": "voice", "ses": "voice", "voicemsg": "voice",
    "videonote": "videonote", "round": "videonote", "circle": "videonote", "dairə": "videonote", "daire": "videonote",
    "audio": "audio", "music": "audio", "musiqi": "audio",
    "document": "document", "doc": "document", "file": "document", "files": "document", "fayl": "document",
    "sənəd": "document", "sened": "document",
    "contact": "contact", "kontakt": "contact", "location": "location", "geo": "location", "yer": "location",
    "poll": "poll", "sorğu": "poll", "sorgu": "poll", "dice": "dice", "zər": "dice", "zer": "dice",
    "game": "game", "oyun": "game", "link": "link", "links": "link", "url": "link",
    "forward": "forward", "fwd": "forward", "yönləndirmə": "forward", "yonlendirme": "forward",
    "inline": "inline", "via": "inline", "text": "text", "mətn": "text", "metn": "text",
}
GROUPS = {"media": MEDIA, "all": tuple(KINDS), "hamısı": tuple(KINDS), "hamisi": tuple(KINDS)}
URL_RE = re.compile(r"(https?://|www\.|t\.me/)", re.I)


def parse_kinds(words) -> tuple[list, list]:
    """['sticker', 'media', '@ali'] → (['sticker', 'gif', ...], ['@ali']): tanınan növlər / qalan sözlər."""
    kinds, rest = [], []
    for w in words:
        key = w.lower().strip(",;")
        found = GROUPS.get(key) or ((ALIASES[key],) if key in ALIASES else None)
        if found:
            kinds += [k for k in found if k not in kinds]
        else:
            rest.append(w)
    return kinds, rest


def kinds_of(msg) -> set:
    """Mesajın növləri: bir əsas növ (sticker / gif / ... / text) + bayraqlar (link, forward, inline)."""
    media = getattr(msg, "media", None)
    plain = media is None or type(media).__name__ in ("MessageMediaWebPage",)   # link önizləməsi mediadır, şəkil sayılmasın
    out = set()
    primary = "text"
    if not plain:
        for kind, attr in (("sticker", "sticker"), ("gif", "gif"), ("videonote", "video_note"), ("voice", "voice"),
                           ("audio", "audio"), ("video", "video"), ("photo", "photo"), ("contact", "contact"),
                           ("poll", "poll"), ("dice", "dice"), ("game", "game")):
            if getattr(msg, attr, None):
                primary = kind
                break
        else:
            if getattr(msg, "geo", None) or getattr(msg, "venue", None):
                primary = "location"
            elif getattr(msg, "document", None):
                primary = "document"
    out.add(primary)
    ents = getattr(msg, "entities", None) or []
    text = getattr(msg, "message", None) or getattr(msg, "raw_text", None) or ""
    if any(isinstance(e, (MessageEntityUrl, MessageEntityTextUrl)) for e in ents) or (isinstance(text, str) and URL_RE.search(text)):
        out.add("link")
    if getattr(msg, "fwd_from", None):
        out.add("forward")
    if getattr(msg, "via_bot_id", None) or getattr(msg, "via_bot", None):
        out.add("inline")
    return out


DEFAULT_CFG = {
    "enabled": True,        # 🟢 bütün kilidlər işləyir (🔴 — hamısı fasilədə, ayarlar qalır)
    "revoke": True,         # şəxsi çatda silmə: hər iki tərəfdən (True) / yalnız səndən (False)
    "admins": True,         # qrup üzrə kilid adminlərə tətbiq olunmur
    "bots": False,          # botların mesajı da silinsin
}


class LockState:
    """{"chats": {chat_id: {"all": [növ...], "users": {uid: [növ...]}, "paused": bool}}, "cfg": {...}} — yaddaşda,
    yazı fon thread-ində."""

    def __init__(self, udb=None):
        self._udb = udb
        self.chats = {}
        self.cfg = dict(DEFAULT_CFG)
        self.deleted = 0
        try:
            saved = (udb or get_udb()).kv_get(NS, KEY) or {}
            self.chats = {str(c): {"all": list(v.get("all") or []), "paused": bool(v.get("paused")),
                                   "users": {str(u): list(k) for u, k in (v.get("users") or {}).items()}}
                          for c, v in (saved.get("chats") or {}).items()}
            self.cfg.update({k: bool(v) for k, v in (saved.get("cfg") or {}).items() if k in DEFAULT_CFG})
        except Exception as e:
            logger.warning(f"Lock ayarı oxunmadı: {e}")

    def save(self):
        snap = {"chats": self.chats, "cfg": self.cfg}
        try:
            run_bg((self._udb or get_udb()).kv_set, NS, KEY, snap)
        except Exception as e:
            logger.warning(f"Lock ayarı yazılmadı: {e}")

    def _scope(self, chat_id, uid=None, create=False):
        chat = self.chats.get(str(chat_id))
        if chat is None:
            if not create:
                return None
            chat = self.chats[str(chat_id)] = {"all": [], "users": {}, "paused": False}
        if uid is None:
            return chat["all"]
        if create:
            return chat["users"].setdefault(str(uid), [])
        return chat["users"].get(str(uid))

    def add(self, chat_id, kinds, uid=None) -> list:
        scope = self._scope(chat_id, uid, create=True)
        scope += [k for k in kinds if k not in scope]
        self.save()
        return scope

    def remove(self, chat_id, kinds, uid=None) -> list:
        """kinds=None — bu əhatənin hamısı. Qaytarır: qalan növlər."""
        scope = self._scope(chat_id, uid)
        if scope is None:
            return []
        scope[:] = [] if kinds is None else [k for k in scope if k not in kinds]
        chat = self.chats[str(chat_id)]
        if uid is not None and not scope:
            chat["users"].pop(str(uid), None)
        self._prune(chat_id)
        self.save()
        return scope

    def _prune(self, chat_id):
        chat = self.chats.get(str(chat_id))
        if chat is not None and not chat["all"] and not chat["users"]:
            self.chats.pop(str(chat_id), None)                    # kilidsiz çat — qeyd də silinir (fasilə bayrağı da)

    def locked(self, chat_id, uid=None) -> set:
        chat = self.chats.get(str(chat_id))
        if not chat:
            return set()
        return set(chat["all"]) | set(chat["users"].get(str(uid), []))

    def blocked_kind(self, chat_id, uid, present) -> str | None:
        """Mesajın növlərindən hansı bağlıdır? (gözəgörünən ilk növ) — fasilədə / söndürülübsə və ya yoxdursa None."""
        chat = self.chats.get(str(chat_id))
        if not chat or chat.get("paused") or not self.cfg["enabled"]:
            return None
        hit = self.locked(chat_id, uid) & set(present)
        return next((k for k in KINDS if k in hit), None)

    # ── 🔘 düymə / kart əməliyyatları ──
    def toggle(self, chat_id, kind, uid=None) -> bool:
        """Növü çevir → indi bağlıdır?"""
        if kind in (self._scope(chat_id, uid) or []):
            self.remove(chat_id, [kind], uid)
            return False
        self.add(chat_id, [kind], uid)
        return True

    def set_all(self, chat_id, kinds) -> None:
        """Çatın ümumi (hamı üçün) kilidini tam əvəz et; boş siyahı — açır (şəxs üzrə kilidlər qalır)."""
        if kinds:
            scope = self._scope(chat_id, None, create=True)
            scope[:] = [k for k in KINDS if k in set(kinds)]
        elif self._scope(chat_id) is not None:
            self.chats[str(chat_id)]["all"] = []
            self._prune(chat_id)
        self.save()

    def clear_chat(self, chat_id) -> None:
        self.chats.pop(str(chat_id), None)
        self.save()

    def clear_everything(self) -> int:
        n = len(self.chats)
        self.chats.clear()
        self.save()
        return n

    def pause(self, chat_id, on: bool) -> None:
        if str(chat_id) in self.chats:
            self.chats[str(chat_id)]["paused"] = bool(on)
            self.save()

    def set_cfg(self, key: str, value: bool) -> bool:
        if key not in DEFAULT_CFG:
            return False
        self.cfg[key] = bool(value)
        self.save()
        return True

    def locked_chats(self) -> list:
        return [int(c) for c in self.chats]


def label(kinds) -> str:
    ks = [k for k in KINDS if k in set(kinds)]
    if set(ks) == set(KINDS):
        return "🔒 hər şey"
    if set(MEDIA) <= set(ks) and len(ks) > len(MEDIA):
        rest = [k for k in ks if k not in MEDIA]
        return "🔒 media + " + ", ".join(f"{KINDS[k][0]} {KINDS[k][1]}" for k in rest)
    if ks == list(MEDIA):
        return "🔒 bütün media"
    return ", ".join(f"{KINDS[k][0]} {KINDS[k][1]}" for k in ks) or "—"


def status_text(state: LockState, chat_id, names: dict | None = None) -> str:
    chat = state.chats.get(str(chat_id))
    if not chat:
        return "🔓 <b>Bu çatda heç nə bağlı deyil.</b>\n\n<i>.lock sticker · .lock media · .lock all · .unlock · .locks</i>"
    lines = ["🔒 <b>Bu çatda bağlı olanlar</b>" + (" — ⏸ fasilədə" if chat.get("paused") else ""), ""]
    if chat["all"]:
        lines.append("Hamı üçün: " + label(chat["all"]))
    for uid, kinds in chat["users"].items():
        lines.append(f"{escape((names or {}).get(uid) or uid)}: {label(kinds)}")
    lines += ["", f"Silinib (bu açılışdan): {state.deleted}", "<i>.lock · .unlock · .locks</i>"]
    return "\n".join(lines)


async def can_delete(client, chat) -> bool:
    """Bu çatda başqasının mesajını silə bilərsənmi (şəxsi çatda həmişə)."""
    if isinstance(chat, User):
        return True
    perms = await client.get_permissions(chat, "me")
    return bool(perms.is_creator or getattr(perms, "delete_messages", False) or (isinstance(chat, Chat) and perms.is_admin))
