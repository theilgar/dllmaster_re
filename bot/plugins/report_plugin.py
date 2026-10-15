"""
🚨 «Adminə bildir» düyməsi — bot/plugins/report_plugin.py

İstifadəçiyə göstərilən xəta mesajının (❌ / ⚠️ + «Xəta», «Yüklənmədi», «tapılmadı» ...) altına AVTOMATİK
«🚨 Adminə bildir» düyməsi qoyulur — hər funksiyada (musiqi, mix, inline, TikTok, Shazam, səsli yayım, linklər ...),
hər plugin-də ayrıca kod yazmadan: bot sessiyasına request-middleware qoşulur.
Düyməyə basılanda bot sahibinə (CREATOR_ID) istifadəçi, çat, xəta mətni və son axtarış göndərilir.

Qorunma: bir istifadəçi 30 san.-də bir, gündə ən çox 10 bildiriş; admin öz çatında düyməni görmür;
istifadəçi səhvləri (ləğv, köhnə siyahı, icazə, limit ...) üçün düymə qoyulmur.
Söndürmək: /reportbtn off (yalnız bot sahibi), /reportbtn on.
"""
import logging
import re
import time
from html import escape

from aiogram import F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from common.i18n import _ as tr

logger = logging.getLogger(__name__)

PRIORITY = 4

CB_GO, CB_DONE = "rp:go", "rp:done"
SETTING = "bot:report_btn"
COOLDOWN, DAILY_MAX = 30, 10

_TAGS = re.compile(r"<[^>]+>")
_START = re.compile(r"^\s*[`'\"·\-–—]*\s*(?:❌|⚠️|🚫|⛔)")
_ERR_WORDS = ("xəta", "yüklənmədi", "yüklənə bilmədi", "alınmadı", "tapılmadı", "tanınmadı", "açılmadı",
              "göndərilmədi", "göndərilə bilmədi", "oxudulmadı", "error", "failed", "ilişdi", "problem",
              # en / tr / ru (istifadəçi dilində göstərilən xətalar)
              "could not", "couldn't", "not found", "unable", "not recognized", "didn't", "hata", "başarısız",
              "bulunamadı", "indirilemedi", "yüklenemedi", "tanınamadı", "açılamadı", "gönderilemedi", "ошибка",
              "не удалось", "не найден", "не найдена", "не распозна", "не загруз", "не открыл")
# istifadəçi səhvi / gözlənilən hal — admin üçün maraqsız
_SKIP_WORDS = ("ləğv", "dayandır", "köhnəl", "icazə", "yalnız", "müddət", "premium", "söndürül", "limit", "gözləmə",
               "flood", "bloklan", "artıq", "yanlış", "seçim", "bildirildi", "admin məlumatlandırıldı", "bot sahibi",
               "adminlər", "qrup admin", "digər", "əvvəl",
               # en / tr / ru
               "cancel", "stopped", "expired", "permission", "only", "already", "wrong", "limit", "wait",
               "iptal", "durdur", "süresi", "izin", "yalnızca", "zaten", "yanlış", "bekle",
               "отмен", "останов", "устар", "разреш", "только", "уже", "неверн", "подожд", "ожида")


def plain(text: str) -> str:
    return _TAGS.sub("", text or "")


def is_error_text(text: str) -> bool:
    """Mesaj istifadəçiyə göstərilən TEXNİKİ xəta kimi görünürmü."""
    t = plain(text)
    if not _START.match(t):
        return False
    low = t.lower()
    if any(w in low for w in _SKIP_WORDS):
        return False
    return any(w in low for w in _ERR_WORDS)


def has_report_row(markup) -> bool:
    rows = getattr(markup, "inline_keyboard", None) or []
    return any(getattr(b, "callback_data", None) in (CB_GO, CB_DONE) for r in rows for b in r)


def with_report(markup):
    """Mövcud inline klaviaturanın sonuna «🚨 Adminə bildir» sətri əlavə edir (digər düymələr qalır)."""
    rows = [list(r) for r in (getattr(markup, "inline_keyboard", None) or [])] if markup is not None else []
    rows.append([InlineKeyboardButton(text=tr("🚨 Adminə bildir"), callback_data=CB_GO)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def setup(context):
    from aiogram.client.session.middlewares.base import BaseRequestMiddleware
    from aiogram.methods import EditMessageText, SendMessage

    bot, dp = context.bot, context.dp
    creator = context.creator_id
    last_report, daily = {}, {}          # user_id -> ts ; user_id -> (gün, say)

    def enabled() -> bool:
        try:
            from common.database import get_db
            return get_db().get_setting(SETTING, "1") != "0"
        except Exception:
            return True

    class ReportButton(BaseRequestMiddleware):
        async def __call__(self, make_request, b, method):
            try:
                if (isinstance(method, (SendMessage, EditMessageText)) and enabled()
                        and is_error_text(getattr(method, "text", "") or "")):
                    cid = getattr(method, "chat_id", None)
                    markup = getattr(method, "reply_markup", None)
                    is_inline_kb = markup is None or isinstance(markup, InlineKeyboardMarkup)
                    if is_inline_kb and not has_report_row(markup) and not (creator and cid == creator):
                        method = method.model_copy(update={"reply_markup": with_report(markup)})
            except Exception as e:                      # düymə əlavəsi heç vaxt mesajı pozmasın
                logger.debug(f"report düyməsi qoyulmadı: {e!r}")
            return await make_request(b, method)

    old = getattr(context, "_report_mw", None)
    if old is not None:
        try:
            bot.session.middleware.unregister(old)
        except Exception as _e:
            logger.debug("report mw çıxarılmadı: %r", _e)
    mw = ReportButton()
    bot.session.middleware(mw)
    context._report_mw = mw

    def last_search(uid: int) -> str:
        us = (getattr(context, "user_searches", None) or {}).get(uid) or {}
        parts = []
        if us.get("query"):
            parts.append(f"axtarış: {us['query']}")
        res = us.get("results") or []
        if res:
            parts.append(f"nəticə sayı: {len(res)}")
        return " · ".join(parts)

    def user_link(u) -> str:
        name = escape(" ".join(x for x in (u.first_name, u.last_name) if x) or str(u.id))
        uname = f" · @{escape(u.username)}" if u.username else ""
        return f'<a href="tg://user?id={u.id}">{name}</a>{uname} · <code>{u.id}</code>'

    @dp.callback_query(F.data.in_({CB_GO, CB_DONE}))
    async def report_cb(cb: types.CallbackQuery):
        if cb.data == CB_DONE:
            await cb.answer(tr("✅ Admin artıq məlumatlandırılıb"))
            return
        uid = cb.from_user.id
        now = time.time()
        if now - last_report.get(uid, 0) < COOLDOWN:
            await cb.answer(tr("⏳ Bir az gözləyin — bildiriş artıq göndərilib"), show_alert=True)
            return
        day, n = daily.get(uid, (time.strftime("%Y%m%d"), 0))
        if day != time.strftime("%Y%m%d"):
            day, n = time.strftime("%Y%m%d"), 0
        if n >= DAILY_MAX:
            await cb.answer(tr("Bu gün üçün bildiriş limiti dolub"), show_alert=True)
            return
        if not creator:
            await cb.answer(tr("Admin təyin olunmayıb"), show_alert=True)
            return
        msg = cb.message
        err_text = plain(getattr(msg, "html_text", None) or getattr(msg, "text", None)
                         or getattr(msg, "caption", None) or "") if msg else ""
        chat = msg.chat if msg else None
        where = (f"{escape(getattr(chat, 'title', None) or chat.type)} · <code>{chat.id}</code>" if chat
                 else "inline mesaj")
        lines = ["🚨 <b>İstifadəçi xəta bildirdi</b>", "━━━━━━━━━━━━━━━━━━",
                 f"👤 {user_link(cb.from_user)}", f"💬 {where}",
                 f"🕒 {time.strftime('%d.%m.%Y %H:%M:%S')}"]
        ls = last_search(uid)
        if ls:
            lines.append(f"🔎 {escape(ls)}")
        if err_text:
            lines.append(f"\n<b>Xəta mətni:</b>\n<pre>{escape(err_text[:900])}</pre>")
        lines.append("\n<i>Ətraflı səbəb üçün bot_logs-a bu vaxt aralığına baxın.</i>")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💬 İstifadəçiyə yaz", url=f"tg://user?id={uid}"),
            InlineKeyboardButton(text="✖️ Bağla", callback_data="menu:cancel")]])
        try:
            await bot.send_message(creator, "\n".join(lines), parse_mode="HTML", reply_markup=kb,
                                   disable_web_page_preview=True)
        except Exception as e:
            logger.warning(f"Xəta bildirişi adminə getmədi: {e}")
            await cb.answer(tr("❌ Bildiriş göndərilmədi, bir az sonra yenidən cəhd edin"), show_alert=True)
            return
        last_report[uid] = now
        daily[uid] = (day, n + 1)
        logger.info(f"🚨 Xəta bildirildi: user={uid} chat={getattr(chat, 'id', 'inline')} :: {err_text[:120]!r}")
        await cb.answer(tr("✅ Adminə göndərildi. Təşəkkürlər!"), show_alert=False)
        # düyməni «✅ Bildirildi» ilə əvəz et (qalan düymələr qalır)
        try:
            rows = [list(r) for r in (getattr(getattr(msg, "reply_markup", None), "inline_keyboard", None) or [])
                    if not any(b.callback_data == CB_GO for b in r)]
            rows.append([InlineKeyboardButton(text=tr("✅ Admin məlumatlandırıldı"), callback_data=CB_DONE)])
            if msg:
                await msg.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            elif cb.inline_message_id:
                await bot.edit_message_reply_markup(inline_message_id=cb.inline_message_id,
                                                    reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        except Exception as _e:
            logger.debug("report düyməsi dəyişmədi: %r", _e)

    @dp.message(Command("reportbtn"))
    async def reportbtn_cmd(message: types.Message, command: CommandObject):
        if not message.from_user or not context.is_creator(message.from_user.id):
            return
        from common.database import get_db
        arg = (command.args or "").strip().lower()
        if arg in ("on", "off"):
            get_db().set_setting(SETTING, "1" if arg == "on" else "0")
        await message.answer(f"🚨 «Adminə bildir» düyməsi: <b>{'🟢 açıq' if enabled() else '🔴 bağlı'}</b>\n"
                             "<i>Dəyiş:</i> <code>/reportbtn on</code> · <code>/reportbtn off</code>", parse_mode="HTML")

    logger.info("✅ «Adminə bildir» düyməsi aktivdir (xəta mesajlarına avtomatik)")
