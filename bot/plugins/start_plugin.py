from aiogram import types, Router, Bot, F
from aiogram.filters import Command
from aiogram.filters.chat_member_updated import (
    ChatMemberUpdatedFilter,
    JOIN_TRANSITION,
    ADMINISTRATOR,
    MEMBER,
    RESTRICTED,
)
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ChatAdministratorRights,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramMigrateToChat
from html import escape
import asyncio
import contextlib
import json
import logging
import os
import time

from common.database import get_db, is_creator
from common import i18n, tiers
from common.i18n import _ as tr                       # 🌐 "_" bu faylda dəyişən adı kimi də işlənir
from bot.core.welcome import send_welcome, send_media, get_text_template, render_template, ui_styled
from bot.core.stars import plan_offers, plans_for_sale, per_month, buy_keyboard, TIP_AMOUNTS, sale_active, sale_settings
from common.formatting import fmt_date
from common.utilities import safe_delete, spawn

logger = logging.getLogger(__name__)
router = Router()
CREATOR_USERNAME = os.getenv("CREATOR_USERNAME", "jabrailzade_i").lstrip("@")

# Botun qrupda işləməsi üçün lazım olan yetkilər: (açar, göstəriləcək ad)
# "admin" — media göndərmə üçün: adminlər qrupun "üzvlər media göndərə bilməz" məhdudiyyətindən asılı olmur.
REQUIRED_RIGHTS = [
    ("admin", "🛡 Admin olmaq (media göndərmə)"),
    ("can_delete_messages", "🗑 Mesaj silmə"),
    ("can_pin_messages", "📌 Mesaj pinləmə"),
]
# t.me/<bot>?startgroup=true&admin=... linkində istənilən yetkilər (Telegram özü təsdiq dialoqu açır)
ADMIN_LINK_RIGHTS = "delete_messages+pin_messages"

# Eyni çatda eyni çatışmazlıq bildirişini təkrar göndərməmək üçün
_last_notice = {}
# Qrupda yazma yetkisi olmadığı üçün salamı şəxsidə göndərilən qruplar —
# yetki veriləndə salam qrupun özünə də göndərilir
_pending_welcome = set()
# Qrupda göndərilən salam / yetki bildirişlərinin ID-ləri (yetki verildikdən sonra silinmək üçün)
_welcome_msgs = {}


def remember_rights_msg(chat_id: int, message_id: int):
    _welcome_msgs.setdefault(chat_id, set()).add(message_id)
    try:
        key = f"welcome_msg:{chat_id}"
        cur = set(json.loads(get_db().get_setting(key) or "[]"))
        cur.add(message_id)
        get_db().set_setting(key, json.dumps(list(cur)))
    except Exception as _e:
        logger.debug("udulan xəta [bot/plugins/start_plugin.py:65]: %r", _e)


def get_and_clear_rights_msgs(chat_id: int) -> set:
    out = set(_welcome_msgs.pop(chat_id, set()))
    try:
        key = f"welcome_msg:{chat_id}"
        from_db = set(json.loads(get_db().get_setting(key) or "[]"))
        out.update(from_db)
        get_db().set_setting(key, "[]")
    except Exception as _e:
        logger.debug("udulan xəta [bot/plugins/start_plugin.py:76]: %r", _e)
    return out


async def delete_old_rights_messages(bot: Bot, chat_id: int, username: str = None):
    """Yetkilər verildikdə qrupdakı köhnə salam / yetki bildirişlərini silir."""
    # 1) Qeydə alınmış mesaj ID-lərini bot vasitəsilə sil
    msg_ids = get_and_clear_rights_msgs(chat_id)
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id, mid)
            logger.info(f"🗑 Köhnə yetki mesajı silindi: chat={chat_id}, msg={mid}")
        except Exception as e:
            logger.debug(f"Köhnə mesaj silinmədi ({chat_id}/{mid}): {e}")

    # 2) Əgər userbot aktivdirsə, çatda qalmış köhnə salam/yetki mesajlarını da təmizlə
    client = getattr(_CTX, "telethon_client", None)
    if client and client.is_connected():
        try:
            uname = (username or (await bot.me()).username or "").lower().lstrip("@")
            async for m in client.iter_messages(chat_id, limit=8):
                snd = getattr(m, "sender", None)
                snd_uname = (getattr(snd, "username", "") or "").lower().lstrip("@")
                if snd_uname and snd_uname == uname:
                    text_low = (m.text or "").lower()
                    if any(k in text_low for k in ("salam, qrup", "lazımi yetkilər", "çatışmayan yetkilər", "admin yetkiləri verin")):
                        await m.delete()
                        logger.info(f"🗑 Userbot tərəfindən köhnə yetki mesajı silindi: chat={chat_id}, msg={m.id}")
        except Exception as e:
            logger.debug(f"Userbot axtarış/silmə xətası ({chat_id}): {e}")


_CTX = None


async def setup(context):
    global _CTX
    _CTX = context
    context.main_router.include_router(router)

    # "Qrupa əlavə et" düyməsi ilə admin kimi əlavə edilərkən Telegram bu yetkiləri təklif edəcək
    try:
        await context.bot.set_my_default_administrator_rights(
            rights=ChatAdministratorRights(
                is_anonymous=False,
                can_manage_chat=True,
                can_delete_messages=True,
                can_manage_video_chats=False,
                can_restrict_members=False,
                can_promote_members=False,
                can_change_info=False,
                can_invite_users=False,
                can_post_stories=False,
                can_edit_stories=False,
                can_delete_stories=False,
                can_send_welcome_messages=False,
                can_pin_messages=True,
                can_manage_topics=True,
            ),
            for_channels=False,
        )
        logger.info("✅ Default admin yetkiləri təyin edildi (silmə, pinləmə)")
    except Exception as e:
        logger.warning(f"Default admin yetkiləri təyin edilmədi: {e}")

    async def show_start(chat_id: int, user):
        """🌐 Dil dəyişəndə salam mesajını yeni dildə yenidən göstər (lang_plugin çağırır)."""
        username = (await context.bot.me()).username
        text = await asyncio.to_thread(build_private_text, user.first_name or "🙂", username, user.id)
        await send_welcome(context.bot, chat_id, text, private_keyboard(username, user.id))

    context.show_start = show_start
    logger.info("✅ Start plugin yükləndi")


# ───────────────────────── köməkçilər ─────────────────────────
def _fmt_date(ts: int) -> str:
    return fmt_date(ts, "%d.%m.%Y")


def status_line(user_id: int) -> str:
    """İstifadəçinin roluna görə start mesajındakı status sətri."""
    if is_creator(user_id):
        return tr("👑 <b>Creator</b> — idarə paneli: /menu")
    try:
        cur = get_db().get_premium(user_id)
    except Exception as e:
        logger.warning(f"Premium yoxlanışı xətası: {e}")
        cur = None
    clean_perk = not tiers.get("songlink", "premium") and tiers.get("songlink", "free")
    if cur:
        until = tr("ömürlük") if cur["until"] is None else tr("{date}-dək", date=_fmt_date(cur["until"]))
        if clean_perk:
            return tr("💎 <b>Premium</b> ({until}) — mahnılar yazısız gəlir", until=until)
        return tr("💎 <b>Premium</b> ({until})", until=until)
    if not tiers.perks():                  # Premium heç nə artıq vermir — reklam etmə
        return ""
    if clean_perk:
        return tr("💎 Premium ilə mahnılar \"via @dllmasterbot\" yazısı olmadan gəlir → /premium")
    return tr("💎 Premium ilə daha çox imkan → /premium")


# ───────────────────────── Default mətn şablonları ─────────────────────────
# /menu → 📝 Mesajlar ilə dəyişilə bilər; {dəyişənlər} göndəriləndə əvəz olunur.
DEFAULT_TEXTS = {
    "welcome": (
        "<b>Salam {name}! 👋 Mən DLLMaster Bot 🎵</b>\n\n"
        "🎧 <b>Nə edə bilərəm?</b>\n"
        "🔎 Mahnı adını yaz və ya <code>/music ad</code> — axtarıb yükləyirəm\n"
        "🔗 YouTube / Spotify linki, playlisti və ya albomu at\n"
        "⚡ İstənilən çatda: <code>@{bot} mahnı adı</code>\n"
        "🔀 <code>/mix ad</code> və ya mahnının altındakı <b>Mix</b> — oxşar mahnılar\n"
        "⏹ Yükləməni istənilən an dayandıra bilərsən\n"
        "🎙 <code>/artist ad</code> — ifaçının bütün mahnıları\n"
        "🎶 <code>/playlist</code> — sevdiyin mahnıları öz playlistlərinə yığ\n"
        "🎁 <code>/invite</code> — dostlarını dəvət et, pulsuz Premium qazan\n"
        "✏️ Mənə audio göndər — adını, artistini və şəklini dəyişim\n\n"
        "{status}\n\n"
        "Bot @{creator} tərəfindən yaradılmışdır 🚀"
    ),
    "group": (
        "<b>Salam, {group}! 👋</b>\n\n"
        "{thanks}Mən <b>DLLMaster Bot</b> 🎵\n\n"
        "🎧 <b>Qrupda necə istifadə etmək olar?</b>\n"
        "🔎 <code>/music mahnı adı</code> — axtarıb yükləyirəm\n"
        "⚡ <code>@{bot} mahnı adı</code> — bir toxunuşla göndər\n"
        "🔗 YouTube / Spotify playlist, albom və ya track linki\n"
        "🔀 <code>/mix mahnı adı</code> — oxşar mahnılar\n\n"
        "{rights}\n\n"
        "Bot @{creator} tərəfindən yaradılmışdır 🚀"
    ),
    "download": (
        "🎵 <b>Mahnı Necə Yüklənir?</b>\n\n"
        "1️⃣ <b>Sadə Adla Axtarış:</b>\n"
        "   • Mahnının adını yazın: <code>Miri Yusif Bağışla</code> və ya <code>\"Röya Qayıt\"</code>\n"
        "   • Və ya <code>/song Mahnı adı</code> yazın.\n\n"
        "2️⃣ <b>Link ilə Birbaşa Yükləmə:</b>\n"
        "   • YouTube, Spotify, Apple Music track/albom və ya playlist linkini göndərin.\n\n"
        "3️⃣ <b>Canlı Səsli Söhbət:</b>\n"
        "   • Qruplarda <code>/play Mahnı adı</code> yazaraq canlı yayımda oxuda bilərsiniz 🎙\n\n"
        "4️⃣ <b>Tövsiyələr & Oxşar Mahnılar:</b>\n"
        "   • <code>/mix Mahnı adı</code> və ya mahnının altındakı <b>Mix</b> düyməsi ilə oxşar trekləri kəşf edin."
    ),
    "inline": (
        "⚡ <b>İnline Axtarış Bələdçisi</b>\n\n"
        "Botu heç bir qrupa əlavə etmədən, <b>istənilən çatda</b> dostlarınızla mahnı paylaşa bilərsiniz:\n\n"
        "1️⃣ Mesaj yazılan yerdə botun adını və mahnını yazın:\n"
        "   👉 <code>@{bot} Mahnı adı</code>\n"
        "2️⃣ Bir saniyə gözləyin, açılan siyahıdan istədiyiniz mahnını seçin.\n"
        "3️⃣ Mahnı həmin an bir toxunuşla çata göndəriləcək! 🎧\n\n"
        "👇 <b>Elə indi aşağıdakı düymə ilə sınayın:</b>"
    ),
    "premium": (
        "💎 <b>DLLMaster Premium</b>\n\n"
        "✨ <b>Premium nə verir?</b>\n"
        "{perks}\n\n"
        "⚡ Telegram Stars ilə bir neçə saniyəyə aktivləşir\n"
        "🔁 Aktiv premiumu uzatmaq olar — müddət üstünə əlavə olunur\n\n"
        "{status}\n\n"
        "{plans}"
    ),
    "tip": (
        "⭐ <b>Bəxşiş göndər</b>\n\n"
        "DLLMaster-i bəyənirsənsə, əməyə dəstək olaraq Telegram Stars ilə bəxşiş göndərə bilərsən 💙\n\n"
        "Bəxşişlər serverin, depo kanalının və yeni funksiyaların xərclərini qarşılamağa kömək edir.\n\n"
        "👇 <b>Məbləği seç və ya öz məbləğini yaz:</b>"
    ),
    "edit": (
        "✏️ <b>Audio Redaktoru və Tag Dəyişdirilməsi</b>\n\n"
        "Mahnının adını, ifaçısını və ya albom şəklini bir neçə saniyəyə dəyişə bilərsiniz:\n\n"
        "1️⃣ Mənə istənilən <b>Audio faylı (.mp3, .m4a)</b> göndərin.\n"
        "2️⃣ Açılan interaktiv paneldən seçim edin:\n"
        "   • ✏️ <b>Ad</b> — Mahnının başlığını redaktə edin\n"
        "   • 🎤 <b>Artist</b> — İfaçı adını qeyd edin\n"
        "   • 🖼 <b>Örtük şəkli</b> — Mahnıya yeni şəkil əlavə edin\n"
        "3️⃣ <b>✅ Təsdiqlə</b> düyməsinə basın — yenilənmiş audio fayl dərhal sizə göndəriləcək!\n\n"
        "<i>ℹ️ Maksimum fayl həcmi: 50 MB (Telegram limiti).</i>"
    ),
    "invite": (
        "🎁 <b>Dostlarını dəvət et — Pulsuz Premium qazan!</b>\n\n"
        "Dəvət etdiyin hər <b>1 dostun</b> üçün sənə 💎 <b>Premium</b> hədiyyə olunur!\n\n"
        "🔗 Öz xüsusi linkini almaq və statistikana baxmaq üçün:\n"
        "👉 <code>/invite</code> və ya aşağıdakı düyməyə bas."
    ),
}


def text_template(slot: str) -> str:
    """Creator-un /menu-dan yazdığı şablon, yoxdursa default. 🌐 Azərbaycanca olmayan istifadəçiyə —
    hazır tərcümə (common/i18n.py TEMPLATES); creator-un şablonu Azərbaycanca istifadəçilərə gedir."""
    lang = i18n.current()
    if lang != "az":
        t = i18n.template(slot, lang)
        if t:
            return t
    return get_text_template(slot) or DEFAULT_TEXTS[slot]


def tidy(text: str) -> str:
    """Gizlədilən sətirlərdən qalan artıq boş sətirləri yığışdırır."""
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def build_private_text(first_name: str, bot_username: str, user_id: int) -> str:
    text = render_template(text_template("welcome"), {
        "name": escape(first_name),
        "bot": escape(bot_username),
        "status": status_line(user_id),
        "creator": CREATOR_USERNAME,
    })
    from common.database import is_invite_enabled
    if not is_invite_enabled():
        lines = [ln for ln in text.split("\n") if "/invite" not in ln]
        text = "\n".join(lines)
    text = tidy(tiers.strip_hidden(text, user_id))      # 🎛 bağlı imkanların sətirləri görünmür
    banner = sale_banner(user_id)
    return f"{text}\n\n{banner}" if banner else text


def sale_banner(user_id: int) -> str:
    """🏷 Aktiv endirim varsa /start-da bir sətirlik elan (ömürlük premiumu və creator-u maraqlandırmır)."""
    try:
        if not sale_active() or is_creator(user_id):
            return ""
        cur = get_db().get_premium(user_id)
        if cur and cur["until"] is None:
            return ""
        s = sale_settings()
        line = tr("🔥 <b>Premium endirimi -{pct}%</b>", pct=s["pct"])
        if s["end"]:
            from datetime import datetime
            from common.formatting import get_tz
            line += " · " + tr("{date} tarixinədək", date=datetime.fromtimestamp(s["end"], get_tz()).strftime("%d.%m.%Y %H:%M"))
        return line
    except Exception:
        return ""


def depo_rows() -> list:
    """📦 Depo kanalı düyməsi — /menu → 📦 Depo → 📢 Depo kanalı ilə idarə olunur."""
    try:
        from bot.core import audio_cache
        pub = audio_cache.depo_public()
    except Exception:
        return []
    if not pub.get("show") or not pub.get("link"):
        return []
    return [[InlineKeyboardButton(text=tr("📦 Depo kanalı — bütün mahnılar"), url=pub["link"])]]


def private_keyboard(bot_username: str, user_id: int = None) -> InlineKeyboardMarkup:
    """🎛 İmkanlar və /menu → 🔘 Start düymələri ayarlarına əsasən düymələr qurulur."""
    from common.database import is_invite_enabled, is_start_button_enabled

    def vis(feature):
        try:
            return tiers.visible(feature, user_id)
        except Exception:
            return True

    def mark(feature, text):
        return text + (" 💎" if tiers.locked(feature, user_id) else "")

    rows = []

    # 1. Mahnı yüklə & İnline axtarış
    r1 = []
    if is_start_button_enabled("download"):
        r1.append(InlineKeyboardButton(text=tr("🎵 Mahnı yüklə"), callback_data="sg:download"))
    if is_start_button_enabled("inline"):
        r1.append(InlineKeyboardButton(text=tr("🔎 İnline axtarış"), callback_data="sg:inline"))
    if r1:
        rows.append(r1)

    # 2. Metadata redaktə, Playlistlər, Dəvət sistemi, Premium
    extra = []
    if is_start_button_enabled("audio_edit") and vis("audio_edit"):
        extra.append(InlineKeyboardButton(text=mark("audio_edit", tr("✏️ Metadata redaktə")), callback_data="sg:edit"))
    if is_start_button_enabled("playlists") and vis("playlists"):
        extra.append(InlineKeyboardButton(text=mark("playlists", tr("🎶 Playlistlərim")), callback_data="pl:open"))
    if is_start_button_enabled("invite") and is_invite_enabled():
        extra.append(InlineKeyboardButton(text=tr("🎁 Dəvət et"), callback_data="sg:invite"))
    try:
        sell = bool(tiers.perks()) and not is_creator(user_id)
    except Exception:
        sell = True
    if is_start_button_enabled("premium") and sell:
        extra.append(InlineKeyboardButton(
            text=(tr("🔥 Premium -{pct}%", pct=sale_settings()["pct"]) if sale_active() else tr("💎 Premium al")),
            callback_data="sg:premium"))
    for i in range(0, len(extra), 2):
        rows.append(extra[i:i + 2])

    # 3. Bəxşiş & Dil seçimi
    r3 = []
    if is_start_button_enabled("tip"):
        r3.append(InlineKeyboardButton(text=tr("⭐ Bəxşiş göndər"), callback_data="sg:tip"))
    if is_start_button_enabled("lang"):
        r3.append(InlineKeyboardButton(text="🌐 " + i18n.LANGS[i18n.current()].split(" ", 1)[1], callback_data="lang:menu"))
    if r3:
        rows.append(r3)

    # 4. Depo kanalı
    if is_start_button_enabled("depo"):
        rows.extend(depo_rows())

    # 5. Qrupa əlavə et
    if is_start_button_enabled("add_group"):
        rows.append([InlineKeyboardButton(text=tr("🤖 Botu qrupa əlavə et"), url=admin_request_url(bot_username))])

    # 6. Ləğv et
    if is_start_button_enabled("close"):
        rows.append([InlineKeyboardButton(text=tr("✖️ Ləğv et"), callback_data="sg:close")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def nav_row() -> list:
    """Bələdçilərin altındakı naviqasiya: salam mesajına qayıt / bağla."""
    return [InlineKeyboardButton(text=tr("⬅️ Geri"), callback_data="sg:back"),
            InlineKeyboardButton(text=tr("✖️ Ləğv et"), callback_data="sg:close")]


# ───────────────────────── Bələdçilər (start düymələri) ─────────────────────────
# Hər bələdçinin mediası /menu → 🖼 Media bölməsindən dəyişilir (bot/core/welcome.py slot-ları)
def guide_text(slot: str, bot_username: str, user_id: int = None) -> str:
    if slot not in ("download", "inline", "edit"):
        raise ValueError(slot)
    text = render_template(text_template(slot), {"bot": escape(bot_username), "creator": CREATOR_USERNAME})
    return tidy(tiers.strip_hidden(text, user_id))


def guide_keyboard(slot: str) -> InlineKeyboardMarkup:
    rows = []
    if slot == "inline":
        rows += [
            [InlineKeyboardButton(text=tr("🔎 Burada sına"), switch_inline_query_current_chat="")],
            [InlineKeyboardButton(text=tr("📤 Başqa çatda istifadə et"), switch_inline_query="")],
        ]
    rows.append(nav_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def can_close(callback: CallbackQuery) -> bool:
    """Şəxsi çatda hər kəs; qrupda yalnız adminlər və creator bağlaya bilər."""
    msg = callback.message
    if not msg or msg.chat.type == "private" or is_creator(callback.from_user.id):
        return True
    try:
        m = await callback.bot.get_chat_member(msg.chat.id, callback.from_user.id)
        return m.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
    except Exception:
        return False


async def delete_quietly(message):
    await safe_delete(message)


@router.callback_query(F.data.startswith("sg:"))
async def start_guide(callback: CallbackQuery):
    """
    Start düymələri. Hər dəfə yeni mesaj göndərilir və köhnəsi silinir — çatda bir mesaj qalır:
      sg:download / sg:inline / sg:edit → bələdçi (salam mesajının yerinə)
      sg:back  → bələdçidən salam mesajına qayıt
      sg:close → mesajı bağla
    """
    action = callback.data.split(":", 1)[1]
    bot = callback.bot
    msg = callback.message
    chat_id = msg.chat.id if msg else callback.from_user.id

    if action == "close":
        if not await can_close(callback):
            await callback.answer(tr("⛔ Yalnız qrup adminləri bağlaya bilər"), show_alert=True)
            return
        await callback.answer(tr("❌ Bağlandı"))
        if msg:
            await delete_quietly(msg)
        return

    if action == "back":
        await callback.answer()
        try:
            username = (await bot.me()).username
            text = await asyncio.to_thread(
                build_private_text, callback.from_user.first_name or "Dostum", username, callback.from_user.id
            )
            await send_welcome(bot, chat_id, text, private_keyboard(username, callback.from_user.id))
        except Exception as e:
            logger.error(f"Salam mesajına qayıtmaq alınmadı: {e}")
            return
        if msg:
            await delete_quietly(msg)
        return

    if action not in ("download", "inline", "edit", "premium", "tip", "invite"):
        await callback.answer()
        return
    if action == "edit" and not tiers.allowed("audio_edit", callback.from_user.id):
        await callback.answer(tiers.gate_text("audio_edit", callback.from_user.id).split("\n")[0]
                              .replace("<b>", "").replace("</b>", "")[:190], show_alert=True)
        return
    await callback.answer()
    try:
        username = (await bot.me()).username
        if action == "premium":
            uid = callback.from_user.id
            text, kb = await asyncio.to_thread(premium_text, uid, username), \
                await asyncio.to_thread(premium_keyboard, uid)
        elif action == "tip":
            text, kb = tip_text(username), tip_keyboard()
        elif action == "invite":
            from bot.menus.views.invites import user_invite_view
            text, kb = await user_invite_view(callback.from_user, username, back_cb="sg:back")
        else:
            text, kb = guide_text(action, username, callback.from_user.id), guide_keyboard(action)
        await send_media(bot, chat_id, text, kb, action)
    except Exception as e:
        logger.error(f"Bələdçi göndərilmədi ({action}): {e}")
        return
    if msg:
        await delete_quietly(msg)


def rights_status(member) -> dict:
    """Botun üzv obyektinə görə hansı yetkilərin olduğunu qaytarır."""
    if member.status == ChatMemberStatus.CREATOR:
        return {key: True for key, _ in REQUIRED_RIGHTS}
    is_admin = member.status == ChatMemberStatus.ADMINISTRATOR
    return {
        "admin": is_admin,
        "can_delete_messages": is_admin and bool(getattr(member, "can_delete_messages", False)),
        "can_pin_messages": is_admin and bool(getattr(member, "can_pin_messages", False)),
    }


def missing_keys(rights: dict) -> tuple:
    return tuple(key for key, _ in REQUIRED_RIGHTS if not rights.get(key))


def admin_request_url(bot_username: str) -> str:
    return f"https://t.me/{bot_username}?startgroup=true&admin={ADMIN_LINK_RIGHTS}"


def rights_keyboard(bot_username: str, rights: dict):
    from common.database import is_start_button_enabled
    rows = []
    if is_start_button_enabled("grp_rights") and missing_keys(rights):
        rows.append([InlineKeyboardButton(text=tr("🛡 Yetkiləri ver"), url=admin_request_url(bot_username))])
    if is_start_button_enabled("grp_inline"):
        rows.append([InlineKeyboardButton(text=tr("🔎 İnline axtarış"), switch_inline_query_current_chat="")])
    if is_start_button_enabled("grp_close"):
        rows.append([InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="sg:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rights_lines(rights: dict) -> str:
    return "\n".join(f"{'✅' if rights.get(key) else '❌'} {tr(label)}" for key, label in REQUIRED_RIGHTS)


def rights_block(bot_username: str, rights: dict) -> str:
    """Yetki siyahısı + çatışmırsa təlimat (qrup salamında {rights})."""
    text = f"🔧 <b>{tr('Lazımi yetkilər:')}</b>\n{rights_lines(rights)}\n\n"
    if missing_keys(rights):
        text += (
            tr("⚠️ <b>Tam işləməyim üçün aşağıdakı düyməyə basıb mənə admin yetkiləri verin.</b>\n")
            + tr("<i>Və ya: Qrup → Adminlər → Admin əlavə et → @{bot}</i>", bot=escape(bot_username))
        )
    else:
        text += tr("✅ <b>Bütün yetkilər verilib, hazıram!</b>")
    return text


def build_group_text(title: str, bot_username: str, rights: dict, adder_html: str = None) -> str:
    template = text_template("group")
    if "{rights}" not in template:
        template = template.rstrip() + "\n\n{rights}"      # yetki siyahısı həmişə görünsün
    thanks_str = tr("Məni qrupa əlavə etdiyin üçün təşəkkürlər, {adder}! ", adder=adder_html) if adder_html else ""
    return tidy(tiers.strip_hidden(render_template(template, {
        "group": escape(title),
        "thanks": thanks_str,
        "adder": adder_html or "",
        "bot": escape(bot_username),
        "rights": rights_block(bot_username, rights),
        "creator": CREATOR_USERNAME,
    }), None))                                            # qrup: 🆓 Free-yə görünməyən imkanlar gizli


def premium_info(user_id: int):
    """(statusu sətri, planlar mətni, alış düymələri) — /start → 💎 Premium al."""
    if is_creator(user_id):
        return tr("👑 <b>Sənin statusun: Creator</b> — bütün imkanlar açıqdır."), "", None
    try:
        cur = get_db().get_premium(user_id)
    except Exception:
        cur = None
    lifetime = bool(cur) and cur["until"] is None
    if cur:
        until = tr("ömürlük") if lifetime else tr("{date}-dək", date=_fmt_date(cur["until"]))
        status = tr("📌 <b>Sənin statusun: 💎 Premium</b> ({until})", until=until)
    else:
        status = tr("📌 <b>Sənin statusun: 🆓 Free</b>")
    if lifetime:
        return status, tr("♾ <i>Ömürlük premiumun var — heç nə almağa ehtiyac yoxdur.</i>"), None
    plans = plans_for_sale()
    if not plans:
        return status, tr("<i>Premium hazırda satışda deyil. Ətraflı: @{creator}</i>", creator=CREATOR_USERNAME), None
    stars = tr("ulduz")
    lines = "\n".join(
        (f"🔥 {escape(label)} — <s>{base}</s> <b>{price}</b> {stars}{per_month(days, price)}" if price < base
         else f"⭐ {escape(label)} — <b>{price}</b> {stars}{per_month(days, price)}")
        for _, days, label, price, base in plan_offers())
    if sale_active():
        end = sale_settings()["end"]
        if end:
            from datetime import datetime
            from common.formatting import get_tz
            lines += "\n" + tr("🏷 <b>Endirim -{pct}%</b> — {date} tarixinədək", pct=sale_settings()["pct"],
                               date=datetime.fromtimestamp(end, get_tz()).strftime("%d.%m.%Y %H:%M"))
        else:
            lines += "\n" + tr("🏷 <b>Endirim -{pct}%</b>", pct=sale_settings()["pct"])
    head = tr("⭐ <b>Uzatmaq üçün plan seç:</b>") if cur else tr("⭐ <b>Plan seç:</b>")
    return status, f"{head}\n{lines}", buy_keyboard(False)


def premium_text(user_id: int, bot_username: str) -> str:
    status, plans, _ = premium_info(user_id)
    from common import tiers
    text = render_template(text_template("premium"), {
        "status": status, "plans": plans, "bot": escape(bot_username), "creator": CREATOR_USERNAME,
        "perks": tiers.perks_html(),                     # /menu → 💎 Premium → 🎛 İmkanlar-a görə
    })
    return text.rstrip()


def premium_keyboard(user_id: int) -> InlineKeyboardMarkup:
    _, _, buy = premium_info(user_id)
    rows = list(buy.inline_keyboard) if buy else []
    rows.append(nav_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tip_text(bot_username: str) -> str:
    return render_template(text_template("tip"), {"bot": escape(bot_username), "creator": CREATOR_USERNAME})


def tip_keyboard() -> InlineKeyboardMarkup:
    """Bəxşiş məbləğləri (payments_plugin invoice göndərir) + öz məbləğin + naviqasiya."""
    btns = [InlineKeyboardButton(text=f"⭐ {n}", callback_data=f"tip:{n}") for n in TIP_AMOUNTS]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([InlineKeyboardButton(text=tr("✏️ Öz məbləğim"), callback_data="tip:custom")])
    rows.append(nav_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tip_message_keyboard() -> InlineKeyboardMarkup:
    """İstifadəçiyə GÖNDƏRİLƏN bəxşiş mesajının düymələri: məbləğlər + öz məbləğim + ✖️ Bağla.
    (tip_keyboard() /start bələdçisi üçündür — naviqasiya sətri var, göndərilən mesajda gərəksizdir.)"""
    btns = [InlineKeyboardButton(text=f"⭐ {n}", callback_data=f"tip:{n}") for n in TIP_AMOUNTS]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([InlineKeyboardButton(text=tr("✏️ Öz məbləğim"), callback_data="tip:custom")])
    rows.append([InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="tip_prompt:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tip_group_text() -> str:
    return tr("⭐ <b>Bəxşiş göndər</b>\n\n"
              "DLLMaster-i bəyənirsinizsə, əməyə dəstək olaraq Telegram Stars ilə bəxşiş göndərə bilərsiniz 💙\n\n"
              "👇 Düyməyə basın — bot şəxsi çatda ödəniş pəncərəsini açacaq.")


def tip_group_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    """Qrup üçün: ödəniş qrupda deyil, botun şəxsi çatında olur (t.me/<bot>?start=tip) —
    qrupdakı üzv botu başlatmayıbsa invoice-u ona göndərmək mümkün olmaz."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr("⭐ Bəxşiş göndər"), url=f"https://t.me/{bot_username}?start=tip")
    ]])


def tip_message(bot_username: str, lang: str | None = None, group: bool = False):
    """Başqasına göndəriləcək bəxşiş mesajı → (mətn, klaviatura). Dil: göndərənin yox, botun defolt dili
    (və ya lang) — creator-un öz dili başqalarına sızmasın. group=True — qrup variantı (deep-link düyməsi)."""
    token = i18n.use(lang or i18n.default_lang())
    try:
        if group:
            return tip_group_text(), tip_group_keyboard(bot_username)
        return tip_text(bot_username), tip_message_keyboard()
    finally:
        i18n.reset(token)


def premium_message(bot_username: str, lang: str | None = None, group: bool = False):
    """Başqasına göndəriləcək 💎 Premium təklifi → (mətn, klaviatura). Alıcı Free kimi göstərilir.
    group=True — qrup variantı: düymə botun şəxsi çatına (t.me/<bot>?start=premium) aparır."""
    token = i18n.use(lang or i18n.default_lang())
    try:
        if group:
            text = tr("💎 <b>DLLMaster Premium</b>\n\nMahnıları \"via\" yazısı olmadan, daha çox imkanla alın.\n\n"
                      "👇 Düyməyə basın — bot şəxsi çatda planları göstərəcək.")
            return text, InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=tr("💎 Premium al"), url=f"https://t.me/{bot_username}?start=premium")]])
        _, _, buy = premium_info(0)
        rows = list(buy.inline_keyboard) if buy else []
        rows.append([InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="tip_prompt:close")])
        return premium_text(0, bot_username), InlineKeyboardMarkup(inline_keyboard=rows)
    finally:
        i18n.reset(token)


def sample_message(slot: str, first_name: str, user_id: int, bot_username: str):
    """/menu önizləməsi üçün: (mətn, klaviatura) — istifadəçilərin görəcəyi kimi."""
    if slot == "welcome":
        return build_private_text(first_name, bot_username, user_id), private_keyboard(bot_username, user_id)
    if slot == "group":
        rights = {"admin": True, "can_delete_messages": True, "can_pin_messages": False}
        adder = f'<a href="tg://user?id={user_id}">{escape(first_name)}</a>'
        return build_group_text("Test qrupu", bot_username, rights, adder), rights_keyboard(bot_username, rights)
    if slot == "premium":
        # Creator önizləmədə free istifadəçinin görəcəyini görsün
        return premium_text(0, bot_username), premium_keyboard(0)
    if slot == "tip":
        return tip_text(bot_username), tip_keyboard()
    if slot == "invite":
        import types as pytypes
        from bot.menus.views.invites import user_invite_data
        user = pytypes.SimpleNamespace(id=user_id, first_name=first_name)
        return user_invite_data(user, bot_username, back_cb="sg:back")
    return guide_text(slot, bot_username), guide_keyboard(slot)


async def get_bot_member(bot: Bot, chat_id: int):
    me = await bot.me()
    member = await bot.get_chat_member(chat_id, me.id)
    return member, me.username


def no_rights_note(title: str) -> str:
    return tr(
        "⚠️ <b>{title}</b> qrupunda mesaj yazmaq icazəm yoxdur, ona görə bu mesajı sənə göndərirəm.\n"
        "<i>Qrupda işləməyim üçün mənə yazmaq icazəsi və ya admin yetkisi verin.</i>\n\n",
        title=escape(title)
    )


async def deliver(bot: Bot, chat, user, text: str, keyboard=None, *,
                  welcome: bool = True, reply_to: int = None, track_prompt: bool = True) -> str:
    """
    Mesajı qrupa göndərir. Qrupda yazmaq icazəsi yoxdursa (Forbidden / BadRequest)
    eyni mesajı `user`-ə (botu əlavə edənə / komandanı yazana) şəxsidə göndərir.
    Qaytarır: "group" | "private" | "failed"
    """
    async def to_chat(cid, rt):
        if welcome:
            return await send_welcome(bot, cid, text, keyboard, reply_to=rt)
        else:
            kw = {"reply_to_message_id": rt, "allow_sending_without_reply": True} if rt else {}
            with ui_styled("user"):
                return await bot.send_message(cid, text, parse_mode="HTML", reply_markup=keyboard,
                                              disable_web_page_preview=True, **kw)

    try:
        try:
            sent = await to_chat(chat.id, reply_to)
            target_cid = chat.id
        except TelegramMigrateToChat as e:
            # Botu admin edəndə adi qrup supergroup-a çevrilir → chat_id dəyişir
            logger.info(f"↪️ Qrup supergroup-a çevrilib: {chat.id} → {e.migrate_to_chat_id}")
            sent = await to_chat(e.migrate_to_chat_id, None)
            target_cid = e.migrate_to_chat_id
        if track_prompt and sent and getattr(sent, "message_id", None):
            remember_rights_msg(target_cid, sent.message_id)
        return "group"
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        logger.warning(f"⚠️ Qrupa yazmaq olmadı ({chat.title} | {chat.id}): {e}")
    except Exception as e:
        logger.error(f"❌ Qrupa göndərmə xətası ({chat.id}): {e}")
        return "failed"

    if not user or user.is_bot:
        return "failed"
    try:
        note = no_rights_note(chat.title or "qrup")
        if welcome:
            await send_welcome(bot, user.id, note + text, keyboard)
        else:
            with ui_styled("user"):
                await bot.send_message(user.id, note + text, parse_mode="HTML", reply_markup=keyboard,
                                       disable_web_page_preview=True)
        logger.info(f"📩 Qrup mesajı şəxsidə göndərildi: {user.id} ({chat.title})")
        return "private"
    except TelegramForbiddenError:
        # İstifadəçi botu heç vaxt /start etməyibsə və ya bloklayıbsa bot ona ilk yaza bilməz
        logger.warning(f"⚠️ {user.id} botu start etməyib/bloklayıb — şəxsiyə də yazmaq olmadı")
    except Exception as e:
        logger.error(f"❌ Şəxsiyə göndərmə xətası ({user.id}): {e}")
    return "failed"


async def send_start_message(message: types.Message, text: str, bot: Bot):
    try:
        bot_username = (await bot.me()).username
        await send_welcome(bot, message.chat.id, text,
                           private_keyboard(bot_username, message.from_user.id if message.from_user else None))
    except Exception as e:
        logger.error(f"Start mesajı xətası: {e}")
        await message.answer(tr("❌ Xəta baş verdi, zəhmət olmasa yenidən cəhd edin."))


@router.message(Command("start"))
async def start_command(message: types.Message):
    context = message.bot.data.get('app_context')
    bot = context.bot

    if message.chat.type == "private":
        payload = (message.text or "").split(maxsplit=1)[1:] or [""]
        p_val = payload[0].strip().lower()

        db = get_db()
        u = await asyncio.to_thread(db.get_user, message.from_user.id) if message.from_user else None
        now = int(time.time())
        is_old = bool(u and (now - int(u.get("first_seen") or now) > 300))

        # 🎁 Dəvət (invite / referral) linki ilə gəliş
        if p_val.startswith("inv_") or p_val.startswith("ref_"):
            try:
                inviter_id = int(p_val.split("_", 1)[1])
            except (ValueError, IndexError):
                inviter_id = 0

            if inviter_id > 0 and message.from_user and inviter_id != message.from_user.id:
                from common.database import (
                    get_invite_reward_days,
                    is_invite_enabled,
                )

                if is_invite_enabled() and not is_old:
                    reward_days = get_invite_reward_days()
                    ok = await asyncio.to_thread(
                        db.record_invite, message.from_user.id, inviter_id, reward_days
                    )
                    if ok:
                        await asyncio.to_thread(
                            db.grant_premium, inviter_id, reward_days, granted_by=0
                        )
                        with contextlib.suppress(Exception):
                            inv_name = escape(message.from_user.first_name or "Yeni istifadəçi")
                            await bot.send_message(
                                inviter_id,
                                tr(
                                    "🎉 <b>Yeni dəvət!</b>\n\n"
                                    "Sizin linkinizlə yeni istifadəçi ({name}) bota qoşuldu.\n"
                                    "💎 Hesabınıza <b>+{days} gün</b> Premium əlavə olundu!",
                                    name=inv_name,
                                    days=reward_days,
                                ),
                                parse_mode="HTML",
                            )
                        with contextlib.suppress(Exception):
                            await message.answer(
                                tr("🎁 <b>Xoş gəldiniz!</b> Dəvət linki ilə qoşuldunuz."),
                                parse_mode="HTML",
                            )

        # 🎉 Xoş gəldin (yeni istifadəçi) sınaq / kampaniya premium-u
        if message.from_user and not message.from_user.is_bot:
            from common.database import (
                get_welcome_prem_days,
                get_welcome_prem_mode,
                get_welcome_prem_until,
                is_welcome_prem_active,
            )

            if is_welcome_prem_active() and not is_old and not await asyncio.to_thread(db.has_welcome_trial, message.from_user.id):
                mode = get_welcome_prem_mode()
                trial_until = None
                dur_text = ""
                trial_days = None

                if mode == "days":
                    trial_days = get_welcome_prem_days()
                    if trial_days > 0:
                        trial_until = now + trial_days * 86400
                        dur_text = tr("{days} gün", days=trial_days)
                elif mode == "date":
                    date_until = get_welcome_prem_until()
                    if date_until and date_until > now:
                        trial_until = date_until
                        dur_text = fmt_date(trial_until, "%d.%m.%Y %H:%M")

                if trial_until:
                    ok = await asyncio.to_thread(
                        db.record_welcome_trial,
                        message.from_user.id,
                        mode,
                        trial_days,
                        trial_until,
                    )
                    if ok:
                        await asyncio.to_thread(
                            db.grant_premium_until,
                            message.from_user.id,
                            trial_until,
                            granted_by=0,
                        )
                        with contextlib.suppress(Exception):
                            await message.answer(
                                tr(
                                    "🎉 <b>Xoş gəldiniz!</b>\n\n"
                                    "Sizə yeni istifadəçi hədiyyəsi olaraq 💎 <b>Premium</b> aktivləşdirildi!\n"
                                    "⏳ <b>Müddət:</b> {duration}\n\n"
                                    "İstədiyiniz musiqiləri yüksək sürətlə və limitsiz dinləyə bilərsiniz. 🎧",
                                    duration=dur_text,
                                ),
                                parse_mode="HTML",
                            )

            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    db.upsert_user,
                    message.from_user.id,
                    message.from_user.username,
                    message.from_user.first_name,
                    message.from_user.last_name,
                )

        if p_val in ("invite", "ref", "davet", "dovet", "inv"):
            username = (await bot.me()).username
            from bot.menus.views.invites import user_invite_view

            text, kb = await user_invite_view(message.from_user, username, back_cb="sg:back")
            await send_media(bot, message.chat.id, text, kb, "invite")
            return

        # 🌐 dil hələ seçilməyibsə (yeni və ya köhnə istifadəçi) — əvvəl dil, salam seçimdən sonra o dildə
        ask = getattr(_CTX, "lang_ask_first", None)
        if ask and await ask(message):
            return
        username = (await bot.me()).username
        if p_val == "tip":         # t.me/<bot>?start=tip (qrupa göndərilən ☕ bəxşiş mesajının düyməsi)
            await send_media(bot, message.chat.id, tip_text(username), tip_keyboard(), "tip")
            return
        if p_val == "premium":     # t.me/<bot>?start=premium (qrupdakı 💎 düyməsi)
            uid = message.from_user.id
            text = await asyncio.to_thread(premium_text, uid, username)
            kb = await asyncio.to_thread(premium_keyboard, uid)
            await send_media(bot, message.chat.id, text, kb, "premium")
            return
        if p_val in ("codec", "format", "audioformat", "mp3", "test_mp3"):
            uid = message.from_user.id
            from bot.plugins.test_mp3_plugin import codec_panel_view
            text, kb = codec_panel_view(user_id=uid, from_menu=False)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return
        if p_val in ("admin_menu", "menu"):
            if is_creator(message.from_user.id):
                from bot.plugins.menu_plugin import main_text, main_kb
                await message.answer(main_text(), parse_mode="HTML", reply_markup=main_kb())
            else:
                from bot.plugins.menu_plugin import user_menu_view
                text, kb = await user_menu_view(bot, message.from_user)
                await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return
        if p_val in ("help_group", "group_help", "rights"):
            text = tr(
                "🛡 <b>Qrupda Musiqi Yükləmə İcazəsi</b>\n\n"
                "Qrup daxilində musiqi göndərilə bilmirsə, qrup admini tərəfindən bota "
                "musiqi/media göndərmə icazəsi verilməli və ya bot admin edilməlidir.\n\n"
                "📌 <b>Bota yetki vermək üçün:</b>\n"
                "1. Aşağıdakı <b>'🛡 Qrupa yetkilərlə əlavə et'</b> düyməsinə klikləyin.\n"
                "2. Qrupunuzu seçin və bota musiqi göndərmə icazəsi verin.\n"
                "3. Və ya qrupun parametrlərindən üzv icazələrində <b>'Musiqi göndər'</b> funksiyasını aktiv edin."
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=tr("🛡 Qrupa yetkilərlə əlavə et"), url=admin_request_url(username))],
                [InlineKeyboardButton(text=tr("🔙 Əsas Menyu"), callback_data="start:home")]
            ])
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return
        if p_val.startswith("dl_"):
            vid = p_val[3:].strip()
            if vid:
                audio_cache = getattr(context, "audio_cache", None)
                row = None
                if audio_cache and hasattr(audio_cache, "get_cached"):
                    row = await asyncio.to_thread(audio_cache.get_cached, vid)
                if not row:
                    try:
                        row = await asyncio.to_thread(get_db().cache_get, vid)
                    except Exception as _e:
                        logger.debug("udulan xəta [bot/plugins/start_plugin.py:738]: %r", _e)
                if row and row.get("file_id"):
                    file_id = row["file_id"]
                    track = row.get("title") or "Mahnı"
                    artist = row.get("performer") or ""
                    dur = row.get("duration") or 0
                    caption = f"🎵 <b>{escape(artist)} - {escape(track)}</b>" if artist else f"🎵 <b>{escape(track)}</b>"
                    await bot.send_audio(
                        chat_id=message.chat.id,
                        audio=file_id,
                        title=track[:64],
                        performer=artist[:64],
                        duration=dur or None,
                        caption=caption,
                        parse_mode="HTML",
                    )
                    return
        text = await asyncio.to_thread(
            build_private_text, message.from_user.first_name or "Dostum", username, message.from_user.id
        )
        await send_start_message(message, text, bot)
    else:
        try:
            member, username = await get_bot_member(bot, message.chat.id)
            rights = rights_status(member)
        except Exception as e:
            logger.error(f"Yetki yoxlanışı xətası: {e}")
            username = (await bot.me()).username
            rights = {key: False for key, _ in REQUIRED_RIGHTS}
        await deliver(
            bot, message.chat, message.from_user,
            build_group_text(message.chat.title or "qrup", username, rights),
            rights_keyboard(username, rights),
            welcome=False, reply_to=message.message_id,
        )


@router.my_chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def on_bot_added(event: ChatMemberUpdated, bot: Bot):
    # Yalnız qrup/supergroup hadisələri
    if event.chat.type not in ["group", "supergroup"]:
        return

    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    logger.info(f"🔄 Bot status dəyişikliyi: {old_status} -> {new_status} | Chat: {event.chat.title} ({event.chat.id})")

    try:
        username = (await bot.me()).username
        rights = rights_status(event.new_chat_member)
        adder = event.from_user.mention_html() if event.from_user else None

        result = await deliver(
            bot, event.chat, event.from_user,
            build_group_text(event.chat.title or "qrup", username, rights, adder_html=adder),
            rights_keyboard(username, rights),
        )
        _last_notice[event.chat.id] = missing_keys(rights)
        if result == "private":
            _pending_welcome.add(event.chat.id)
        logger.info(f"✅ Qrup salam mesajı: {result} | {event.chat.title}")

        # 🤖 Kənar qrupa köməkçi botların avtomatik qoşulması (ayarlardan asılı olaraq)
        pool = getattr(_CTX, "depo_helpers", None)
        if pool and pool.ext_enabled and pool.ext_auto:
            spawn(pool.auto_add_to_group(event.chat.id))

    except Exception as e:
        logger.error(f"❌ Qrup salam mesajı göndərilmədi: {str(e)}")
        logger.debug(f"Xəta detalları: Chat ID: {event.chat.id}, Status: {new_status}")


@router.my_chat_member(ChatMemberUpdatedFilter((MEMBER | RESTRICTED) >> ADMINISTRATOR))
@router.my_chat_member(ChatMemberUpdatedFilter(ADMINISTRATOR >> ADMINISTRATOR))
@router.my_chat_member(ChatMemberUpdatedFilter(RESTRICTED >> MEMBER))
async def on_bot_rights_changed(event: ChatMemberUpdated, bot: Bot):
    """Bot admin edildikdə və ya yetkiləri dəyişdikdə: çatışmayanları bildirir / təşəkkür edir."""
    if event.chat.type not in ["group", "supergroup"]:
        return

    try:
        username = (await bot.me()).username
        rights = rights_status(event.new_chat_member)
        missing = missing_keys(rights)

        # Salam əvvəl yetki olmadığı üçün şəxsidə göndərilmişdisə — indi qrupun özünə göndər
        if event.chat.id in _pending_welcome:
            result = await deliver(
                bot, event.chat, None,
                build_group_text(event.chat.title or "qrup", username, rights),
                rights_keyboard(username, rights),
            )
            if result == "group":
                _pending_welcome.discard(event.chat.id)
                _last_notice[event.chat.id] = missing
                logger.info(f"✅ Gecikmiş qrup salamı göndərildi: {event.chat.title}")
                return

        # Eyni vəziyyəti təkrar bildirmə
        if _last_notice.get(event.chat.id) == missing:
            return
        _last_notice[event.chat.id] = missing

        if not missing:
            # 🗑 Yetkilər verildikdən sonra köhnə mesaj(lar) silinsin
            await delete_old_rights_messages(bot, event.chat.id, username)

            text = tr(
                "✅ <b>Təşəkkürlər!</b> Bütün yetkilər verildi, hazıram 🚀\n\n"
                "🔎 <code>/music mahnı adı</code> yazaraq başlaya bilərsiniz."
            )
            keyboard = None
            result = await deliver(bot, event.chat, event.from_user, text, keyboard, welcome=False, track_prompt=False)
            logger.info(f"🔐 Yetki bildirişi ({result}): {event.chat.title} | bütün yetkilər verildi")
            return
        else:
            # Hələ çatışmayan yetkilər varsa — əvvəlki bildirişi silək ki, qrupda təkrar mesaj yığılmasın
            await delete_old_rights_messages(bot, event.chat.id, username)

            text = (
                tr("⚠️ <b>Hələ çatışmayan yetkilər var:</b>\n")
                + f"{rights_lines(rights)}\n\n"
                + tr("<i>Aşağıdakı düyməyə basıb tamamlayın.</i>")
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=tr("🛡 Yetkiləri ver"), url=admin_request_url(username))],
            ])
            result = await deliver(bot, event.chat, event.from_user, text, keyboard, welcome=False, track_prompt=True)
            logger.info(f"🔐 Yetki bildirişi ({result}): {event.chat.title} | çatışmayan: {missing}")

    except Exception as e:
        logger.error(f"❌ Yetki bildirişi göndərilmədi: {e}")
