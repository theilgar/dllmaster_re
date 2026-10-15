"""
👥 Qrup paneli — bot/core/group_panel.py

Qrup ayarlarının YEGANƏ mənbəyi: həm saxlama (getter / setter), həm də ekran və düymələr.
Əvvəl eyni ayarlar dörd ayrı ekranda təkrarlanırdı (gmenu, gmenu:settings, pm_grp, mp3_g) — hər biri
öz callback-i və eyni if/elif budaqları ilə. İndi bir panel var; qrupda da, şəxsi çatda da eynidir.

Ayarlar (SETTINGS cədvəli — yeni ayar = bir sətir):
  menu  — qrupda /menu açıqdır                     acc   — menyudan kim istifadə edir (admin / hamı / sahib)
  mp3   — mahnılar MP3 göndərilir                   br    — MP3 bitreyti
  lm    — link rejimi (avto / təsdiq / söndürülü)   rec   — yayım avtomatik qeyd olunur
  txt   — qrupda «/music» yazmadan mətnlə axtarış (bot/core/group_text.py)
  ubinl — userbot inline kartları

Callback: grp:<əməl>:<chat_id>. Köhnə mesajlardakı gmenu:* / mp3_g:* / pm_grp:<əməl>:<chat_id> düymələri də
eyni handler-ə düşür (ALIASES).
"""
import logging
from html import escape

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.core import panel as ui
from common.database import get_db, get_setting, set_setting, is_userbot_inline_disabled, set_userbot_inline_disabled
from common.permissions import is_creator, is_group_admin, is_group_owner
from common.utilities import ui_styled
from common.kv import next_of

logger = logging.getLogger(__name__)

# ── MP3 / bitreyt / link ──
VALID_BITRATES = [64, 128, 192, 256, 320]
DEFAULT_BITRATE = 320
CYCLE_BITRATES = [320, 256, 192, 128, 64]

LINK_MODE_AUTO, LINK_MODE_CONFIRM, LINK_MODE_OFF = "auto", "confirm", "off"
VALID_LINK_MODES = (LINK_MODE_AUTO, LINK_MODE_CONFIRM, LINK_MODE_OFF)
CYCLE_LINK_MODES = list(VALID_LINK_MODES)
LINK_MODE_LABELS = {LINK_MODE_AUTO: "🤖 Avto", LINK_MODE_CONFIRM: "🟡 Təsdiqlə", LINK_MODE_OFF: "🔴 Söndürülü"}

# ── menyu icazəsi ──
MENU_ACCESS_ADMIN, MENU_ACCESS_ALL, MENU_ACCESS_OWNER = "admin", "all", "owner"
MENU_ACCESS_ROLES = [MENU_ACCESS_ADMIN, MENU_ACCESS_ALL, MENU_ACCESS_OWNER]
MENU_ACCESS_LABELS = {MENU_ACCESS_ADMIN: "👮 Yalnız adminlər", MENU_ACCESS_ALL: "👥 Hamı",
                      MENU_ACCESS_OWNER: "👑 Yalnız qrup sahibi"}
MENU_ACCESS_SHORT = {MENU_ACCESS_ADMIN: "👮 Adminlər", MENU_ACCESS_ALL: "👥 Hamı", MENU_ACCESS_OWNER: "👑 Sahib"}

_CTX = None          # setup() təyin edir — 🎙 asistan (context.voice_assistant) üçün


def _cycle(seq, cur):
    return next_of(seq, cur)


def _key(chat_id, name):
    return f"group_mp3:{chat_id}:{name}"


# ───────────────────────── saxlama ─────────────────────────
def is_group_mp3_active(chat_id) -> bool:
    return get_setting(_key(chat_id, "active"), get_setting("group_mp3:default_active", "0")) == "1"


def set_group_mp3_active(chat_id, active: bool):
    set_setting(_key(chat_id, "active"), "1" if active else "0")


def get_group_bitrate(chat_id) -> int:
    try:
        b = int(get_setting(_key(chat_id, "bitrate"), DEFAULT_BITRATE))
    except (TypeError, ValueError):
        return DEFAULT_BITRATE
    return b if b in VALID_BITRATES else DEFAULT_BITRATE


def set_group_bitrate(chat_id, bitrate: int):
    if int(bitrate) in VALID_BITRATES:
        set_setting(_key(chat_id, "bitrate"), str(int(bitrate)))


def get_next_bitrate(current: int) -> int:
    return _cycle(CYCLE_BITRATES, current)


def get_group_link_mode(chat_id) -> str:
    v = get_setting(_key(chat_id, "link_mode")) or get_setting("group_link:default_mode") or LINK_MODE_AUTO
    return v if v in VALID_LINK_MODES else LINK_MODE_AUTO


def set_group_link_mode(chat_id, mode: str):
    if mode in VALID_LINK_MODES:
        set_setting(_key(chat_id, "link_mode"), mode)


def get_next_link_mode(current: str) -> str:
    return _cycle(CYCLE_LINK_MODES, current)


def is_group_text_search(chat_id) -> bool:
    """🔎 Qrupda «/music» yazmadan mətnlə axtarış (bot/core/group_text.py) — hər qrup üçün ayrıca, defolt bağlı."""
    return get_setting(_key(chat_id, "text_search"), "0") == "1"


def set_group_text_search(chat_id, on: bool):
    set_setting(_key(chat_id, "text_search"), "1" if on else "0")


def is_group_menu_enabled(chat_id) -> bool:
    v = get_setting(f"group_menu:{chat_id}:enabled")
    return (v if v is not None else (get_setting("group_menu:enabled") or "1")) == "1"


def set_group_menu_enabled(chat_id, enabled: bool):
    set_setting(f"group_menu:{chat_id}:enabled", "1" if enabled else "0")


def get_group_menu_access(chat_id) -> str:
    v = get_setting(f"group_menu:{chat_id}:access") or get_setting("group_menu:access")
    return v if v in MENU_ACCESS_ROLES else MENU_ACCESS_ADMIN


def cycle_group_menu_access(chat_id) -> str:
    nxt = _cycle(MENU_ACCESS_ROLES, get_group_menu_access(chat_id))
    set_setting(f"group_menu:{chat_id}:access", nxt)
    return nxt


def active_groups() -> list:
    """Bazadakı aktiv qrup / superqruplar: [{"chat_id", "title"}, ...]."""
    try:
        with get_db()._conn() as c:
            return c.execute("SELECT chat_id, title FROM chats WHERE active=1 "
                             "AND type IN ('group', 'supergroup')").fetchall()
    except Exception as e:
        logger.warning(f"qrup siyahısı oxunmadı: {e}")
        return []


# ── 🌐 bütün qruplar (yalnız bot sahibi) — standart dəyər + hər qrupa yazılır ──
def all_groups_set(kind: str, value) -> int:
    rows = active_groups()
    if kind == "menu":
        set_setting("group_menu:enabled", "1" if value else "0")
        for r in rows:
            set_group_menu_enabled(r["chat_id"], value)
    elif kind == "acc":
        if value not in MENU_ACCESS_ROLES:
            return 0
        set_setting("group_menu:access", value)
        for r in rows:
            set_setting(f"group_menu:{r['chat_id']}:access", value)
    elif kind == "mp3":
        set_setting("group_mp3:default_active", "1" if value else "0")
        for r in rows:
            set_group_mp3_active(r["chat_id"], value)
    elif kind == "lm":
        if value not in VALID_LINK_MODES:
            return 0
        set_setting("group_link:default_mode", value)
        for r in rows:
            set_group_link_mode(r["chat_id"], value)
    elif kind == "rec":
        from bot.core import va_record
        d = va_record.auto_chats()
        for r in rows:
            if value:
                d[int(r["chat_id"])] = r["title"] or str(r["chat_id"])
            else:
                d.pop(int(r["chat_id"]), None)
        va_record.save_auto_chats(d)
    return len(rows)


# ───────────────────────── 🎙 yayım qeydi ─────────────────────────
def _rec():
    va = getattr(_CTX, "voice_assistant", None)
    return va.rec if va is not None and getattr(va, "rec", None) is not None else None


def _rec_desc(chat_id) -> str:
    from bot.core import va_record
    rec = _rec()
    sess = rec.sessions.get(chat_id) if rec else None
    if sess:
        from common.utilities import format_clock
        return f"🔴 qeyd gedir ({format_clock(sess.now())})"
    return "🤖 avto (yayım başlayan kimi)" if va_record.auto_on(chat_id) else "✋ manual (/vrec ilə)"


async def _toggle_rec(chat_id, title) -> str:
    from bot.core import va_record
    on = not va_record.auto_on(chat_id)
    va_record.set_auto(chat_id, title, on)
    rec = _rec()
    if on and rec and not rec.active(chat_id) and await rec.call_active(chat_id):
        rec._spawn(rec.on_call_started(chat_id, title))
    return "⏺ Qeyd: 🤖 Avto" if on else "⏺ Qeyd: ✋ Manual"


# ───────────────────────── ayarlar cədvəli ─────────────────────────
async def _t_menu(cid, title):
    on = not is_group_menu_enabled(cid)
    set_group_menu_enabled(cid, on)
    return "🟢 Qrup menyusu aktiv" if on else "🔴 Qrup menyusu deaktiv"


async def _t_acc(cid, title):
    return f"👥 İcazə: {MENU_ACCESS_LABELS[cycle_group_menu_access(cid)]}"


async def _t_mp3(cid, title):
    on = not is_group_mp3_active(cid)
    set_group_mp3_active(cid, on)
    return "🟢 MP3 aktiv" if on else "🔴 MP3 deaktiv"


async def _t_br(cid, title):
    b = get_next_bitrate(get_group_bitrate(cid))
    set_group_bitrate(cid, b)
    return f"🎚 {b} kbps"


async def _t_lm(cid, title):
    m = get_next_link_mode(get_group_link_mode(cid))
    set_group_link_mode(cid, m)
    return f"🔗 Link: {LINK_MODE_LABELS[m]}"


async def _t_txt(cid, title):
    on = not is_group_text_search(cid)
    set_group_text_search(cid, on)
    if not on:
        return "🔎 Mətnlə axtarış: 🔴 söndürüldü"
    note = "🔎 Mətnlə axtarış: 🟢 açıq — mahnı adını yazmaq kifayətdir"
    bot = getattr(_CTX, "bot", None)
    try:                                          # bot adi mesajları görürmü? (admin / Group Privacy söndürülüb)
        me = await bot.me()
        if not getattr(me, "can_read_all_group_messages", False):
            m = await bot.get_chat_member(cid, me.id)
            if str(getattr(m.status, "value", m.status)) not in ("administrator", "creator"):
                note += " ⚠️ Bot admin deyil — adi mesajları görməyə bilər"
    except Exception as e:
        logger.debug("mətn axtarışı: bot hüquqları yoxlanmadı: %r", e)
    return note


async def _t_ubinl(cid, title):
    off = not is_userbot_inline_disabled(cid)
    set_userbot_inline_disabled(cid, off)
    return "📱 UB inline: 🔴 söndürüldü" if off else "📱 UB inline: 🟢 açıq"


def _rec_auto(cid):
    from bot.core import va_record
    return va_record.auto_on(cid)


# key → (düymə mətni, dəyişdirici, yalnız sahib (menyu "sahib" rejimindədirsə))
SETTINGS = {
    "menu":  (lambda c: ui.toggle("📋 Menyu", is_group_menu_enabled(c), ""), _t_menu, True),
    "acc":   (lambda c: ui.btn(f"👥 İcazə: {MENU_ACCESS_SHORT[get_group_menu_access(c)]}", ""), _t_acc, True),
    "mp3":   (lambda c: ui.toggle("🎧 MP3", is_group_mp3_active(c), ""), _t_mp3, False),
    "br":    (lambda c: ui.btn(f"🎚 {get_group_bitrate(c)} kbps", ""), _t_br, False),
    "lm":    (lambda c: ui.btn(f"🔗 Link: {LINK_MODE_LABELS[get_group_link_mode(c)]}", ""), _t_lm, False),
    "rec":   (lambda c: ui.mode("⏺ Qeyd", _rec_auto(c), ""), None, False),       # _toggle_rec (başlığa ehtiyac)
    "txt":   (lambda c: ui.toggle("🔎 Mətnlə axtarış", is_group_text_search(c), ""), _t_txt, False),
    "ubinl": (lambda c: ui.toggle("📱 UB inline", not is_userbot_inline_disabled(c), ""), _t_ubinl, False),
}

# köhnə (gmenu: / mp3_g: / pm_grp:) callback adları → yeni əməl
ALIASES = {
    "toggle_enable": "menu", "toggle_enable_st": "menu", "cycle_access": "acc", "cycle_access_st": "acc",
    "toggle_mp3": "mp3", "toggle_mp3_st": "mp3", "toggle": "mp3", "cycle_br": "br", "cycle_br_st": "br",
    "cycle_lm": "lm", "cycle_lm_st": "lm", "vrec_toggle": "rec", "vrec_toggle_st": "rec", "toggle_vrec": "rec",
    "toggle_ubinl": "ubinl", "toggle_ubinl_st": "ubinl",
    "settings": "view", "sel": "view", "back": "view", "panel": "view", "mp3": "view",
    "vrec": "recp", "rec": "recp", "uback": "uview", "uhelp": "uview",
}


def _setting_btn(key, cid):
    b = SETTINGS[key][0](cid)
    return InlineKeyboardButton(text=b.text, callback_data=f"grp:{key}:{cid}")


async def panel_view(bot, chat_id: int, user_id: int, title: str = None, private: bool = False):
    """Bütün qrup ayarları bir ekranda: xülasə + açarlar. private=True — şəxsi çatdan idarə (⬅️ Qruplar)."""
    if not title:
        title = "Qrup"
        try:
            title = (await bot.get_chat(chat_id)).title or title
        except Exception:
            row = get_db().get_chat(chat_id)
            title = (row or {}).get("title") or title
    if is_creator(user_id):
        role = "👑 bot sahibi"
    elif await is_group_owner(bot, chat_id, user_id):
        role = "👑 qrup sahibi"
    else:
        role = "👮 admin"
    text = (
        f"👥 <b>{escape(title)}</b> — qrup ayarları\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"• Sizin rolunuz: <b>{role}</b>\n"
        f"• Menyu: <b>{ui.state(is_group_menu_enabled(chat_id))}</b> · "
        f"{MENU_ACCESS_LABELS[get_group_menu_access(chat_id)]}\n"
        f"• MP3: <b>{ui.state(is_group_mp3_active(chat_id))}</b> · {get_group_bitrate(chat_id)} kbps\n"
        f"• Linklə yükləmə: <b>{LINK_MODE_LABELS[get_group_link_mode(chat_id)]}</b>\n"
        f"• Mətnlə axtarış (/music-süz): <b>{ui.state(is_group_text_search(chat_id))}</b>\n"
        f"• Yayım qeydi: <b>{_rec_desc(chat_id)}</b>\n"
        f"• Userbot inline: <b>{ui.state(not is_userbot_inline_disabled(chat_id))}</b>"
    )
    rows = ui.grid([_setting_btn(k, chat_id) for k in SETTINGS], 2)
    rows.append([ui.btn("⏺ Yayım qeydi paneli", f"grp:recp:{chat_id}"),
                 ui.btn("❓ Qrup əmrləri", f"grp:help:{chat_id}")])
    if private:
        rows.append([ui.back("umenu:groups", "Qruplar"), ui.close("umenu:close")])
    else:
        rows.append([InlineKeyboardButton(text="🔎 Musiqi axtar", switch_inline_query_current_chat=""),
                     ui.close("grp:close")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _uname(bot) -> str:
    try:
        return (await bot.me()).username or "dllmasterbot"
    except Exception:
        return "dllmasterbot"


async def user_view(bot, chat_id: int, title: str):
    """Adi üzv üçün (menyu "hamı" rejimində): yalnız musiqi bələdçisi."""
    un = await _uname(bot)
    text = (
        f"🎵 <b>{escape(title)}</b> — musiqi\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "• <code>/song Mahnı adı</code> — mahnını qrupa göndərir\n"
        "• <code>/play Mahnı adı</code> — səsli söhbətdə oxudur\n"
        f"• <code>@{un} Mahnı</code> — inline axtarış\n"
        "• YouTube / Spotify linki göndərin — bot yükləyir\n"
        "• Səsə reply edib <code>/shazam</code> — mahnını tanıyır"
    )
    kb = ui.kb([InlineKeyboardButton(text="🔎 Musiqi axtar", switch_inline_query_current_chat="")],
               [ui.url_btn("🎧 Şəxsi ayarlar", f"https://t.me/{un}?start=mp3"), ui.close("grp:close")])
    return text, kb


async def help_view(bot, chat_id: int, title: str, private: bool = False):
    un = await _uname(bot)
    text = (
        f"❓ <b>{escape(title)}</b> — qrup əmrləri\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎵 <code>/play</code> · <code>/song</code> · <code>/shazam</code> · "
        f"<code>@{un} mahnı</code>\n"
        "⏯ <code>/pause</code> · <code>/resume</code> · <code>/skip</code> · <code>/stop</code> · "
        "<code>/queue</code> · <code>/clear</code>\n"
        "⚙️ <code>/menu</code> — bu panel · <code>/vrec</code> — yayım qeydi · "
        "<code>/mp3 on|off|320|link auto</code>\n"
        "🔎 Admin paneldə «Mətnlə axtarış»ı açsa, <code>/music</code> yazmadan mahnı adı yazmaq kifayətdir."
    )
    return text, ui.kb([ui.back(f"grp:view:{chat_id}", "Qrup ayarları"),
                        ui.close("umenu:close" if private else "grp:close")])


# ───────────────────────── callback ─────────────────────────
_PM_GRP_LOCAL = ("input_id", "page", "all_panel", "all_menu", "all_acc", "all_mp3", "all_vrec", "all_lm")


def _is_ours(data: str) -> bool:
    prefix, _, rest = data.partition(":")
    if prefix in ("grp", "gmenu", "mp3_g"):
        return True
    return prefix == "pm_grp" and rest.split(":", 1)[0] not in _PM_GRP_LOCAL


async def _show(cb, text, kb):
    with ui_styled("admin"):
        await ui.show(cb, text, kb)


async def handle(cb: CallbackQuery):
    parts = cb.data.split(":")
    act = parts[1] if len(parts) > 1 else ""
    if parts[0] != "grp":
        act = ALIASES.get(act, act)
    if act == "close":
        await cb.answer()
        if cb.message:
            try:
                await cb.message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/core/group_panel.py:360]: %r", _e)
        return
    try:
        chat_id = int(parts[2])
    except (IndexError, ValueError):
        await cb.answer()
        return
    msg = cb.message
    private = bool(msg and msg.chat.type == "private")
    title = None if private or not msg else (msg.chat.title or "Qrup")
    uid = cb.from_user.id

    if act == "uview":
        await cb.answer()
        await _show(cb, *(await user_view(cb.bot, chat_id, title or "Qrup")))
        return
    if not await is_group_admin(cb.bot, chat_id, uid):
        await cb.answer("👮 Bu ayarı yalnız qrup adminləri idarə edə bilər!", show_alert=True)
        return

    if act in SETTINGS:
        if SETTINGS[act][2] and get_group_menu_access(chat_id) == MENU_ACCESS_OWNER \
                and not await is_group_owner(cb.bot, chat_id, uid):
            await cb.answer("👑 Bu ayarı yalnız qrup sahibi dəyişə bilər!", show_alert=True)
            return
        if act == "rec":
            note = await _toggle_rec(chat_id, title or "Qrup")
        else:
            note = await SETTINGS[act][1](chat_id, title)
        await cb.answer(note)
        await _show(cb, *(await panel_view(cb.bot, chat_id, uid, title, private)))
    elif act == "view":
        await cb.answer()
        await _show(cb, *(await panel_view(cb.bot, chat_id, uid, title, private)))
    elif act == "help":
        await cb.answer()
        await _show(cb, *(await help_view(cb.bot, chat_id, title or "Qrup", private)))
    elif act == "recp":
        rec = _rec()
        if rec is None:
            await cb.answer("⚠️ Canlı yayım asistanı aktiv deyil!", show_alert=True)
            return
        await cb.answer()
        if not title:
            try:
                title = (await cb.bot.get_chat(chat_id)).title
            except Exception:
                title = None
        text, kb = await rec.panel(chat_id, title or str(chat_id), from_menu=True, owner=is_creator(uid))
        await _show(cb, text, kb)
    else:
        await cb.answer()


def setup(context):
    global _CTX
    _CTX = context
    if not getattr(context, "_group_panel_on", False):          # təkrar çağırışda handler ikiləşməsin
        context.dp.callback_query(F.data.func(_is_ours))(handle)
        context._group_panel_on = True
