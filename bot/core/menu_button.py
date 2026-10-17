"""
⌨️ Menyu düymələri — iki ayrı Telegram mexanizmi, ikisi də menyudan idarə olunur.

1) Həmişə görünən klaviatura (reply keyboard) — şəxsi çatda, HƏR istifadəçi üçün ayrıca, üç üslub:
     🔴 bağlı (standart: heç nə dəyişmir, inline panellər əsas qalır) · ⌨️ qısa (6 qısayol) ·
     ⌨️ tam — əsas menyunun BÜTÜN düymələri (sahib: 8 bölmə + Yenilə / Bələdçi / Bağla; istifadəçi: öz menyusu).
     /keyboard [off|short|full] · /menu → ⌨️ Klaviatura (istifadəçi) · /menubutton (sahib).
   Düymə basılanda yazı xəttinə adi mətn düşür; plugin (bot/plugins/menu_button_plugin.py) onu tanıyır:
   qısa üslubda — komanda; tam üslubda — həmin inline düymənin callback-i (BridgeMiddleware ilə həqiqi panel açılır).
2) Komanda siyahısı ("Menu" düyməsi — yazı xəttinin solunda): bot.set_my_commands + MenuButtonCommands.
   Hər dil üçün ayrıca təsvir (i18n.LANGS), sahib üçün əlavə admin komandaları (BotCommandScopeChat).
   🤖 Avto — açılışda fərq varsa özü yenilənir · ✋ Manual (standart) — BotFather-dəki siyahıya toxunmur, yalnız düymə ilə.
"""
import hashlib
import json
import logging
import re
from collections import deque

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.methods import (AnswerCallbackQuery, DeleteMessage, EditMessageCaption, EditMessageMedia,
                             EditMessageReplyMarkup, EditMessageText, SendMessage)
from aiogram.types import (BotCommand, BotCommandScopeChat, BotCommandScopeDefault, KeyboardButton,
                           MenuButtonCommands, ReplyKeyboardMarkup)

from common import i18n
from common.database import Settings
from common.i18n import _

logger = logging.getLogger(__name__)
_cfg = Settings("menubtn:", empty_is_default=True)
S_MODE, S_HASH = "cmds_mode", "cmds_hash"
CMDS_MODES = ("manual", "auto")

# ── klaviatura ──
LABELS = {
    "menu": lambda l: _("📋 Menyu", l),
    "search": lambda l: _("🔎 Axtar", l),
    "format": lambda l: _("🎧 Format", l),
    "playlists": lambda l: _("📚 Pleylistlər", l),
    "premium": lambda l: _("💎 Premium", l),
    "lang": lambda l: _("🌐 Dil", l),
    "invite": lambda l: _("🎁 Dəvət et", l),
}
COMMAND_OF = {"menu": "/menu", "format": "/codec", "playlists": "/playlist", "premium": "/premium", "lang": "/lang",
              "invite": "/invite"}                       # "search" — komanda yox, izah mesajıdır
LAYOUT = (("menu", "search"), ("format", "playlists"), ("premium", "lang"))


STYLES = ("off", "short", "full")
STYLE_LABEL = {"off": "🔴 Deaktiv", "short": "🟢 Qısa", "full": "🟢 Tam"}
BRIDGE_PREFIX = "kbbridge:"


def kb_style(uid) -> str:
    v = _cfg.get(f"kb:{uid}")
    return "full" if v == "full" else "short" if v == "1" else "off"       # "1" — ilk versiyadakı qısa klaviatura


def set_kb_style(uid, style: str):
    _cfg.put(f"kb:{uid}", {"full": "full", "short": "1"}.get(style, "0"))


def next_style(cur: str) -> str:
    return STYLES[(STYLES.index(cur) + 1) % len(STYLES)] if cur in STYLES else "short"


def kb_on(uid) -> bool:
    return kb_style(uid) != "off"


def set_kb(uid, on: bool):
    set_kb_style(uid, "short" if on else "off")


def kb_users() -> dict:
    """Klaviaturanı açmış istifadəçi sayı üslublara görə (sahib paneli üçün)."""
    from common.database import get_db
    try:
        rows = get_db()._all("SELECT value, count(*) AS n FROM settings WHERE key LIKE %s AND value IN ('1', 'full') "
                             "GROUP BY value", ("menubtn:kb:%",))
        got = {r["value"]: int(r["n"]) for r in rows}
    except Exception:
        got = {}
    return {"short": got.get("1", 0), "full": got.get("full", 0)}


def label(action: str, lang: str | None = None) -> str:
    return LABELS[action](lang)


_label_cache = {"key": None, "map": {}}


def label_map() -> dict:
    """{düymə mətni: əməl} — BÜTÜN dillərdə (istifadəçi dilini dəyişsə də köhnə klaviatura işləsin). Hər mesajda
    çağırılır — dillər dəyişməyibsə keşdən verilir."""
    key = tuple(i18n.LANGS)
    if _label_cache["key"] == key:
        return _label_cache["map"]
    out = {}
    for code in list(key) or [i18n.DEFAULT]:
        for action in LABELS:
            out.setdefault(label(action, code), action)
    _label_cache.update(key=key, map=out)
    return out


def keyboard(lang: str | None = None, invite: bool = False) -> ReplyKeyboardMarkup:
    rows = [list(r) for r in LAYOUT] + ([["invite"]] if invite else [])
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label(a, lang)) for a in row] for row in rows],
        resize_keyboard=True, is_persistent=True,
        input_field_placeholder=_("Mahnı adı və ya link yazın...", lang))


# ── 👤 istifadəçi menyusu (şəxsi çat): sahib hansı düymələrin görünəcəyini seçir ──
USER_MENU = {                                  # açar → yazı (sahib panelində); "✖️ Bağla" həmişə qalır
    "codec": "🎛 Audio formatı", "keyboard": "⌨️ Klaviatura", "invite": "🎁 Dəvət et & Premium qazan",
    "groups": "👥 Qruplarım", "premium": "💎 Premium", "search": "🔎 Musiqi axtar", "help": "❓ Əmrlər və bələdçi",
    "lang": "🌐 Dil",
}


def um_on(key: str) -> bool:
    """İstifadəçi menyusunda düymə görünürmü (standart — hamısı görünür)."""
    return _cfg.get(f"um:{key}") != "0"


def set_um(key: str, on: bool):
    if key in USER_MENU:
        _cfg.put(f"um:{key}", "1" if on else "0")


# ── tam üslub: inline menyunun BÜTÜN düymələri reply klaviaturasında ──
def spec_of(b):
    """Inline düymənin əməli: callback / link / inline axtarış (basılanda nə olacağını yadda saxlamaq üçün)."""
    if getattr(b, "callback_data", None):
        return {"cb": b.callback_data}
    if getattr(b, "url", None):
        return {"url": b.url}
    if getattr(b, "switch_inline_query", None) is not None:
        return {"sw": b.switch_inline_query}
    if getattr(b, "switch_inline_query_current_chat", None) is not None:
        return {"swc": b.switch_inline_query_current_chat}
    return None


def full_keyboard(markup, lang: str | None = None):
    """InlineKeyboardMarkup → (ReplyKeyboardMarkup, {düymə mətni: əməl}). Sətir düzülüşü saxlanılır; mətni təkrar olan
    düymə atılır (eyni yazı iki əməlin adı ola bilməz); əməli olmayan (məs. mətn-only) düymə də atılır."""
    rows, mapping = [], {}
    for row in markup.inline_keyboard:
        r = []
        for b in row:
            spec = spec_of(b)
            if spec is None or not b.text or b.text in mapping:
                continue
            mapping[b.text] = spec
            r.append(KeyboardButton(text=b.text))
        if r:
            rows.append(r)
    kb = ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True,
                             input_field_placeholder=_("Mahnı adı və ya link yazın...", lang))
    return kb, mapping


_map_cache = {}


def store_map(uid, mapping: dict):
    """Son göndərilən tam klaviaturanın {mətn: əməl} xəritəsi — restartdan sonra da düymələr işləsin."""
    raw = json.dumps(mapping, ensure_ascii=False)
    _cfg.put(f"map:{uid}", raw)
    _map_cache[uid] = (raw, mapping)


def load_map(uid) -> dict:
    raw = _cfg.get(f"map:{uid}") or ""
    hit = _map_cache.get(uid)
    if hit and hit[0] == raw:
        return hit[1]
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    _map_cache[uid] = (raw, data)
    return data


class BridgeMiddleware(BaseRequestMiddleware):
    """Tam üslubda reply düyməsi basılanda həqiqi callback-i "özümüz" işlədirik (saxta CallbackQuery). Telegram bu
    callback-i tanımır, ona görə:
      • AnswerCallbackQuery (saxta id) — Telegram-a getmir; show_alert mətni adi mesaj kimi göndərilir (itməsin);
      • mesaja toxunan sorğular qeyd olunur → panel redaktə olunmayıbsa, gözləmə mesajı silinir."""
    TOUCH = (EditMessageText, EditMessageCaption, EditMessageMedia, EditMessageReplyMarkup, DeleteMessage)

    def __init__(self):
        self.chats = {}                        # saxta callback id → chat_id
        self.touched = set()
        self._order = deque(maxlen=1000)

    def register(self, cq_id: str, chat_id: int):
        self.chats[cq_id] = chat_id
        if len(self.chats) > 1000:
            for k in list(self.chats)[:500]:
                self.chats.pop(k, None)

    def was_touched(self, chat_id, message_id) -> bool:
        return (chat_id, message_id) in self.touched

    async def __call__(self, make_request, bot, method):
        if isinstance(method, AnswerCallbackQuery) and str(method.callback_query_id).startswith(BRIDGE_PREFIX):
            chat = self.chats.get(method.callback_query_id)
            if method.text and method.show_alert and chat:
                try:
                    await make_request(bot, SendMessage(chat_id=chat, text=method.text))
                except Exception as e:
                    logger.debug("saxta callback xəbərdarlığı göndərilmədi: %r", e)
            return True
        if isinstance(method, self.TOUCH):
            key = (getattr(method, "chat_id", None), getattr(method, "message_id", None))
            if len(self._order) == self._order.maxlen:
                self.touched.discard(self._order[0])
            self._order.append(key)
            self.touched.add(key)
        return await make_request(bot, method)


# ── komanda siyahısı ──
USER_COMMANDS = (
    ("start", lambda l: _("Botu başlat", l)),
    ("menu", lambda l: _("Menyunu aç", l)),
    ("music", lambda l: _("Mahnı axtar", l)),
    ("mix", lambda l: _("Oxşar mahnılar (mahnıya cavab verin)", l)),
    ("shazam", lambda l: _("Mahnını tanı (audioya cavab verin)", l)),
    ("codec", lambda l: _("Audio formatı", l)),
    ("playlist", lambda l: _("Pleylistlərim", l)),
    ("premium", lambda l: _("Premium statusu", l)),
    ("lang", lambda l: _("Dili dəyiş", l)),
    ("keyboard", lambda l: _("Klaviatura düymələrini aç / bağla", l)),
)
CREATOR_COMMANDS = (
    ("depo", lambda l: _("Depo doldurucu", l)),
    ("plugins", lambda l: _("Plugin-lər", l)),
    ("logs", lambda l: _("Loglar", l)),
    ("menubutton", lambda l: _("Menyu düymələri və komanda siyahısı", l)),
    ("restart", lambda l: _("Botu yenidən başlat", l)),
)
_LANG_CODE = re.compile(r"^[a-z]{2}$")


def lang_codes() -> list:
    """Telegram `language_code` yalnız 2 hərfli ISO kodu qəbul edir; standart dil ayrıca (kodsuz) təyin olunur."""
    return [c for c in i18n.LANGS if _LANG_CODE.match(c) and c != i18n.DEFAULT]


def commands(lang: str | None = None, creator: bool = False) -> list:
    items = USER_COMMANDS + (CREATOR_COMMANDS if creator else ())
    return [BotCommand(command=c, description=fn(lang)[:256]) for c, fn in items]


def signature(creator_id: int = 0) -> str:
    """Siyahının imzası: dəyişəndə (komanda, tərcümə, dil əlavə olunması) avto rejim yenidən tətbiq edir."""
    data = {"default": [(c.command, c.description) for c in commands(i18n.DEFAULT)],
            "creator": bool(creator_id),
            "langs": {code: [(c.command, c.description) for c in commands(code)] for code in lang_codes()}}
    return hashlib.sha1(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]


def cmds_mode() -> str:
    return "auto" if _cfg.get(S_MODE) == "auto" else "manual"


def set_cmds_mode(mode: str):
    _cfg.put(S_MODE, "auto" if mode == "auto" else "manual")


def last_hash() -> str:
    return _cfg.get(S_HASH, "") or ""


async def apply_commands(bot, creator_id: int = 0) -> dict:
    """Komanda siyahısını və "Menu" düyməsini təyin edir → {"commands": N, "langs": [...], "hash": ...}."""
    default = BotCommandScopeDefault()
    await bot.set_my_commands(commands(i18n.DEFAULT), scope=default)
    for code in lang_codes():
        await bot.set_my_commands(commands(code), scope=default, language_code=code)
    if creator_id:                                # sahibin söhbətində admin komandaları da görünsün
        own = BotCommandScopeChat(chat_id=creator_id)
        await bot.set_my_commands(commands(i18n.DEFAULT, creator=True), scope=own)
        for code in lang_codes():
            await bot.set_my_commands(commands(code, creator=True), scope=own, language_code=code)
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    sig = signature(creator_id)
    _cfg.put(S_HASH, sig)
    return {"commands": len(USER_COMMANDS), "creator": len(CREATOR_COMMANDS) if creator_id else 0,
            "langs": [i18n.DEFAULT, *lang_codes()], "hash": sig}


async def telegram_state(bot) -> list:
    """Telegram-da hazırda təyin olunmuş (standart əhatə, standart dil) komanda adları; oxunmasa None."""
    try:
        return [c.command for c in await bot.get_my_commands(scope=BotCommandScopeDefault())]
    except Exception:
        return None


def in_sync(current_names, creator_id: int = 0) -> bool:
    """Telegram-dakı siyahı bizimkinə bərabərdirmi (adlara görə) və son tətbiq imzası bugünkü ilə eynidirmi."""
    return current_names == [c for c, _fn in USER_COMMANDS] and last_hash() == signature(creator_id)
