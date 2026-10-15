"""
🌐 Dil dəstəyi — common/i18n.py

  _("Azərbaycan mətni", ad=dəyər)   → cari istifadəçinin dilində (tərcümə yoxdursa — Azərbaycanca)
  current() / use(lang)             → cari dil (hər update üçün bot/main.py middleware təyin edir)
  get_lang(uid) / set_lang(uid, l)  → istifadəçinin seçimi (settings: lang:<uid>)
  template(slot, lang)              → /start bələdçilərinin tərcümələri (start_plugin)

Açar — Azərbaycan mətninin ÖZÜDÜR (gettext kimi): yeni yerə tərcümə əlavə etmək üçün mətni _() ilə
bükmək və CATALOG-a sətir yazmaq kifayətdir. Admin paneli (/menu) Azərbaycanca qalır — onu yalnız sahibi görür.

📁 DİL FAYLLARI — lang/<kod>.json (yeni fayl = yeni dil, restart lazım deyil: reload()):
  {"_meta": {"code": "de", "name": "🇩🇪 Deutsch", "telegram_codes": ["de", "at"]},
   "templates": {"welcome": "...", "group": "...", ...},       # /start bələdçiləri (yer tutucular: {name} {bot} ...)
   "strings":   {"<Azərbaycanca mətn>": "<tərcümə>", ...}}
  Şablon fayl (bütün açarlar boş): export_template() · /menu → 🎨 Mesajlar → 🌐 Dillər → 📤 Şablon
  Koddakı CATALOG / TEMPLATES — daxili ehtiyat (fayl silinsə də en / ru / tr işləyir); fayl onlardan üstündür.
"""
from __future__ import annotations

import contextvars
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANG_DIR = ROOT / "lang"
DEFAULT = "az"
BUILTIN_NAMES = {"en": "🇬🇧 English", "ru": "🇷🇺 Русский", "tr": "🇹🇷 Türkçe"}
_BASE_DETECT = {"az": "az", "tr": "tr", "en": "en", "ru": "ru", "uk": "ru", "be": "ru", "kk": "ru", "ky": "ru",
                "uz": "ru", "tg": "ru", "hy": "ru", "ka": "ru"}
CODE_RE = re.compile(r"^[a-z]{2,3}(?:[-_][a-z0-9]{2,8})?$")
TEMPLATE_VARS = {"name", "bot", "status", "creator", "group", "thanks", "rights", "perks", "plans"}

# reload() doldurur
LANGS = {"az": "🇦🇿 Azərbaycanca"}
STRINGS: dict = {}            # dil → {az mətn: tərcümə}
TPL: dict = {}                # dil → {slot: şablon}
_DETECT = dict(_BASE_DETECT)
REPORT: dict = {}             # dil → {"file", "strings", "templates", "skipped": [...], "error"}

_cur: contextvars.ContextVar = contextvars.ContextVar("lang", default=DEFAULT)
_cache: dict = {}


def _s(key, default=None):
    try:
        from common.database import get_db
        v = get_db().get_setting(key)
    except Exception:
        v = None
    return default if v is None else v


def default_lang() -> str:
    v = _s("i18n:default", DEFAULT)
    return v if v in LANGS else DEFAULT


def auto_detect() -> bool:
    return _s("i18n:auto", "1") == "1"


def detect(code: str | None) -> str:
    if not code or not auto_detect():
        return default_lang()
    c = code.lower().replace("_", "-")
    lang = _DETECT.get(c) or _DETECT.get(c.split("-")[0])
    if lang in LANGS:
        return lang
    return "en" if "en" in LANGS else default_lang()


def get_lang(uid, code: str | None = None) -> str:
    if uid in _cache:
        return _cache[uid]
    v = _s(f"lang:{uid}") if uid else None
    if v in LANGS:
        _cache[uid] = v
        return v
    return detect(code)


def chosen(uid) -> bool:
    return _s(f"lang:{uid}") in LANGS


def set_lang(uid, lang: str):
    if lang not in LANGS:
        return
    from common.database import get_db
    get_db().set_setting(f"lang:{uid}", lang)
    _cache[uid] = lang


def current() -> str:
    return _cur.get()


def use(lang: str):
    """Bu update üçün dili təyin et (middleware). Token qaytarır — reset üçün."""
    return _cur.set(lang if lang in LANGS else DEFAULT)


def reset(token):
    _cur.reset(token)


def _(text: str, lang: str | None = None, **kw) -> str:
    lang = lang or _cur.get()
    if lang != "az":
        text = STRINGS.get(lang, {}).get(text) or text
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def template(slot: str, lang: str | None = None) -> str | None:
    lang = lang or _cur.get()
    return TPL.get(lang, {}).get(slot)


def stats() -> dict:
    """Dil seçimi statistikası (admin paneli üçün)."""
    out = {k: 0 for k in LANGS}
    try:
        from common.database import get_db
        db = get_db()
        for k, v in list(getattr(db, "_settings", {}).items()):
            if k.startswith("lang:") and v in out:
                out[v] += 1
    except Exception:
        pass
    return out


# ═════════════════════════ /start bələdçiləri ═════════════════════════
TEMPLATES = {
    "en": {
        "welcome": (
            "<b>Hi {name}! 👋 I'm DLLMaster Bot 🎵</b>\n\n"
            "🎧 <b>What can I do?</b>\n"
            "🔎 Type a song name or <code>/music name</code> — I'll find and download it\n"
            "🔗 Send a YouTube / Spotify link, playlist or album\n"
            "⚡ In any chat: <code>@{bot} song name</code>\n"
            "🔀 <code>/mix name</code> or the <b>Mix</b> button under a song — similar songs\n"
            "⏹ You can stop a download at any time\n"
            "✏️ Send me an audio — I'll change its title, artist and cover\n\n"
            "{status}\n\n"
            "Bot created by @{creator} 🚀"),
        "group": (
            "<b>Hello, {group}! 👋</b>\n\n"
            "{thanks}I'm <b>DLLMaster Bot</b> 🎵\n\n"
            "🎧 <b>How to use me in a group?</b>\n"
            "🔎 <code>/music song name</code> — I'll find and download it\n"
            "⚡ <code>@{bot} song name</code> — send with one tap\n"
            "🔗 YouTube / Spotify playlist, album or track link\n"
            "🔀 <code>/mix song name</code> — similar songs\n\n"
            "{rights}\n\n"
            "Bot created by @{creator} 🚀"),
        "download": (
            "🎵 <b>How to download a song?</b>\n\n"
            "1️⃣ Type the song name, e.g. <code>Miri Yusif Qarabag</code>\n"
            "   or <code>/music song name</code>\n"
            "2️⃣ Pick the song from the list\n"
            "3️⃣ The song arrives in a few seconds 🎧\n\n"
            "🔗 <b>Links work too:</b> YouTube, Spotify track, playlist and album\n\n"
            "⏹ Cancel any time with <b>Stop download</b>\n"
            "🔀 <code>/mix song name</code> — list of similar songs\n"
            "   (or reply <code>/mix</code> to a song the bot sent)\n"
            "🙈 <b>Hide</b> — hides the buttons under the song"),
        "inline": (
            "🔎 <b>Inline search</b>\n\n"
            "Send songs in <b>any chat</b> without adding the bot:\n\n"
            "1️⃣ In the message field type: <code>@{bot} song name</code>\n"
            "2️⃣ Pick the song from the list\n"
            "3️⃣ The song goes to that chat 🎧\n\n"
            "🔀 Use <b>Mix</b> under a song to send similar songs the same way.\n\n"
            "👇 Tap the button to try it"),
        "premium": (
            "💎 <b>DLLMaster Premium</b>\n\n"
            "✨ <b>What does Premium give?</b>\n"
            "{perks}\n\n"
            "⚡ Activates in seconds with Telegram Stars\n"
            "🔁 You can extend an active premium — time is added on top\n\n"
            "{status}\n\n"
            "{plans}"),
        "tip": (
            "⭐ <b>Send a tip</b>\n\n"
            "If you like DLLMaster, you can support the work with a Telegram Stars tip 💙\n\n"
            "Tips help cover the server, the storage channel and new features.\n\n"
            "👇 <b>Pick an amount or type your own:</b>"),
        "edit": (
            "✏️ <b>Edit song metadata</b>\n\n"
            "1️⃣ Send me any song (audio file)\n"
            "2️⃣ Choose in the panel:\n"
            "   ✏️ <b>Title</b> — song title\n"
            "   🎤 <b>Artist</b> — performer name\n"
            "   🖼 <b>Thumbnail</b> — cover image\n"
            "3️⃣ Tap <b>✅ Send</b> — the edited song comes back\n\n"
            "<i>ℹ️ The file must be under 20 MB (Telegram limit).</i>"),
    },
    "ru": {
        "welcome": (
            "<b>Привет, {name}! 👋 Я DLLMaster Bot 🎵</b>\n\n"
            "🎧 <b>Что я умею?</b>\n"
            "🔎 Напиши название песни или <code>/music название</code> — найду и скачаю\n"
            "🔗 Пришли ссылку YouTube / Spotify, плейлист или альбом\n"
            "⚡ В любом чате: <code>@{bot} название песни</code>\n"
            "🔀 <code>/mix название</code> или кнопка <b>Mix</b> под песней — похожие песни\n"
            "⏹ Загрузку можно остановить в любой момент\n"
            "✏️ Пришли аудио — изменю название, исполнителя и обложку\n\n"
            "{status}\n\n"
            "Бот создан @{creator} 🚀"),
        "group": (
            "<b>Привет, {group}! 👋</b>\n\n"
            "{thanks}Я <b>DLLMaster Bot</b> 🎵\n\n"
            "🎧 <b>Как пользоваться в группе?</b>\n"
            "🔎 <code>/music название песни</code> — найду и скачаю\n"
            "⚡ <code>@{bot} название песни</code> — отправить в одно касание\n"
            "🔗 Ссылка на плейлист, альбом или трек YouTube / Spotify\n"
            "🔀 <code>/mix название песни</code> — похожие песни\n\n"
            "{rights}\n\n"
            "Бот создан @{creator} 🚀"),
        "download": (
            "🎵 <b>Как скачать песню?</b>\n\n"
            "1️⃣ Напиши название, например <code>Miri Yusif Qarabag</code>\n"
            "   или <code>/music название</code>\n"
            "2️⃣ Выбери песню из списка\n"
            "3️⃣ Через пару секунд песня придёт 🎧\n\n"
            "🔗 <b>Ссылки тоже работают:</b> YouTube, трек, плейлист и альбом Spotify\n\n"
            "⏹ Отменить можно кнопкой <b>Остановить загрузку</b>\n"
            "🔀 <code>/mix название</code> — список похожих песен\n"
            "   (или ответь <code>/mix</code> на песню от бота)\n"
            "🙈 <b>Скрыть</b> — убирает кнопки под песней"),
        "inline": (
            "🔎 <b>Инлайн-поиск</b>\n\n"
            "Отправляй песни в <b>любой чат</b>, не добавляя бота:\n\n"
            "1️⃣ В поле сообщения напиши: <code>@{bot} название песни</code>\n"
            "2️⃣ Выбери песню из списка\n"
            "3️⃣ Песня уйдёт в этот чат 🎧\n\n"
            "🔀 Кнопкой <b>Mix</b> под песней так же отправляются похожие песни.\n\n"
            "👇 Нажми кнопку, чтобы попробовать"),
        "premium": (
            "💎 <b>DLLMaster Premium</b>\n\n"
            "✨ <b>Что даёт Premium?</b>\n"
            "{perks}\n\n"
            "⚡ Активируется за секунды через Telegram Stars\n"
            "🔁 Активный премиум можно продлить — срок добавляется\n\n"
            "{status}\n\n"
            "{plans}"),
        "tip": (
            "⭐ <b>Отправить чаевые</b>\n\n"
            "Если тебе нравится DLLMaster, можешь поддержать работу чаевыми в Telegram Stars 💙\n\n"
            "Чаевые помогают оплачивать сервер, канал-хранилище и новые функции.\n\n"
            "👇 <b>Выбери сумму или напиши свою:</b>"),
        "edit": (
            "✏️ <b>Изменить метаданные песни</b>\n\n"
            "1️⃣ Пришли мне любую песню (аудиофайл)\n"
            "2️⃣ Выбери в панели:\n"
            "   ✏️ <b>Название</b> — название песни\n"
            "   🎤 <b>Исполнитель</b> — имя исполнителя\n"
            "   🖼 <b>Обложка</b> — картинка\n"
            "3️⃣ Нажми <b>✅ Отправить</b> — изменённая песня вернётся\n\n"
            "<i>ℹ️ Файл должен быть меньше 20 МБ (лимит Telegram).</i>"),
    },
    "tr": {
        "welcome": (
            "<b>Merhaba {name}! 👋 Ben DLLMaster Bot 🎵</b>\n\n"
            "🎧 <b>Neler yapabilirim?</b>\n"
            "🔎 Şarkı adını yaz veya <code>/music ad</code> — bulup indiririm\n"
            "🔗 YouTube / Spotify bağlantısı, çalma listesi veya albüm gönder\n"
            "⚡ Her sohbette: <code>@{bot} şarkı adı</code>\n"
            "🔀 <code>/mix ad</code> veya şarkının altındaki <b>Mix</b> — benzer şarkılar\n"
            "⏹ İndirmeyi istediğin an durdurabilirsin\n"
            "✏️ Bana bir ses dosyası gönder — adını, sanatçısını ve kapağını değiştireyim\n\n"
            "{status}\n\n"
            "Bot @{creator} tarafından yapıldı 🚀"),
        "group": (
            "<b>Merhaba, {group}! 👋</b>\n\n"
            "{thanks}Ben <b>DLLMaster Bot</b> 🎵\n\n"
            "🎧 <b>Grupta nasıl kullanılır?</b>\n"
            "🔎 <code>/music şarkı adı</code> — bulup indiririm\n"
            "⚡ <code>@{bot} şarkı adı</code> — tek dokunuşla gönder\n"
            "🔗 YouTube / Spotify çalma listesi, albüm veya parça bağlantısı\n"
            "🔀 <code>/mix şarkı adı</code> — benzer şarkılar\n\n"
            "{rights}\n\n"
            "Bot @{creator} tarafından yapıldı 🚀"),
        "download": (
            "🎵 <b>Şarkı nasıl indirilir?</b>\n\n"
            "1️⃣ Şarkının adını yaz, örn. <code>Miri Yusif Qarabag</code>\n"
            "   veya <code>/music şarkı adı</code>\n"
            "2️⃣ Listeden şarkıyı seç\n"
            "3️⃣ Birkaç saniyede şarkı gelir 🎧\n\n"
            "🔗 <b>Bağlantılar da çalışır:</b> YouTube, Spotify parça, çalma listesi ve albüm\n\n"
            "⏹ İndirme sırasında <b>İndirmeyi durdur</b> ile iptal edebilirsin\n"
            "🔀 <code>/mix şarkı adı</code> — benzer şarkılar listesi\n"
            "   (veya botun gönderdiği şarkıya yanıt olarak <code>/mix</code>)\n"
            "🙈 <b>Gizle</b> — şarkının altındaki düğmeleri gizler"),
        "inline": (
            "🔎 <b>Satır içi arama</b>\n\n"
            "Botu eklemeden <b>her sohbette</b> şarkı gönderebilirsin:\n\n"
            "1️⃣ Mesaj alanına yaz: <code>@{bot} şarkı adı</code>\n"
            "2️⃣ Listeden şarkıyı seç\n"
            "3️⃣ Şarkı o sohbete gider 🎧\n\n"
            "🔀 Şarkının altındaki <b>Mix</b> ile benzer şarkıları da aynı şekilde gönderebilirsin.\n\n"
            "👇 Denemek için düğmeye bas"),
        "premium": (
            "💎 <b>DLLMaster Premium</b>\n\n"
            "✨ <b>Premium ne sağlar?</b>\n"
            "{perks}\n\n"
            "⚡ Telegram Stars ile saniyeler içinde etkinleşir\n"
            "🔁 Aktif premium uzatılabilir — süre üstüne eklenir\n\n"
            "{status}\n\n"
            "{plans}"),
        "tip": (
            "⭐ <b>Bahşiş gönder</b>\n\n"
            "DLLMaster'ı beğendiysen, emeğe destek olarak Telegram Stars ile bahşiş gönderebilirsin 💙\n\n"
            "Bahşişler sunucu, depo kanalı ve yeni özelliklerin masraflarına yardımcı olur.\n\n"
            "👇 <b>Tutarı seç veya kendin yaz:</b>"),
        "edit": (
            "✏️ <b>Şarkı bilgilerini düzenle</b>\n\n"
            "1️⃣ Bana herhangi bir şarkı (ses dosyası) gönder\n"
            "2️⃣ Açılan panelden seç:\n"
            "   ✏️ <b>Ad</b> — şarkının adı\n"
            "   🎤 <b>Sanatçı</b> — sanatçının adı\n"
            "   🖼 <b>Kapak</b> — kapak resmi\n"
            "3️⃣ <b>✅ Gönder</b>'e bas — düzenlenmiş şarkı geri gelir\n\n"
            "<i>ℹ️ Dosya 20 MB'tan büyük olmamalı (Telegram sınırı).</i>"),
    },
}


# ═════════════════════════ qısa mətnlər (açar = Azərbaycanca mətn) ═════════════════════════
def _t(en, ru, tr):
    return {"en": en, "ru": ru, "tr": tr}


CATALOG = {
    # ── dil seçimi ──
    "🌐 Dil seçin:": _t("🌐 Choose a language:", "🌐 Выберите язык:", "🌐 Dil seçin:"),
    "✅ Dil dəyişdirildi: {name}": _t("✅ Language changed: {name}", "✅ Язык изменён: {name}", "✅ Dil değiştirildi: {name}"),
    "🌐 Dil": _t("🌐 Language", "🌐 Язык", "🌐 Dil"),
    # ── /start klaviaturası ──
    "🎵 Mahnı yüklə": _t("🎵 Download a song", "🎵 Скачать песню", "🎵 Şarkı indir"),
    "🔎 İnline axtarış": _t("🔎 Inline search", "🔎 Инлайн-поиск", "🔎 Satır içi arama"),
    "✏️ Metadata redaktə": _t("✏️ Edit metadata", "✏️ Изменить метаданные", "✏️ Bilgileri düzenle"),
    "💎 Premium al": _t("💎 Get Premium", "💎 Купить Premium", "💎 Premium al"),
    "⭐ Bəxşiş göndər": _t("⭐ Send a tip", "⭐ Чаевые", "⭐ Bahşiş gönder"),
    "📦 Depo kanalı — bütün mahnılar": _t("📦 Storage channel — all songs", "📦 Канал-хранилище — все песни",
                                         "📦 Depo kanalı — tüm şarkılar"),
    "🤖 Botu qrupa əlavə et": _t("🤖 Add the bot to a group", "🤖 Добавить бота в группу", "🤖 Botu gruba ekle"),
    "❌ Ləğv et": _t("❌ Cancel", "❌ Отмена", "❌ İptal"),
    "⬅️ Geri": _t("⬅️ Back", "⬅️ Назад", "⬅️ Geri"),
    "🔎 Burada sına": _t("🔎 Try it here", "🔎 Попробовать здесь", "🔎 Burada dene"),
    "📤 Başqa çatda istifadə et": _t("📤 Use in another chat", "📤 Использовать в другом чате", "📤 Başka sohbette kullan"),
    # ── /start status ──
    "👑 <b>Creator</b> — idarə paneli: /menu": _t("👑 <b>Creator</b> — admin panel: /menu",
                                                  "👑 <b>Creator</b> — панель управления: /menu",
                                                  "👑 <b>Creator</b> — yönetim paneli: /menu"),
    "ömürlük": _t("lifetime", "навсегда", "ömür boyu"),
    "{date}-dək": _t("until {date}", "до {date}", "{date} tarihine kadar"),
    "💎 <b>Premium</b> ({until}) — mahnılar yazısız gəlir": _t("💎 <b>Premium</b> ({until}) — songs come without a caption",
                                                              "💎 <b>Premium</b> ({until}) — песни без подписи",
                                                              "💎 <b>Premium</b> ({until}) — şarkılar yazısız gelir"),
    "💎 Premium ilə mahnılar \"via @dllmasterbot\" yazısı olmadan gəlir → /premium": _t(
        "💎 With Premium, songs come without the \"via @dllmasterbot\" caption → /premium",
        "💎 С Premium песни приходят без подписи \"via @dllmasterbot\" → /premium",
        "💎 Premium ile şarkılar \"via @dllmasterbot\" yazısı olmadan gelir → /premium"),
    "👑 <b>Sənin statusun: Creator</b> — bütün imkanlar açıqdır.": _t(
        "👑 <b>Your status: Creator</b> — everything is unlocked.", "👑 <b>Твой статус: Creator</b> — всё открыто.",
        "👑 <b>Durumun: Creator</b> — tüm özellikler açık."),
    "📌 <b>Sənin statusun: 💎 Premium</b> ({until})": _t("📌 <b>Your status: 💎 Premium</b> ({until})",
                                                        "📌 <b>Твой статус: 💎 Premium</b> ({until})",
                                                        "📌 <b>Durumun: 💎 Premium</b> ({until})"),
    "📌 <b>Sənin statusun: 🆓 Free</b>": _t("📌 <b>Your status: 🆓 Free</b>", "📌 <b>Твой статус: 🆓 Free</b>",
                                           "📌 <b>Durumun: 🆓 Free</b>"),
    "♾ <i>Ömürlük premiumun var — heç nə almağa ehtiyac yoxdur.</i>": _t(
        "♾ <i>You have lifetime premium — nothing to buy.</i>", "♾ <i>У тебя пожизненный премиум — покупать ничего не нужно.</i>",
        "♾ <i>Ömür boyu premiumun var — bir şey almana gerek yok.</i>"),
    "<i>Premium hazırda satışda deyil. Ətraflı: @{creator}</i>": _t(
        "<i>Premium isn't on sale right now. Details: @{creator}</i>", "<i>Premium сейчас не продаётся. Подробнее: @{creator}</i>",
        "<i>Premium şu an satışta değil. Detay: @{creator}</i>"),
    "ulduz": _t("stars", "звёзд", "yıldız"),
    "⭐ <b>Uzatmaq üçün plan seç:</b>": _t("⭐ <b>Choose a plan to extend:</b>", "⭐ <b>Выбери план для продления:</b>",
                                           "⭐ <b>Uzatmak için plan seç:</b>"),
    "⭐ <b>Plan seç:</b>": _t("⭐ <b>Choose a plan:</b>", "⭐ <b>Выбери план:</b>", "⭐ <b>Plan seç:</b>"),
    "✏️ Öz məbləğim": _t("✏️ Custom amount", "✏️ Своя сумма", "✏️ Kendi tutarım"),
    "⛔ Yalnız qrup adminləri bağlaya bilər": _t("⛔ Only group admins can close this", "⛔ Закрыть могут только админы группы",
                                                "⛔ Yalnızca grup yöneticileri kapatabilir"),
    "❌ Bağlandı": _t("❌ Closed", "❌ Закрыто", "❌ Kapatıldı"),
    # ── axtarış ──
    "<i>🔍 Axtarılır...</i>": _t("<i>🔍 Searching...</i>", "<i>🔍 Ищу...</i>", "<i>🔍 Aranıyor...</i>"),
    "Nəticə tapılmadı": _t("Nothing found", "Ничего не найдено", "Sonuç bulunamadı"),
    "🎵 Tapılan Mahnılar": _t("🎵 Songs found", "🎵 Найденные песни", "🎵 Bulunan şarkılar"),
    " (Səhifə {page}/{pages})": _t(" (Page {page}/{pages})", " (Стр. {page}/{pages})", " (Sayfa {page}/{pages})"),
    "<i>📦 — depoda var, dərhal göndərilir</i>": _t("<i>📦 — in storage, sent instantly</i>",
                                                   "<i>📦 — в хранилище, отправляется сразу</i>",
                                                   "<i>📦 — depoda var, hemen gönderilir</i>"),
    "{n} nəticə": _t("{n} results", "результатов: {n}", "{n} sonuç"),
    " · ▶️ ilə daha çox gəlir": _t(" · ▶️ for more", " · ▶️ — ещё", " · ▶️ ile daha fazlası"),
    " · hamısı göstərilir": _t(" · all shown", " · показаны все", " · hepsi gösteriliyor"),
    "İrəli ▶️": _t("Next ▶️", "Далее ▶️", "İleri ▶️"),
    "⬇️ Hamısını yüklə ({n})": _t("⬇️ Download all ({n})", "⬇️ Скачать все ({n})", "⬇️ Hepsini indir ({n})"),
    "Bu siyahı köhnəlib — yenidən axtar": _t("This list has expired — search again", "Список устарел — ищи заново",
                                            "Bu liste eskidi — yeniden ara"),
    "⏳ Daha çox nəticə axtarılır...": _t("⏳ Looking for more results...", "⏳ Ищу ещё...", "⏳ Daha fazla sonuç aranıyor..."),
    "Başqa nəticə yoxdur — hamısı göstərildi": _t("No more results — all shown", "Больше нет — показано всё",
                                                 "Başka sonuç yok — hepsi gösterildi"),
    "⏳ Yüklənir...": _t("⏳ Loading...", "⏳ Загрузка...", "⏳ Yükleniyor..."),
    "❌ <b>Axtarış ləğv edildi.</b>": _t("❌ <b>Search cancelled.</b>", "❌ <b>Поиск отменён.</b>", "❌ <b>Arama iptal edildi.</b>"),
    "❌ <b>Yanlış seçim!</b>": _t("❌ <b>Invalid choice!</b>", "❌ <b>Неверный выбор!</b>", "❌ <b>Geçersiz seçim!</b>"),
    "❌ <b>Xəta:</b> <code>{e}</code>": _t("❌ <b>Error:</b> <code>{e}</code>", "❌ <b>Ошибка:</b> <code>{e}</code>",
                                         "❌ <b>Hata:</b> <code>{e}</code>"),
    "<b>🎵 Musiqi axtarışı istifadə qaydası:</b>\n👉 <code>/music &lt;mahnı adı&gt;</code>\n<i>Nümunə:</i> <code>/music Eminem Lose Yourself</code>":
        _t("<b>🎵 Music search usage:</b>\n👉 <code>/music &lt;song name&gt;</code>\n<i>Example:</i> <code>/music Eminem Lose Yourself</code>",
           "<b>🎵 Как искать музыку:</b>\n👉 <code>/music &lt;название песни&gt;</code>\n<i>Пример:</i> <code>/music Eminem Lose Yourself</code>",
           "<b>🎵 Müzik arama kullanımı:</b>\n👉 <code>/music &lt;şarkı adı&gt;</code>\n<i>Örnek:</i> <code>/music Eminem Lose Yourself</code>"),
    "Nümunə:": _t("Example:", "Пример:", "Örnek:"),
    "bu mahnı": _t("this song", "эта песня", "bu şarkı"),
    "👉 <code>/mix mahnı adı</code>\n👉 <code>/mix YouTube linki</code>\n👉 və ya istənilən mahnıya <b>cavab (reply)</b> olaraq <code>/mix</code> və ya sadəcə <code>mix</code> yaz":
        _t("👉 <code>/mix song name</code>\n👉 <code>/mix YouTube link</code>\n👉 or <b>reply</b> <code>/mix</code> (or just <code>mix</code>) to any song",
           "👉 <code>/mix название песни</code>\n👉 <code>/mix ссылка YouTube</code>\n👉 или <b>ответь</b> <code>/mix</code> (или просто <code>mix</code>) на любую песню",
           "👉 <code>/mix şarkı adı</code>\n👉 <code>/mix YouTube bağlantısı</code>\n👉 veya herhangi bir şarkıya <b>yanıt</b> olarak <code>/mix</code> ya da sadece <code>mix</code> yaz"),
    "⬇️ {n} mahnı yüklənir...": _t("⬇️ Downloading {n} songs...", "⬇️ Загружаю песен: {n}...", "⬇️ {n} şarkı indiriliyor..."),
    "⏭ {n} uzun video (15 dəq.+) ötürüldü": _t("⏭ {n} long videos (15 min+) skipped", "⏭ пропущено длинных видео (15+ мин): {n}",
                                              "⏭ {n} uzun video (15 dk+) atlandı"),
    "⏳ <b>Yüklənir:</b>": _t("⏳ <b>Downloading:</b>", "⏳ <b>Загрузка:</b>", "⏳ <b>İndiriliyor:</b>"),
    "⬇️ <b>Yüklənir</b>": _t("⬇️ <b>Downloading</b>", "⬇️ <b>Загрузка</b>", "⬇️ <b>İndiriliyor</b>"),
    "paralel": _t("parallel", "параллельно", "paralel"),
    "❌ <b>Yüklənmədi:</b>": _t("❌ <b>Not downloaded:</b>", "❌ <b>Не загружено:</b>", "❌ <b>İndirilemedi:</b>"),
    "⏹ <b>Yükləmə dayandırıldı</b>": _t("⏹ <b>Download stopped</b>", "⏹ <b>Загрузка остановлена</b>", "⏹ <b>İndirme durduruldu</b>"),
    "… və daha {n}": _t("… and {n} more", "… и ещё {n}", "… ve {n} tane daha"),
    "cəmi": _t("total", "всего", "toplam"),
    # ── yükləmə ──
    "⏹ Yükləməni dayandır": _t("⏹ Stop download", "⏹ Остановить загрузку", "⏹ İndirmeyi durdur"),
    "⏹ Dayandır": _t("⏹ Stop", "⏹ Стоп", "⏹ Durdur"),
    "🙈 Gizlət": _t("🙈 Hide", "🙈 Скрыть", "🙈 Gizle"),
    "🙈 Gizlədildi": _t("🙈 Hidden", "🙈 Скрыто", "🙈 Gizlendi"),
    "Gizlətmək alınmadı": _t("Couldn't hide", "Не удалось скрыть", "Gizlenemedi"),
    "📦 Depodan göndərilir": _t("📦 Sending from storage", "📦 Отправляю из хранилища", "📦 Depodan gönderiliyor"),
    "⏳ Yüklənir": _t("⏳ Downloading", "⏳ Загружается", "⏳ İndiriliyor"),
    "⏳ <i>Mahnı hazırlanır...</i>": _t("⏳ <i>Preparing the song...</i>", "⏳ <i>Готовлю песню...</i>",
                                       "⏳ <i>Şarkı hazırlanıyor...</i>"),
    "Yükləmə artıq bitib": _t("The download has already finished", "Загрузка уже завершена", "İndirme zaten bitti"),
    "⛔ Yalnız mahnını istəyən dayandıra bilər": _t("⛔ Only the person who requested it can stop it",
                                                   "⛔ Остановить может только тот, кто запросил",
                                                   "⛔ Yalnızca isteyen kişi durdurabilir"),
    "⛔ Yalnız mahnını göndərən gizlədə bilər": _t("⛔ Only the sender can hide it", "⛔ Скрыть может только отправитель",
                                                  "⛔ Yalnızca gönderen gizleyebilir"),
    "⛔ Yalnız mahnını göndərən dayandıra bilər": _t("⛔ Only the sender can stop it", "⛔ Остановить может только отправитель",
                                                    "⛔ Yalnızca gönderen durdurabilir"),
    "⛔ Yalnız yükləməni başladan dayandıra bilər": _t("⛔ Only whoever started the download can stop it",
                                                      "⛔ Остановить может только тот, кто начал загрузку",
                                                      "⛔ Yalnızca indirmeyi başlatan durdurabilir"),
    "⏹ <i>Dayandırılır...</i>": _t("⏹ <i>Stopping...</i>", "⏹ <i>Останавливаю...</i>", "⏹ <i>Durduruluyor...</i>"),
    "⏹ Dayandırılır...": _t("⏹ Stopping...", "⏹ Останавливаю...", "⏹ Durduruluyor..."),
    "⏹ <b>Yükləmə dayandırıldı:</b>\n{title}": _t("⏹ <b>Download stopped:</b>\n{title}", "⏹ <b>Загрузка остановлена:</b>\n{title}",
                                                   "⏹ <b>İndirme durduruldu:</b>\n{title}"),
    "❌ Mahnı hazır olmadı (yükləmə dayandırılıb və ya alınmayıb).\n<i>Mahnı gələndə yenidən seç.</i>":
        _t("❌ The song isn't ready (download stopped or failed).\n<i>Pick it again later.</i>",
           "❌ Песня не готова (загрузка остановлена или не удалась).\n<i>Выбери её ещё раз позже.</i>",
           "❌ Şarkı hazır değil (indirme durduruldu veya başarısız).\n<i>Daha sonra tekrar seç.</i>"),
    "Yüklənir, bir az gözləyin...": _t("Downloading, please wait...", "Загружается, подождите...",
                                       "İndiriliyor, biraz bekleyin..."),
    # ── toplu yükləmə ──
    "💎 Hamısını birdən yükləmək yalnız Premium üçündür": _t("💎 Downloading everything at once is Premium only",
                                                            "💎 Скачивание всего сразу — только для Premium",
                                                            "💎 Hepsini birden indirmek yalnızca Premium içindir"),
    "Artıq bir toplu yükləmə gedir — bitməsini gözlə və ya ⏹ Dayandır": _t(
        "A bulk download is already running — wait or ⏹ Stop", "Уже идёт массовая загрузка — подожди или ⏹ Стоп",
        "Zaten toplu bir indirme var — bekle veya ⏹ Durdur"),
    "Yüklənəcək mahnı yoxdur": _t("Nothing to download", "Нечего скачивать", "İndirilecek şarkı yok"),
    "⏹ <b>Dayandırıldı</b>": _t("⏹ <b>Stopped</b>", "⏹ <b>Остановлено</b>", "⏹ <b>Durduruldu</b>"),
    "✅ <b>Hamısı göndərildi</b>": _t("✅ <b>All sent</b>", "✅ <b>Всё отправлено</b>", "✅ <b>Hepsi gönderildi</b>"),
    "<b>Yüklənməyənlər:</b>": _t("<b>Not downloaded:</b>", "<b>Не загружены:</b>", "<b>İndirilemeyenler:</b>"),
    # ── Mix ──
    "<i>🔀 Oxşar mahnılar axtarılır...</i>": _t("<i>🔀 Looking for similar songs...</i>", "<i>🔀 Ищу похожие песни...</i>",
                                               "<i>🔀 Benzer şarkılar aranıyor...</i>"),
    "Bu mahnı üçün Mix tapılmadı": _t("No Mix found for this song", "Для этой песни Mix не найден", "Bu şarkı için Mix bulunamadı"),
    "Mahnı tapılmadı": _t("Song not found", "Песня не найдена", "Şarkı bulunamadı"),
    "<b>🔀 Mix — oxşar mahnılar</b>": _t("<b>🔀 Mix — similar songs</b>", "<b>🔀 Mix — похожие песни</b>",
                                        "<b>🔀 Mix — benzer şarkılar</b>"),
    # ── Premium / imkanlar ──
    "💎 <b>{name} — yalnız Premium üçündür</b>": _t("💎 <b>{name} — Premium only</b>", "💎 <b>{name} — только для Premium</b>",
                                                   "💎 <b>{name} — yalnızca Premium</b>"),
    "✨ <b>Premium ilə əldə edəcəklərin:</b>": _t("✨ <b>What you get with Premium:</b>", "✨ <b>Что даёт Premium:</b>",
                                                 "✨ <b>Premium ile kazanacakların:</b>"),
    "⭐ Telegram Stars ilə bir neçə saniyəyə aktivləşir — /premium": _t(
        "⭐ Activates in seconds with Telegram Stars — /premium", "⭐ Активируется за секунды через Telegram Stars — /premium",
        "⭐ Telegram Stars ile saniyeler içinde etkinleşir — /premium"),
    "<i>Hazırda Premium və Free eyni imkanlara malikdir.</i>": _t(
        "<i>Premium and Free currently have the same features.</i>", "<i>Сейчас у Premium и Free одинаковые возможности.</i>",
        "<i>Şu anda Premium ve Free aynı özelliklere sahip.</i>"),
    "🟢 <b>Spotify linkləri</b> — mahnı, albom, playlist, ifaçı linkini göndər, yüklənsin": _t(
        "🟢 <b>Spotify links</b> — send a track, album, playlist or artist link to download",
        "🟢 <b>Ссылки Spotify</b> — трек, альбом, плейлист или исполнитель",
        "🟢 <b>Spotify bağlantıları</b> — parça, albüm, çalma listesi, sanatçı bağlantısı gönder"),
    "▶️ <b>YouTube linkləri</b> — video, playlist, Mix linkindən mahnı yüklə": _t(
        "▶️ <b>YouTube links</b> — download from a video, playlist or Mix link",
        "▶️ <b>Ссылки YouTube</b> — скачивание из видео, плейлиста или Mix",
        "▶️ <b>YouTube bağlantıları</b> — video, çalma listesi, Mix bağlantısından indir"),
    "⬇️ <b>Hamısını yüklə</b> — siyahıdakı bütün mahnılar bir toxunuşla": _t(
        "⬇️ <b>Download all</b> — every song in the list with one tap",
        "⬇️ <b>Скачать все</b> — все песни списка в одно касание",
        "⬇️ <b>Hepsini indir</b> — listedeki tüm şarkılar tek dokunuşla"),
    "🎵 Mahnılar <b>\"via | song.link\" yazısı olmadan</b>, təmiz gəlir": _t(
        "🎵 Songs come clean, <b>without the \"via | song.link\" caption</b>",
        "🎵 Песни приходят <b>без подписи \"via | song.link\"</b>",
        "🎵 Şarkılar <b>\"via | song.link\" yazısı olmadan</b>, temiz gelir"),
    "🔕 <b>Elan / reklam mesajları gəlmir</b>": _t("🔕 <b>No announcements / ads</b>", "🔕 <b>Без объявлений / рекламы</b>",
                                                 "🔕 <b>Duyuru / reklam mesajı gelmez</b>"),
    "🟢 Spotify linkləri": _t("🟢 Spotify links", "🟢 Ссылки Spotify", "🟢 Spotify bağlantıları"),
    "▶️ YouTube linkləri": _t("▶️ YouTube links", "▶️ Ссылки YouTube", "▶️ YouTube bağlantıları"),
    "⬇️ Hamısını yüklə": _t("⬇️ Download all", "⬇️ Скачать все", "⬇️ Hepsini indir"),
    "🔗 via | song.link yazısı": _t("🔗 via | song.link caption", "🔗 подпись via | song.link", "🔗 via | song.link yazısı"),
    "📢 Broadcast mesajları": _t("📢 Announcements", "📢 Рассылки", "📢 Duyuru mesajları"),
    "🟢 Spotify mahnı/albom": _t("🟢 Spotify track/album", "🟢 Трек/альбом Spotify", "🟢 Spotify parça/albüm"),
    "🟢 Spotify playlistləri": _t("🟢 Spotify playlists", "🟢 Плейлисты Spotify", "🟢 Spotify çalma listeleri"),
    "▶️ YouTube mahnı/video": _t("▶️ YouTube track/video", "▶️ Трек/видео YouTube", "▶️ YouTube parça/video"),
    "▶️ YouTube playlistləri": _t("▶️ YouTube playlists", "▶️ Плейлисты YouTube", "▶️ YouTube çalma listeleri"),
    "🔀 Mix (Oxşar mahnılar)": _t("🔀 Mix (Similar tracks)", "🔀 Mix (Похожие треки)", "🔀 Mix (Benzer şarkılar)"),
    "🎙 İfaçı axtarışı": _t("🎙 Artist search", "🎙 Поиск исполнителя", "🎙 Sanatçı arama"),
    "🎶 Playlistlər": _t("🎶 Playlists", "🎶 Плейлисты", "🎶 Çalma listeleri"),
    "💾 Linkdən playlist": _t("💾 Playlist from link", "💾 Плейлист по ссылке", "💾 Bağlantıdan çalma listesi"),
    "▶️ Playlisti birdən yüklə": _t("▶️ Download entire playlist", "▶️ Скачать весь плейлист", "▶️ Çalma listesini toplu indir"),
    "🟢 <b>Spotify mahnıları</b> — mahnı və albom linklərindən yükləmə": _t("🟢 <b>Spotify tracks</b> — download from track and album links", "🟢 <b>Треки Spotify</b> — скачивание по ссылкам на треки и альбомы", "🟢 <b>Spotify parçaları</b> — parça ve albüm bağlantılarından indirme"),
    "🟢 <b>Spotify playlistləri</b> — bütün Spotify playlistləri bir toxunuşla yüklənsin": _t("🟢 <b>Spotify playlists</b> — download any Spotify playlist with one tap", "🟢 <b>Плейлисты Spotify</b> — скачивание любых плейлистов Spotify в одно касание", "🟢 <b>Spotify çalma listeleri</b> — tüm Spotify çalma listeleri tek dokunuşla indirilsin"),
    "▶️ <b>YouTube linkləri</b> — video və musiqi linklərindən yükləmə": _t("▶️ <b>YouTube links</b> — download from video and music links", "▶️ <b>Ссылки YouTube</b> — скачивание по ссылкам на видео и музыку", "▶️ <b>YouTube bağlantıları</b> — video ve müzik bağlantılarından indirme"),
    "▶️ <b>YouTube playlistləri</b> — bütün YouTube playlisti bir toxunuşla yüklənsin": _t("▶️ <b>YouTube playlists</b> — download entire YouTube playlists with one tap", "▶️ <b>Плейлисты YouTube</b> — скачивание плейлистов YouTube в одно касание", "▶️ <b>YouTube çalma listeleri</b> — tüm YouTube çalma listesi tek dokunuşla indirilsin"),
    "🔀 <b>Mix (Oxşar mahnılar)</b> — sevdiyin mahnıya uyğun avtomatik playlist": _t("🔀 <b>Mix (Similar tracks)</b> — automatic playlist tailored to songs you love", "🔀 <b>Mix (Похожие треки)</b> — автоплейлист на основе ваших любимых песен", "🔀 <b>Mix (Benzer şarkılar)</b> — sevdiğin şarkıya uygun otomatik liste"),
    "🎙 <b>İfaçı axtarışı</b> — ifaçının bütün mahnıları bir siyahıda": _t("🎙 <b>Artist search</b> — all songs by the artist in one list", "🎙 <b>Поиск исполнителя</b> — все песни исполнителя в одном списке", "🎙 <b>Sanatçı arama</b> — sanatçının tüm şarkıları tek listede"),
    "🎶 <b>Playlistlər</b> — sevdiyin mahnıları öz siyahılarına yığ": _t("🎶 <b>Playlists</b> — save your favorite songs into custom playlists", "🎶 <b>Плейлисты</b> — собирай любимые песни в свои плейлисты", "🎶 <b>Çalma listeleri</b> — sevdiğin şarkıları kendi listelerine topla"),
    "💾 <b>Linkdən playlist</b> — YouTube / Spotify playlistini bir toxunuşla özünə saxla": _t("💾 <b>Playlist from link</b> — save YouTube / Spotify playlist to your collection with one tap", "💾 <b>Плейлист по ссылке</b> — сохрани плейлист YouTube / Spotify себе в одно касание", "💾 <b>Bağlantıdan çalma listesi</b> — YouTube / Spotify çalma listesini tek dokunuşla kendine kaydet"),
    "▶️ <b>Playlisti birdən yüklə</b> — bütün playlist bir toxunuşla": _t("▶️ <b>Download playlist</b> — entire playlist with one tap", "▶️ <b>Скачать весь плейлист</b> — весь плейлист в одно касание", "▶️ <b>Çalma listesini toplu indir</b> — tüm çalma listesi tek dokunuşla"),
    "✏️ <b>Metadata redaktə</b> — mahnının adını, artistini və şəklini dəyiş": _t("✏️ <b>Edit metadata</b> — change title, artist and cover of any song", "✏️ <b>Редактирование тегов</b> — меняй название, исполнителя и обложку трека", "✏️ <b>Bilgileri düzenle</b> — şarkının adını, sanatçısını ve kapağını değiştir"),
    "🎶 <b>Daha çox playlist</b> — {pc} playlist, hər birində {ps} mahnıya qədər (Free: {fc} × {fs})": _t("🎶 <b>More playlists</b> — {pc} playlists, up to {ps} songs each (Free: {fc} × {fs})", "🎶 <b>Больше плейлистов</b> — {pc} плейлистов, до {ps} треков в каждом (Free: {fc} × {fs})", "🎶 <b>Daha fazla çalma listesi</b> — {pc} çalma listesi, her birinde en fazla {ps} şarkı (Free: {fc} × {fs})"),
    "👑 <b>{name}</b> sənin üçün bağlıdır — /menu → 💎 Premium → 🎛 İmkanlar": _t("👑 <b>{name}</b> is disabled for you — /menu → 💎 Premium → 🎛 Features", "👑 <b>{name}</b> отключено для вас — /menu → 💎 Premium → 🎛 Возможности", "👑 <b>{name}</b> senin için kapalı — /menu → 💎 Premium → 🎛 Özellikler"),
    "⛔ <b>{name}</b> hazırda bağlıdır.": _t("⛔ <b>{name}</b> is currently disabled.", "⛔ <b>{name}</b> сейчас отключено.", "⛔ <b>{name}</b> şu anda kapalı."),
    "🎶 <b>Playlistlərim</b>": _t("🎶 <b>My Playlists</b>", "🎶 <b>Мои плейлисты</b>", "🎶 <b>Çalma Listelerim</b>"),
    "<i>Hələ playlistin yoxdur.</i>\n\nMahnının altındakı <b>➕</b> düyməsi ilə və ya <b>➕ Yeni playlist</b> ilə başla.": _t("<i>You don't have any playlists yet.</i>\n\nStart with the <b>➕</b> button under a song or <b>➕ New playlist</b>.", "<i>У тебя пока нет плейлистов.</i>\n\nНачни с кнопки <b>➕</b> под песней или <b>➕ Новый плейлист</b>.", "<i>Henüz bir çalma listen yok.</i>\n\nŞarkının altındaki <b>➕</b> düğmesiyle veya <b>➕ Yeni çalma listesi</b> ile başla."),
    "YouTube / Spotify playlist linki göndərib <b>💾 Playlist kimi saxla</b> da edə bilərsən.": _t("You can also send a YouTube / Spotify playlist link and tap <b>💾 Save as playlist</b>.", "Также можешь отправить ссылку на плейлист YouTube / Spotify и нажать <b>💾 Сохранить как плейлист</b>.", "Ayrıca YouTube / Spotify çalma listesi bağlantısı gönderip <b>💾 Çalma listesi olarak kaydet</b> de yapabilirsin."),
    "Hər playlistdə {n} mahnıya qədər.": _t("Up to {n} songs in each playlist.", "До {n} треков в каждом плейлисте.", "Her çalma listesinde en fazla {n} şarkı."),
    "➕ Yeni playlist": _t("➕ New playlist", "➕ Новый плейлист", "➕ Yeni çalma listesi"),
    "❌ Bağla": _t("❌ Close", "❌ Закрыть", "❌ Kapat"),
    "🎵 {n} mahnı": _t("🎵 {n} songs", "🎵 {n} треков", "🎵 {n} şarkı"),
    "səhifə": _t("page", "стр.", "sayfa"),
    "<i>Boşdur — mahnının altındakı ➕ ilə və ya mahnıya reply edib /add yazaraq əlavə et.</i>": _t("<i>Empty — add songs with ➕ under a song or by replying with /add.</i>", "<i>Пусто — добавь треки кнопкой ➕ под песней или ответив /add на песню.</i>", "<i>Boş — şarkının altındaki ➕ ile veya şarkıya yanıt olarak /add yazarak ekle.</i>"),
    "✂️ <b>Silmək istədiyin mahnıya toxun</b>": _t("✂️ <b>Tap the song you want to remove</b>", "✂️ <b>Нажми на трек, который хочешь удалить</b>", "✂️ <b>Silmek istediğin şarkıya dokun</b>"),
    "✅ Hazır": _t("✅ Done", "✅ Готово", "✅ Bitti"),
    "🔀 Qarışıq": _t("🔀 Shuffle", "🔀 Вперемешку", "🔀 Karışık"),
    "✏️ Ad": _t("✏️ Rename", "✏️ Название", "✏️ Ad"),
    "✂️ Mahnı sil": _t("✂️ Remove track", "✂️ Удалить трек", "✂️ Şarkı sil"),
    "🗑 Sil": _t("🗑 Delete", "🗑 Удалить", "🗑 Sil"),
    "⬅️ Playlistlər": _t("⬅️ Playlists", "⬅️ Плейлисты", "⬅️ Çalma listeleri"),
    "➕ <b>{t}</b>\n\nHansı playlistə əlavə edək?": _t("➕ <b>{t}</b>\n\nWhich playlist should we add it to?", "➕ <b>{t}</b>\n\nВ какой плейлист добавить?", "➕ <b>{t}</b>\n\nHangi çalma listesine ekleyelim?"),
    "✅ <b>{name}</b> playlistinə əlavə olundu": _t("✅ Added to playlist <b>{name}</b>", "✅ Добавлено в плейлист <b>{name}</b>", "✅ <b>{name}</b> çalma listesine eklendi"),
    "✅ <b>{name}</b> playlistinə {n} mahnı əlavə olundu": _t("✅ Added {n} songs to playlist <b>{name}</b>", "✅ Добавлено {n} треков в плейлист <b>{name}</b>", "✅ <b>{name}</b> çalma listesine {n} şarkı eklendi"),
    "ℹ️ Bu mahnı artıq <b>{name}</b> playlistindədir": _t("ℹ️ This song is already in <b>{name}</b>", "ℹ️ Этот трек уже в плейлисте <b>{name}</b>", "ℹ️ Bu şarkı zaten <b>{name}</b> çalma listesinde"),
    "⚠️ <b>{name}</b> doludur": _t("⚠️ <b>{name}</b> is full", "⚠️ <b>{name}</b> заполнен", "⚠️ <b>{name}</b> dolu"),
    "<i>{n} mahnı artıq orada idi</i>": _t("<i>{n} songs were already there</i>", "<i>{n} треков уже было там</i>", "<i>{n} şarkı zaten oradaydı</i>"),
    "⚠️ <i>Limit dolduğu üçün {n} mahnı əlavə olunmadı</i>": _t("⚠️ <i>{n} songs not added because the limit was reached</i>", "⚠️ <i>{n} треков не добавлено из-за лимита</i>", "⚠️ <i>Limit dolduğu için {n} şarkı eklenmedi</i>"),
    "❌ Playlist tapılmadı": _t("❌ Playlist not found", "❌ Плейлист не найден", "❌ Çalma listesi bulunamadı"),
    "⚠️ Playlist limiti doludur ({n}).": _t("⚠️ Playlist limit reached ({n}).", "⚠️ Лимит плейлистов исчерпан ({n}).", "⚠️ Çalma listesi limiti dolu ({n})."),
    "💎 Premium ilə daha çox — /premium": _t("💎 Get more with Premium — /premium", "💎 Больше с Premium — /premium", "💎 Premium ile daha fazlası — /premium"),
    "❌ Bu mahnını tanımadım — yalnız botun göndərdiyi mahnılar əlavə oluna bilər.": _t("❌ Couldn't recognize this song — only songs sent by the bot can be added.", "❌ Не распознал трек — добавлять можно только песни, отправленные ботом.", "❌ Bu şarkıyı tanıyamadım — yalnızca botun gönderdiği şarkılar eklenebilir."),
    "Əvvəlcə botu şəxsi çatda başlat (/start)": _t("Start the bot in private chat first (/start)", "Сначала запусти бота в личном чате (/start)", "Önce botu özel sohbette başlat (/start)"),
    "❤️ Sevimlilər": _t("❤️ Favorites", "❤️ Избранное", "❤️ Favoriler"),
    "✏️ Ad dəyişdi": _t("✏️ Name updated", "✏️ Название изменено", "✏️ Ad güncellendi"),
    "✅ Playlist yaradıldı": _t("✅ Playlist created", "✅ Плейлист создан", "✅ Çalma listesi oluşturuldu"),
    "Bu mahnı əlavə oluna bilmir": _t("This song cannot be added", "Этот трек нельзя добавить", "Bu şarkı eklenemiyor"),
    "Köhnəlib — ➕-a yenidən toxun": _t("Expired — tap ➕ again", "Устарело — нажми ➕ снова", "Eskidi — ➕ düğmesine tekrar dokun"),
    "Ləğv edildi": _t("Cancelled", "Отменено", "İptal edildi"),
    "✍️ Yeni playlistin adını yaz:": _t("✍️ Enter a name for the new playlist:", "✍️ Напиши название нового плейлиста:", "✍️ Yeni çalma listesinin adını yaz:"),
    "🎶 Playlistlər bağlıdır": _t("🎶 Playlists are disabled", "🎶 Плейлисты отключены", "🎶 Çalma listeleri kapalı"),
    "⚠️ Playlist limiti doludur ({n})": _t("⚠️ Playlist limit reached ({n})", "⚠️ Лимит плейлистов исчерпан ({n})", "⚠️ Çalma listesi limiti dolu ({n})"),
    "🗑 Silindi": _t("🗑 Deleted", "🗑 Удалено", "🗑 Silindi"),
    "Artıq yoxdur": _t("No longer exists", "Больше не существует", "Artık yok"),
    "✍️ <b>{name}</b> üçün yeni ad yaz:": _t("✍️ Enter a new name for <b>{name}</b>:", "✍️ Напиши новое название для <b>{name}</b>:", "✍️ <b>{name}</b> için yeni ad yaz:"),
    "🗑 <b>{name}</b> ({n} mahnı) silinsin?": _t("🗑 Delete <b>{name}</b> ({n} songs)?", "🗑 Удалить <b>{name}</b> ({n} треков)?", "🗑 <b>{name}</b> ({n} şarkı) silinsin mi?"),
    "✅ Bəli, sil": _t("✅ Yes, delete", "✅ Да, удалить", "✅ Evet, sil"),
    "❌ Xeyr": _t("❌ No", "❌ Нет", "❌ Hayır"),
    "Yükləmə modulu yüklənməyib": _t("Download module not loaded", "Модуль загрузки не загружен", "İndirme modülü yüklü değil"),
    "💎 Playlisti birdən yükləmək Premium üçündür": _t("💎 Bulk downloading a playlist is Premium only", "💎 Скачивание плейлиста целиком доступно только в Premium", "💎 Çalma listesini toplu indirmek yalnızca Premium içindir"),
    "💾 Saxlanmış playlist": _t("💾 Saved playlist", "💾 Сохранённый плейлист", "💾 Kaydedilen çalma listesi"),
    "🎶 /playlist — playlistlərin": _t("🎶 /playlist — your playlists", "🎶 /playlist — твои плейлисты", "🎶 /playlist — çalma listelerin"),
    "Mənbələr": _t("Sources", "Источники", "Kaynaklar"),
    "📦 {n} mahnı artıq depoda — dərhal gəlir": _t("📦 {n} songs already in storage — arriving instantly", "📦 {n} треков уже в хранилище — отправляются мгновенно", "📦 {n} şarkı zaten depoda — anında geliyor"),
    "📋 Mahnılar ({n})": _t("📋 Tracks ({n})", "📋 Треки ({n})", "📋 Şarkılar ({n})"),
    "🔥 Top ({n})": _t("🔥 Top ({n})", "🔥 Топ ({n})", "🔥 Popüler ({n})"),
    "💾 Playlist kimi saxla": _t("💾 Save as playlist", "💾 Сохранить как плейлист", "💾 Çalma listesi olarak kaydet"),
    "bütün mahnılar": _t("all songs", "все треки", "tüm şarkılar"),
    "top": _t("top", "топ", "popüler"),
    "<i>Mahnıya toxun — yüklənir. 📦 = depoda var.</i>": _t("<i>Tap a song to download. 📦 = in storage.</i>", "<i>Нажми на трек для скачивания. 📦 = есть в хранилище.</i>", "<i>Şarkıya dokun — indirilir. 📦 = depoda var.</i>"),
    "❌ <b>YouTube Music cavab vermədi</b> — bağlantı qırıldı (şəbəkə / YouTube tərəfi). Bir azdan yenidən yoxla.": _t("❌ <b>YouTube Music did not respond</b> — connection dropped. Please try again in a moment.", "❌ <b>YouTube Music не ответил</b> — соединение разорвано. Попробуйте снова чуть позже.", "❌ <b>YouTube Music yanıt vermedi</b> — bağlantı koptu. Lütfen biraz sonra tekrar deneyin."),
    "🔎 <i>İfaçı axtarılır...</i>": _t("🔎 <i>Searching artist...</i>", "🔎 <i>Ищу исполнителя...</i>", "🔎 <i>Sanatçı aranıyor...</i>"),
    "❌ <b>{q}</b> adlı ifaçı tapılmadı.": _t("❌ Artist named <b>{q}</b> not found.", "❌ Исполнитель <b>{q}</b> не найден.", "❌ <b>{q}</b> adlı sanatçı bulunamadı."),
    "🎙 <b>{q}</b> — hansı ifaçı?": _t("🎙 <b>{q}</b> — which artist?", "🎙 <b>{q}</b> — какой исполнитель?", "🎙 <b>{q}</b> — hangi sanatçı?"),
    "İfaçı": _t("Artist", "Исполнитель", "Sanatçı"),
    "profil": _t("profile", "профиль", "profil"),
    "siyahıda": _t("in list", "в списке", "listede"),
    "mahnı": _t("songs", "песен", "şarkı"),
    "san.": _t("sec.", "сек.", "sn."),
    "⚠️ <b>{n} san.-dir irəliləyiş yoxdur — ilişib kimi görünür</b>": _t("⚠️ <b>No progress for {n} sec — appears stuck</b>", "⚠️ <b>Нет прогресса {n} сек — похоже, зависло</b>", "⚠️ <b>{n} sn. boyunca ilerleme yok — takılmış görünüyor</b>"),
    "<i>YouTube Music cavab vermir. Yenidən başlat və ya toplananı göstər.</i>": _t("<i>YouTube Music is not responding. Restart or show collected songs.</i>", "<i>YouTube Music не отвечает. Перезапустите или покажите собранное.</i>", "<i>YouTube Music yanıt vermiyor. Yeniden başlatın veya toplananı gösterin.</i>"),
    "🔄 Yenidən başlat": _t("🔄 Restart", "🔄 Перезапустить", "🔄 Yeniden başlat"),
    "⏭ Olanı göstər": _t("⏭ Show collected", "⏭ Показать собранное", "⏭ Olanı göster"),
    "✖️ Ləğv et": _t("✖️ Cancel", "✖️ Отмена", "✖️ İptal"),
    "❌ <b>Çox uzun çəkdi</b> — YouTube Music cavab vermir.": _t("❌ <b>Timed out</b> — YouTube Music is not responding.", "❌ <b>Слишком долго</b> — YouTube Music не отвечает.", "❌ <b>Çok uzun sürdü</b> — YouTube Music yanıt vermiyor."),
    "✖️ Ləğv edildi.": _t("✖️ Cancelled.", "✖️ Отменено.", "✖️ İptal edildi."),
    "❌ Bu ifaçının mahnıları tapılmadı.": _t("❌ No songs found for this artist.", "❌ Песни этого исполнителя не найдены.", "❌ Bu sanatçının şarkıları bulunamadı."),
    "✍️ İstifadə: <code>/artist ifaçının adı</code>": _t("✍️ Usage: <code>/artist artist name</code>", "✍️ Использование: <code>/artist имя исполнителя</code>", "✍️ Kullanım: <code>/artist sanatçı adı</code>"),
    "🎙 İfaçının adını yaz:": _t("🎙 Type the artist's name:", "🎙 Напиши имя исполнителя:", "🎙 Sanatçının adını yaz:"),
    "Köhnəlib — /artist ilə yenidən axtar": _t("Expired — search again with /artist", "Устарело — повтори поиск с /artist", "Eskidi — /artist ile yeniden ara"),
    "🔄 Yenidən başlayır (yeni bağlantı)...": _t("🔄 Restarting (new connection)...", "🔄 Перезапуск (новое подключение)...", "🔄 Yeniden başlıyor (yeni bağlantı)..."),
    "✖️ Ləğv edilir...": _t("✖️ Cancelling...", "✖️ Отменяется...", "✖️ İptal ediliyor..."),
    "⏭ Toplanan göstərilir...": _t("⏭ Showing collected...", "⏭ Показываю собранное...", "⏭ Toplanan gösteriliyor..."),
    "Köhnəlib — yenidən axtar": _t("Expired — search again", "Устарело — ищи заново", "Eskidi — yeniden ara"),
    "⛔ Yalnız axtaran seçə bilər": _t("⛔ Only the person who searched can choose", "⛔ Выбрать может только тот, кто искал", "⛔ Yalnızca arayan kişi seçebilir"),
    "Əvvəl ⏹ Dayandır — yükləmə bu kartda gedir": _t("Tap ⏹ Stop first — a download is running on this card", "Сначала нажми ⏹ Стоп — загрузка идёт в этой карточке", "Önce ⏹ Durdur — indirme bu kartta devam ediyor"),
    "Yükləmə artıq gedir — 📊 Proses": _t("Download already in progress — 📊 Progress", "Загрузка уже идёт — 📊 Прогресс", "İndirme zaten sürüyor — 📊 İşlem"),
    "💎 Hamısını yükləmək Premium üçündür": _t("💎 Bulk downloading is Premium only", "💎 Скачивание всего сразу доступно только в Premium", "💎 Hepsini indirmek yalnızca Premium içindir"),
    "🎙 Kart": _t("🎙 Card", "🎙 Карточка", "🎙 Kart"),
    "ℹ️ Artıq saxlanılıb — /playlist": _t("ℹ️ Already saved — /playlist", "ℹ️ Уже сохранено — /playlist", "ℹ️ Zaten kaydedildi — /playlist"),
    "💾 Saxlanılır...": _t("💾 Saving...", "💾 Сохраняю...", "💾 Kaydediliyor..."),
    "✖️ Bağla": _t("✖️ Close", "✖️ Закрыть", "✖️ Kapat"),
    "🎤 Spotify ifaçısı": _t("🎤 Spotify artist", "🎤 Исполнитель Spotify", "🎤 Spotify sanatçısı"),
    "💿 Spotify albomu": _t("💿 Spotify album", "💿 Альбом Spotify", "💿 Spotify albümü"),
    "🎧 Spotify playlist": _t("🎧 Spotify playlist", "🎧 Плейлист Spotify", "🎧 Spotify çalma listesi"),
    "📻 Spotify Radio / Mix": _t("📻 Spotify Radio / Mix", "📻 Радио / Mix Spotify", "📻 Spotify Radyo / Mix"),
    "🎧 Spotify": _t("🎧 Spotify", "🎧 Spotify", "🎧 Spotify"),
    "Bu video": _t("This video", "Это видео", "Bu video"),
    "Nəyi yükləyək?": _t("What should be downloaded?", "Что скачать?", "Ne indirilsin?"),
    "🎵 Yalnız bu mahnı": _t("🎵 Only this song", "🎵 Только этот трек", "🎵 Yalnızca bu şarkı"),
    "📃 Bütün siyahı": _t("📃 Entire list", "📃 Весь список", "📃 Tüm liste"),
    "🎤 <i>İfaçının albomları toplanır... (bir az çəkə bilər)</i>": _t("🎤 <i>Gathering artist's albums... (may take a moment)</i>", "🎤 <i>Собираю альбомы исполнителя... (может занять время)</i>", "🎤 <i>Sanatçının albümleri toplanıyor... (biraz sürebilir)</i>"),
    "📃 <i>Siyahı açılır...</i>": _t("📃 <i>Opening playlist...</i>", "📃 <i>Открываю список...</i>", "📃 <i>Liste açılıyor...</i>"),
    "Siyahı boşdur və ya gizlidir": _t("The list is empty or private", "Список пуст или скрыт", "Liste boş veya gizli"),
    "Bu sorğu köhnəlib — linki yenidən göndər": _t("This request has expired — please send the link again", "Запрос устарел — отправь ссылку заново", "Bu istek eskidi — bağlantıyı tekrar gönder"),
    "⛔ Yalnız linki göndərən seçə bilər": _t("⛔ Only the person who sent the link can choose", "⛔ Выбрать может только отправитель ссылки", "⛔ Yalnızca bağlantıyı gönderen seçebilir"),
    "💎 Bu imkan Premium tələb edir": _t("💎 This feature requires Premium", "💎 Эта функция требует Premium", "💎 Bu özellik Premium gerektirir"),
    "💎 Playlist saxlamaq yalnız Premium üçündür": _t("💎 Saving playlists is Premium only", "💎 Сохранение плейлистов доступно только в Premium", "💎 Çalma listesi kaydetmek yalnızca Premium içindir"),
    "🔥 {name} — top mahnılar": _t("🔥 {name} — top tracks", "🔥 {name} — популярные треки", "🔥 {name} — popüler şarkılar"),
    "💎 Bütün mahnıları birdən yükləmək yalnız Premium üçündür": _t("💎 Downloading all songs at once is Premium only", "💎 Скачивание всех песен сразу доступно только в Premium", "💎 Tüm şarkıları birden indirmek yalnızca Premium içindir"),
    "❌ Spotify qısa linki açılmadı. Tam linki (open.spotify.com/...) göndər.": _t("❌ Could not resolve Spotify short link. Please send full URL (open.spotify.com/...).", "❌ Не удалось открыть короткую ссылку Spotify. Отправь полную (open.spotify.com/...).", "❌ Spotify kısa bağlantısı açılamadı. Tam bağlantıyı (open.spotify.com/...) gönder."),
    "🎧 <b>Spotify Jam</b> canlı sessiyadır — Spotify onun növbəsini kənar tətbiqlərə açmır.\n\n<i>Jam-dakı mahnının, playlistin və ya albomun linkini göndər — yükləyim.</i>": _t("🎧 <b>Spotify Jam</b> is a live session — Spotify does not expose its queue to third parties.\n\n<i>Send a link to a song, playlist or album from the Jam to download.</i>", "🎧 <b>Spotify Jam</b> — живая сессия, Spotify не открывает её очередь сторонним сервисам.\n\n<i>Пришли ссылку на трек, плейлист или альбом из Jam — скачаю.</i>", "🎧 <b>Spotify Jam</b> canlı bir oturumdur — Spotify sırasını harici uygulamalara açmaz.\n\n<i>Jam'deki şarkının, çalma listesinin veya albümün bağlantısını gönder — indireyim.</i>"),
    "❌ Bu siyahıda yüklənəcək mahnı tapılmadı.": _t("❌ No downloadable songs found in this list.", "❌ В этом списке не найдено треков для скачивания.", "❌ Bu listede indirilecek şarkı bulunamadı."),
    "❌ Yükləmə modulu (music_plugin) yüklənməyib.": _t("❌ Download module (music_plugin) is not loaded.", "❌ Модуль загрузки (music_plugin) не загружен.", "❌ İndirme modülü (music_plugin) yüklü değil."),
    "🔎 <i>Link yoxlanılır...</i>": _t("🔎 <i>Checking link...</i>", "🔎 <i>Проверяю ссылку...</i>", "🔎 <i>Bağlantı kontrol ediliyor...</i>"),
    "💿 Bütün mahnılar ({n})": _t("💿 All tracks ({n})", "💿 Все треки ({n})", "💿 Tüm şarkılar ({n})"),
    "🔥 Yalnız top mahnılar ({n})": _t("🔥 Only top tracks ({n})", "🔥 Только топ-треки ({n})", "🔥 Yalnızca popüler şarkılar ({n})"),
    "💿 {albums} albom · 🎵 {singles} sinql/EP": _t("💿 {albums} albums · 🎵 {singles} singles/EPs", "💿 альбомов: {albums} · 🎵 синглов/EP: {singles}", "💿 {albums} albüm · 🎵 {singles} single/EP"),
    "<i>Təkrarlar (deluxe, remaster) çıxarılıb.</i>": _t("<i>Duplicates (deluxe, remaster) removed.</i>", "<i>Дубликаты (deluxe, remaster) удалены.</i>", "<i>Tekrarlar (deluxe, remaster) çıkarıldı.</i>"),
    "<i>ℹ️ Spotify API qoşulmayıb — yalnız top mahnılar əlçatandır.</i>": _t("<i>ℹ️ Spotify API is not connected — only top tracks available.</i>", "<i>ℹ️ Spotify API не подключен — доступны только топ-треки.</i>", "<i>ℹ️ Spotify API bağlı değil — yalnızca popüler şarkılar mevcut.</i>"),
    "📊 Cəmi: <b>{n}</b> mahnı · ⏳ {dur}": _t("📊 Total: <b>{n}</b> songs · ⏳ {dur}", "📊 Всего: <b>{n}</b> треков · ⏳ {dur}", "📊 Toplam: <b>{n}</b> şarkı · ⏳ {dur}"),
    "<i>⏭ {skipped} uzun video (15 dəq.+) ötürüləcək</i>": _t("<i>⏭ {skipped} long videos (15 min+) will be skipped</i>", "<i>⏭ длинных видео (15+ мин) будет пропущено: {skipped}</i>", "<i>⏭ {skipped} uzun video (15 dk+) atlanacak</i>"),
    "<i>ℹ️ Spotify ictimai rejimdə ilk 100 mahnını təqdim edir.</i>": _t("<i>ℹ️ Spotify provides only the first 100 tracks in public mode.</i>", "<i>ℹ️ Spotify предоставляет только первые 100 треков в публичном режиме.</i>", "<i>ℹ️ Spotify genel modda yalnızca ilk 100 parçayı sunar.</i>"),
    "💡 Məsləhət: Spotify API kənar playlist-lərə 100 limiti qoyur. Bütün mahnıları (100+) yükləmək üçün mahnıları Spotify-da öz yaratdığınız playlist-ə əlavə edin.": _t("💡 Tip: Spotify API limits third-party playlists to 100 tracks. To download all tracks (100+), add them to a playlist you created on Spotify.", "💡 Совет: Spotify API ограничивает сторонние плейлисты 100 треками. Чтобы скачать все треки (100+), добавьте их в созданный вами плейлист Spotify.", "💡 İpucu: Spotify API üçüncü taraf listelere 100 sınırı koyar. Tüm şarkıları (100+) indirmek için şarkıları Spotify'da kendinizin oluşturduğu bir listeye ekleyin."),
    "🎧 <b>Audio redaktoru</b>\n\n🎵 <b>Ad:</b> {title}\n🎤 <b>Artist:</b> {artist}\n🖼 <b>Thumbnail:</b> {cover}\n\n<i>Nəyi dəyişmək istəyirsən?</i>": _t("🎧 <b>Audio Editor</b>\n\n🎵 <b>Title:</b> {title}\n🎤 <b>Artist:</b> {artist}\n🖼 <b>Thumbnail:</b> {cover}\n\n<i>What would you like to change?</i>", "🎧 <b>Аудиоредактор</b>\n\n🎵 <b>Название:</b> {title}\n🎤 <b>Исполнитель:</b> {artist}\n🖼 <b>Обложка:</b> {cover}\n\n<i>Что хочешь изменить?</i>", "🎧 <b>Ses Düzenleyici</b>\n\n🎵 <b>Ad:</b> {title}\n🎤 <b>Sanatçı:</b> {artist}\n🖼 <b>Küçük Resim:</b> {cover}\n\n<i>Neyi değiştirmek istersin?</i>"),
    "✅ yeni seçildi": _t("✅ newly selected", "✅ выбрана новая", "✅ yeni seçildi"),
    "orijinal": _t("original", "оригинальная", "orijinal"),
    "🖼 Thumbnail": _t("🖼 Thumbnail", "🖼 Обложка", "🖼 Küçük Resim"),
    "↩️ Orijinal thumbnail": _t("↩️ Original thumbnail", "↩️ Исходная обложка", "↩️ Orijinal küçük resim"),
    "🎤 Artist": _t("🎤 Artist", "🎤 Исполнитель", "🎤 Sanatçı"),
    "✅ Göndər": _t("✅ Send", "✅ Отправить", "✅ Gönder"),
    "❌ <b>Fayl 20 MB-dan böyükdür.</b>\n<i>Telegram Bot API botlara yalnız 20 MB-a qədər faylı yükləməyə icazə verir.</i>": _t("❌ <b>File is larger than 20 MB.</b>\n<i>Telegram Bot API only allows bots to download files up to 20 MB.</i>", "❌ <b>Файл больше 20 МБ.</b>\n<i>Telegram Bot API разрешает ботам загружать файлы размером только до 20 МБ.</i>", "❌ <b>Dosya 20 MB'tan büyük.</b>\n<i>Telegram Bot API botların yalnızca 20 MB'a kadar dosya indirmesine izin verir.</i>"),
    "Sessiya bitib. Audionu yenidən göndərin.": _t("Session expired. Please send the audio again.", "Сессия истекла. Отправьте аудио заново.", "Oturum sona erdi. Lütfen sesi tekrar gönderin."),
    "Bir az gözləyin...": _t("Please wait a moment...", "Подождите немного...", "Biraz bekleyin..."),
    "✏️ <b>Yeni adı yazın:</b>": _t("✏️ <b>Enter new title:</b>", "✏️ <b>Напишите новое название:</b>", "✏️ <b>Yeni adı yazın:</b>"),
    "🎤 <b>Yeni artist adını yazın:</b>\n<i>Boş buraxmaq üçün</i> <code>-</code> <i>yazın.</i>": _t("🎤 <b>Enter new artist name:</b>\n<i>Type</i> <code>-</code> <i>to leave empty.</i>", "🎤 <b>Напишите имя нового исполнителя:</b>\n<i>Напишите</i> <code>-</code> <i>чтобы оставить пустым.</i>", "🎤 <b>Yeni sanatçı adını yazın:</b>\n<i>Boş bırakmak için</i> <code>-</code> <i>yazın.</i>"),
    "🖼 <b>Yeni thumbnail üçün şəkil göndərin</b>\n<i>(foto və ya fayl kimi)</i>": _t("🖼 <b>Send an image for the new thumbnail</b>\n<i>(as photo or file)</i>", "🖼 <b>Пришлите изображение для новой обложки</b>\n<i>(как фото или файл)</i>", "🖼 <b>Yeni küçük resim için görsel gönderin</b>\n<i>(fotoğraf veya dosya olarak)</i>"),
    "❌ <b>Şəkil 20 MB-dan böyükdür.</b>\n🖼 Başqa şəkil göndərin:": _t("❌ <b>Image is larger than 20 MB.</b>\n🖼 Please send another image:", "❌ <b>Изображение больше 20 МБ.</b>\n🖼 Пришлите другое изображение:", "❌ <b>Görsel 20 MB'tan büyük.</b>\n🖼 Başka bir görsel gönderin:"),
    "❌ <b>Şəkil emal olunmadı.</b>\n🖼 Başqa şəkil göndərin:": _t("❌ <b>Image could not be processed.</b>\n🖼 Please send another image:", "❌ <b>Изображение не удалось обработать.</b>\n🖼 Пришлите другое изображение:", "❌ <b>Görsel işlenemedi.</b>\n🖼 Başka bir görsel gönderin:"),
    "Bəxşiş — DLLMaster 💙": _t("Tip — DLLMaster 💙", "Чаевые — DLLMaster 💙", "Bahşiş — DLLMaster 💙"),
    "Əməyə dəstək üçün {amount}⭐ bəxşiş. Təşəkkürlər!": _t("{amount}⭐ tip to support the project. Thank you!", "Чаевые {amount}⭐ в знак поддержки проекта. Спасибо!", "Emeğe destek için {amount}⭐ bahşiş. Teşekkürler!"),
    "Bəxşiş": _t("Tip", "Чаевые", "Bahşiş"),
    "🙂 Özünə bəxşiş göndərə bilməzsən": _t("🙂 You cannot send a tip to yourself", "🙂 Нельзя отправить чаевые самому себе", "🙂 Kendine bahşiş gönderemezsin"),
    "✏️ <b>Neçə ulduz göndərmək istəyirsən?</b>\n<i>{min}–{max} arası rəqəm yaz, məs.</i> <code>75</code>": _t("✏️ <b>How many stars would you like to send?</b>\n<i>Type a number between {min}–{max}, e.g.</i> <code>75</code>", "✏️ <b>Сколько звёзд хочешь отправить?</b>\n<i>Напиши число от {min} до {max}, напр.</i> <code>75</code>", "✏️ <b>Kaç yıldız göndermek istersin?</b>\n<i>{min}–{max} arası bir sayı yaz, örn.</i> <code>75</code>"),
    "❌ Ödəniş pəncərəsi açılmadı, bir az sonra yenidən cəhd et.": _t("❌ Payment window could not open, please try again shortly.", "❌ Окно оплаты не открылось, попробуйте чуть позже.", "❌ Ödeme penceresi açılamadı, lütfen biraz sonra tekrar deneyin."),
    "❌ {min}–{max} arası rəqəm yaz, məs. <code>75</code>": _t("❌ Type a number between {min}–{max}, e.g. <code>75</code>", "❌ Напиши число от {min} до {max}, напр. <code>75</code>", "❌ {min}–{max} arası bir sayı yaz, örn. <code>75</code>"),
    "👑 Creator-un bütün imkanları onsuz da açıqdır": _t("👑 All features are already unlocked for Creator", "👑 Все возможности и так открыты для Creator", "👑 Creator için tüm özellikler zaten açık"),
    "Bu plan hazırda satışda deyil": _t("This plan is currently not on sale", "Этот план сейчас не продаётся", "Bu plan şu anda satışta değil"),
    "💎 Sizin artıq ömürlük premiumunuz var": _t("💎 You already have lifetime premium", "💎 У вас уже есть пожизненный premium", "💎 Zaten ömür boyu premiumunuz var"),
    " Aktiv premiumunuzun üstünə əlavə olunacaq.": _t(" Will be added on top of your active premium.", " Будет добавлено к вашему активному premium.", " Aktif premiumunuzun üzerine eklenecektir."),
    "DLLMaster Premium — {label}": _t("DLLMaster Premium — {label}", "DLLMaster Premium — {label}", "DLLMaster Premium — {label}"),
    "💎 Premium: {label}. Mahnılar \"via @dllmasterbot\" yazısı olmadan göndərilir.{extend}": _t("💎 Premium: {label}. Songs arrive without \"via @dllmasterbot\" caption.{extend}", "💎 Premium: {label}. Песни приходят без подписи \"via @dllmasterbot\".{extend}", "💎 Premium: {label}. Şarkılar \"via @dllmasterbot\" yazısı olmadan gönderilir.{extend}"),
    "Bəxşiş tanınmadı, yenidən cəhd et.": _t("Tip not recognized, please try again.", "Чаевые не распознаны, попробуйте снова.", "Bahşiş tanınmadı, lütfen tekrar deneyin."),
    "Ödəniş tanınmadı. /premium ilə yenidən cəhd edin.": _t("Payment not recognized. Please try again with /premium.", "Платёж не распознан. Попробуйте снова через /premium.", "Ödeme tanınmadı. Lütfen /premium ile tekrar deneyin."),
    "Bu ödəniş başqa istifadəçi üçündür.": _t("This payment is for another user.", "Этот платёж предназначен для другого пользователя.", "Bu ödeme başka bir kullanıcı içindir."),
    "Bu plan artıq satışda deyil.": _t("This plan is no longer on sale.", "Этот план больше не продаётся.", "Bu plan artık satışta değil."),
    "Qiymət dəyişib. /premium ilə yenidən cəhd edin.": _t("Price has changed. Please try again with /premium.", "Цена изменилась. Попробуйте снова через /premium.", "Fiyat değişti. Lütfen /premium ile tekrar deneyin."),
    "Sizin artıq ömürlük premiumunuz var.": _t("You already have lifetime premium.", "У вас уже есть пожизненный premium.", "Zaten ömür boyu premiumunuz var."),
    "🎉 <b>Təşəkkürlər! Premium aktivdir.</b>\n\n⭐ Ödənildi: <b>{amount}</b> ulduz\n💎 Plan: <b>{label}</b>\n⏳ {until}\n\n✨ <b>Artıq istifadə edə biləcəyin imkanlar:</b>\n{perks}\n\n<i>Statusunu istənilən vaxt /premium ilə görə bilərsən.</i>": _t("🎉 <b>Thank you! Premium is active.</b>\n\n⭐ Paid: <b>{amount}</b> stars\n💎 Plan: <b>{label}</b>\n⏳ {until}\n\n✨ <b>Features unlocked for you:</b>\n{perks}\n\n<i>You can check your status anytime via /premium.</i>", "🎉 <b>Спасибо! Premium активирован.</b>\n\n⭐ Оплачено: <b>{amount}</b> звёзд\n💎 План: <b>{label}</b>\n⏳ {until}\n\n✨ <b>Доступные возможности:</b>\n{perks}\n\n<i>Статус можно проверить в любой момент через /premium.</i>", "🎉 <b>Teşekkürler! Premium aktif.</b>\n\n⭐ Ödenen: <b>{amount}</b> yıldız\n💎 Plan: <b>{label}</b>\n⏳ {until}\n\n✨ <b>Kullanabileceğin özellikler:</b>\n{perks}\n\n<i>Durumunu istediğin zaman /premium ile görebilirsin.</i>"),
    "💙 <b>Çox sağ ol!</b>\n\n⭐ <b>{amount}</b> ulduz bəxşişin alındı.\nDəstəyin botun inkişafına kömək edir 🙏": _t("💙 <b>Thank you very much!</b>\n\n⭐ Received your tip of <b>{amount}</b> stars.\nYour support helps keep the bot running 🙏", "💙 <b>Большое спасибо!</b>\n\n⭐ Получены чаевые: <b>{amount}</b> звёзд.\nВаша поддержка помогает развивать бота 🙏", "💙 <b>Çok teşekkürler!</b>\n\n⭐ <b>{amount}</b> yıldız bahşişin alındı.\nDesteğin botun gelişimine yardımcı oluyor 🙏"),
    "🧾 <b>Ödəniş dəstəyi</b>\n\nPremium ödənişi ilə bağlı problem varsa, bota birbaşa yazın və ya @{creator} ilə əlaqə saxlayın.\nÖdənişin nömrəsini və tarixini qeyd edin.\n\n<i>Premium rəqəmsal xidmətdir; geri qaytarma hallara görə creator tərəfindən edilir.</i>": _t("🧾 <b>Payment Support</b>\n\nIf you encounter any issues with a Premium payment, message the bot directly or contact @{creator}.\nPlease include the payment ID and date.\n\n<i>Premium is a digital service; refunds are evaluated on a case-by-case basis by the creator.</i>", "🧾 <b>Поддержка по платежам</b>\n\nЕсли возникли проблемы с оплатой Premium, напишите боту напрямую или свяжитесь с @{creator}.\nУкажите номер и дату платежа.\n\n<i>Premium — цифровой сервис; возвраты рассматриваются создателем в индивидуальном порядке.</i>", "🧾 <b>Ödeme Desteği</b>\n\nPremium ödemesiyle ilgili bir sorun yaşarsanız, doğrudan bota yazın veya @{creator} ile iletişime geçin.\nÖdeme numarasını ve tarihini belirtin.\n\n<i>Premium dijital bir hizmettir; iadeler duruma göre geliştirici tarafından değerlendirilir.</i>"),
    "🎙 Bu komanda qruplarda, səsli yayımda işləyir.": _t("🎙 This command works in groups during voice chats.", "🎙 Эта команда работает в группах во время голосового чата.", "🎙 Bu komut gruplarda, sesli sohbette çalışır."),
    "🔴 <b>Canlı yayım asistanı hazırda söndürülüb.</b>\n\nSəsli yayımda mahnı oxutmaq üçün bot admini onu aktivləşdirməlidir — aşağıdakı düymə ilə əlaqə saxlayın.\n<i>Botun digər funksiyaları (/music, inline) işləyir.</i>": _t("🔴 <b>Live stream assistant is currently disabled.</b>\n\nTo play music in voice chats, the bot admin needs to activate it.\n<i>Other bot features (/music, inline) remain functional.</i>", "🔴 <b>Ассистент голосовых чатов сейчас отключен.</b>\n\nЧтобы проигрывать музыку в голосовом чате, админ бота должен включить его.\n<i>Остальные функции бота (/music, inline) работают.</i>", "🔴 <b>Canlı yayın asistanı şu anda kapalı.</b>\n\nSesli sohbette müzik çalmak için bot yöneticisinin onu etkinleştirmesi gerekir.\n<i>Botun diğer özellikleri (/music, inline) çalışmaktadır.</i>"),
    "⚠️ Asistan işləmir: {err}": _t("⚠️ Assistant unavailable: {err}", "⚠️ Ассистент не работает: {err}", "⚠️ Asistan çalışmıyor: {err}"),
    "👮 Yalnız qrup adminləri idarə edə bilər.": _t("👮 Only group admins can control this.", "👮 Только администраторы группы могут управлять этим.", "👮 Yalnızca grup yöneticileri kontrol edebilir."),
    "🎙 İstifadə: <code>/play mahnı adı</code> və ya mahnıya reply edib <code>/play</code>": _t("🎙 Usage: <code>/play song name</code> or reply to an audio with <code>/play</code>", "🎙 Использование: <code>/play название песни</code> или ответ на аудио с <code>/play</code>", "🎙 Kullanım: <code>/play şarkı adı</code> veya bir şarkıya yanıt olarak <code>/play</code>"),
    "⏭ Çox uzundur ({dur}), maksimum {max_dur}": _t("⏭ Too long ({dur}), maximum {max_dur}", "⏭ Слишком длинный ({dur}), максимум {max_dur}", "⏭ Çok uzun ({dur}), maksimum {max_dur}"),
    "⏳ <b>{title}</b>\n<i>📦 depodan götürülür...</i>": _t("⏳ <b>{title}</b>\n<i>📦 loading from storage...</i>", "⏳ <b>{title}</b>\n<i>📦 загружаю из хранилища...</i>", "⏳ <b>{title}</b>\n<i>📦 depodan alınıyor...</i>"),
    "⏳ <b>{title}</b>\n<i>⬇️ yüklənir...</i>": _t("⏳ <b>{title}</b>\n<i>⬇️ downloading...</i>", "⏳ <b>{title}</b>\n<i>⬇️ скачивается...</i>", "⏳ <b>{title}</b>\n<i>⬇️ yükleniyor...</i>"),
    "📋 Növbəyə əlavə olundu (#{pos}): <b>{title}</b>": _t("📋 Added to queue (#{pos}): <b>{title}</b>", "📋 Добавлено в очередь (#{pos}): <b>{title}</b>", "📋 Sıraya eklendi (#{pos}): <b>{title}</b>"),
    "Səsli yayım başlamayıb — qrupda video chat başladın (və ya asistanı \"Manage video chats\" admini edin)": _t("Voice chat has not started — start a video chat in the group (or promote the assistant with \"Manage video chats\")", "Голосовой чат не запущен — начните видеочат в группе (или дайте ассистенту права \"Управление видеочатами\")", "Sesli sohbet başlamadı — grupta görüntülü sohbet başlatın (veya asistana \"Görüntülü sohbetleri yönet\" yetkisi verin)"),
    "Səsli yayım başlamayıb — qrupda video chat başladın": _t("Voice chat has not started — start a video chat in the group", "Голосовой чат не запущен — начните видеочат в группе", "Sesli sohbet başlamadı — grupta görüntülü sohbet başlatın"),
    "🔇 Hazırda heç nə oxunmur.": _t("🔇 Nothing is currently playing.", "🔇 Сейчас ничего не играет.", "🔇 Şu anda hiçbir şey çalmıyor."),
    "🔇 Hazırda heç nə oxunmur — /play mahnı adı": _t("🔇 Nothing is currently playing — /play song name", "🔇 Сейчас ничего не играет — /play название песни", "🔇 Şu anda hiçbir şey çalmıyor — /play şarkı adı"),
    "⏸ Fasilə": _t("⏸ Paused", "⏸ Пауза", "⏸ Duraklatıldı"),
    "▶️ Davam": _t("▶️ Resumed", "▶️ Возобновлено", "▶️ Devam ediyor"),
    "⏭ Növbəti": _t("⏭ Next", "⏭ Следующий", "⏭ Sonraki"),
    "🔀 Mix rejimi söndürüldü": _t("🔀 Mix mode turned off", "🔀 Режим Mix выключен", "🔀 Mix modu kapatıldı"),
    "Bu mahnı üçün Mix yoxdur": _t("No Mix available for this song", "Для этого трека Mix недоступен", "Bu şarkı için Mix yok"),
    "🔀 Mix: +{n} mahnı": _t("🔀 Mix: +{n} tracks", "🔀 Mix: +{n} треков", "🔀 Mix: +{n} şarkı"),
    "🔀 İstifadə: <code>/mixplay mahnı adı</code>, <code>/mixplay link</code>, mahnıya reply edib <code>/mixplay</code>, və ya bir mahnı oxunarkən sadəcə <code>/mixplay</code>": _t("🔀 Usage: <code>/mixplay song name</code>, <code>/mixplay link</code>, reply to audio with <code>/mixplay</code>, or simply <code>/mixplay</code> while playing", "🔀 Использование: <code>/mixplay название песни</code>, <code>/mixplay ссылка</code>, ответ на аудио с <code>/mixplay</code> или просто <code>/mixplay</code> во время воспроизведения", "🔀 Kullanım: <code>/mixplay şarkı adı</code>, <code>/mixplay bağlantı</code>, şarkıya yanıt olarak <code>/mixplay</code> veya şarkı çalarken sadece <code>/mixplay</code>"),
    "🔀 <b>Mix:</b> {title}\n<i>📦 depodan götürülür...</i>": _t("🔀 <b>Mix:</b> {title}\n<i>📦 loading from storage...</i>", "🔀 <b>Mix:</b> {title}\n<i>📦 загружаю из хранилища...</i>", "🔀 <b>Mix:</b> {title}\n<i>📦 depodan alınıyor...</i>"),
    "🔀 <b>Mix:</b> {title}\n<i>⬇️ hazırlanır...</i>": _t("🔀 <b>Mix:</b> {title}\n<i>⬇️ preparing...</i>", "🔀 <b>Mix:</b> {title}\n<i>⬇️ подготавливаю...</i>", "🔀 <b>Mix:</b> {title}\n<i>⬇️ hazırlanıyor...</i>"),
    "🔀 <b>Mix başladı:</b> {title}\n📋 +{n} oxşar mahnı növbəyə əlavə olundu{auto}": _t("🔀 <b>Mix started:</b> {title}\n📋 +{n} similar songs added to queue{auto}", "🔀 <b>Mix запущен:</b> {title}\n📋 +{n} похожих треков добавлено в очередь{auto}", "🔀 <b>Mix başladı:</b> {title}\n📋 +{n} benzer şarkı sıraya eklendi{auto}"),
    " · ♾ bitəndə özü davam edəcək": _t(" · ♾ will continue automatically when finished", " · ♾ продолжится автоматически по завершении", " · ♾ bittiğinde kendiliğinden devam edecek"),
    "Yayım artıq bitib": _t("Stream has already ended", "Трансляция уже завершена", "Yayın zaten sona erdi"),
    "👮 Yalnız adminlər": _t("👮 Admins only", "👮 Только админы", "👮 Yalnızca yöneticiler"),
    "✅ <b>Növbə bitdi</b> — /play ilə yeni mahnı əlavə et": _t("✅ <b>Queue ended</b> — add a new song with /play", "✅ <b>Очередь закончилась</b> — добавьте трек через /play", "✅ <b>Sıra bitti</b> — /play ile yeni şarkı ekle"),
    "Fasilə": _t("Pause", "Пауза", "Duraklat"),
    "İndi oxunur": _t("Now playing", "Сейчас играет", "Şimdi çalıyor"),
    "🔀 <b>Mix rejimi</b>": _t("🔀 <b>Mix mode</b>", "🔀 <b>Режим Mix</b>", "🔀 <b>Mix modu</b>"),
    "📋 <b>Növbədə: {n}</b>": _t("📋 <b>In queue: {n}</b>", "📋 <b>В очереди: {n}</b>", "📋 <b>Sırada: {n}</b>"),
    "<i>... və daha {n}</i>": _t("<i>... and {n} more</i>", "<i>... и ещё {n}</i>", "<i>... ve {n} tane daha</i>"),
    "⏹ <b>Yayım bitdi</b>": _t("⏹ <b>Stream ended</b>", "⏹ <b>Трансляция завершена</b>", "⏹ <b>Yayın bitti</b>"),
    "🔐 Asistanın qrupa əlavə olunması üçün admin təsdiqi gözlənilir — təsdiqdən sonra avtomatik davam edəcək.": _t("🔐 Awaiting admin approval to add assistant to group — will continue automatically once approved.", "🔐 Ожидается одобрение администратора для добавления ассистента в группу — продолжится автоматически.", "🔐 Asistanın gruba eklenmesi için yönetici onayı bekleniyor — onaydan sonra otomatik devam edecek."),
    "👮 Yalnız qrup adminləri təsdiq edə bilər": _t("👮 Only group admins can approve", "👮 Только администраторы группы могут подтвердить", "👮 Yalnızca grup yöneticileri onaylayabilir"),
    "❌ İmtina edildi": _t("❌ Rejected", "❌ Отклонено", "❌ Reddedildi"),
    "❌ Asistan qrupa əlavə edilmədi. Lazım olsa /play ilə yenidən soruşulacaq.": _t("❌ Assistant was not added to group. Can be requested again with /play.", "❌ Ассистент не был добавлен в группу. При необходимости запросите снова через /play.", "❌ Asistan gruba eklenmedi. Gerekirse /play ile tekrar sorulacak."),
    "⏳ Qoşulur...": _t("⏳ Joining...", "⏳ Подключается...", "⏳ Katılıyor..."),
    "⏳ <i>Asistan qrupa qoşulur...</i>": _t("⏳ <i>Assistant is joining the group...</i>", "⏳ <i>Ассистент подключается к группе...</i>", "⏳ <i>Asistan gruba katılıyor...</i>"),
    "🔄 Yenidən sına": _t("🔄 Try again", "🔄 Попробовать снова", "🔄 Tekrar dene"),
    "✅ <b>Asistan qrupa əlavə olundu</b> ({name}) — təsdiq: {by}\n\n🎙 İndi səsli yayımda: <code>/play mahnı adı</code> · <code>/mixplay</code>\n<i>💡 Asistanı \"Video chatları idarə etmək\" icazəli admin etsəniz, səsli yayımı özü başlada bilər.</i>": _t("✅ <b>Assistant added to group</b> ({name}) — approved by: {by}\n\n🎙 Available now: <code>/play song name</code> · <code>/mixplay</code>\n<i>💡 Promoting the assistant to admin with \"Manage video chats\" allows it to start voice chats automatically.</i>", "✅ <b>Ассистент добавлен в группу</b> ({name}) — подтвердил: {by}\n\n🎙 Доступно в голосовом чате: <code>/play название песни</code> · <code>/mixplay</code>\n<i>💡 Если дать ассистенту права \"Управление видеочатами\", он сможет запускать голосовые чаты сам.</i>", "✅ <b>Asistan gruba eklendi</b> ({name}) — onaylayan: {by}\n\n🎙 Sesli sohbette şimdi: <code>/play şarkı adı</code> · <code>/mixplay</code>\n<i>💡 Asistanı \"Görüntülü sohbetleri yönet\" yetkili yönetici yaparsanız, sesli sohbeti kendisi başlatabilir.</i>"),
    "🎙 <b>Canlı yayım asistanı</b> ({name}) bu qrupda deyil.\n\nSəsli yayımda mahnı oxutması üçün onu qrupa əlavə edimmi?\n<i>👮 Yalnız qrup adminləri təsdiq edə bilər. Asistan yalnız səs oxudur — mesaj yazmır, heç nəyi idarə etmir.</i>": _t("🎙 <b>Live assistant</b> ({name}) is not in this group.\n\nShould I add it to the group to play music in voice chats?\n<i>👮 Only group admins can approve. The assistant only streams audio — does not send messages or moderate.</i>", "🎙 <b>Ассистент голосовых чатов</b> ({name}) не в этой группе.\n\nДобавить его в группу, чтобы проигрывать музыку?\n<i>👮 Только администраторы группы могут подтвердить. Ассистент только воспроизводит звук — не пишет сообщений и ничем не управляет.</i>", "🎙 <b>Canlı yayın asistanı</b> ({name}) bu grupta değil.\n\nSesli sohbette müzik çalması için onu gruba ekleyeyim mi?\n<i>👮 Yalnızca grup yöneticileri onaylayabilir. Asistan yalnızca ses çalar — mesaj yazmaz, hiçbir şeyi yönetmez.</i>"),
    "⚠️ <i>Asistan əvvəllər bu qrupdan qadağan olunub — təsdiqdən sonra qadağa götürüləcək.</i>": _t("⚠️ <i>The assistant was previously banned from this group — the ban will be lifted upon approval.</i>", "⚠️ <i>Ассистент ранее был заблокирован в этой группе — блокировка снимется после подтверждения.</i>", "⚠️ <i>Asistan daha önce bu gruptan yasaklanmış — onaylandıktan sonra yasak kaldırılacak.</i>"),
    "✅ Əlavə et": _t("✅ Add", "✅ Добавить", "✅ Ekle"),
    "☕ <b>Əməyə Dəstək</b>\n<i>Mahnınız uğurla göndərildi! Botun fasiləsiz işləməsi və server xərclərini dəstəkləmək üçün kiçik bir bəxşişlə dəstək ola bilərsiniz. ⭐</i>\n\n⏱ <i>Bu bildiriş 1 dəqiqə sonra avtomatik silinəcək.</i>": _t("☕ <b>Support the Creator</b>\n<i>Your song was sent successfully! You can support continuous bot uptime and server expenses with a small tip. ⭐</i>\n\n⏱ <i>This message will be deleted automatically in 1 minute.</i>", "☕ <b>Поддержка автора</b>\n<i>Песня успешно отправлена! Вы можете поддержать работу бота и оплату серверов небольшими чаевыми. ⭐</i>\n\n⏱ <i>Это сообщение автоматически удалится через 1 минуту.</i>", "☕ <b>Emeğe Destek</b>\n<i>Şarkınız başarıyla gönderildi! Botun kesintisiz çalışmasını ve sunucu masraflarını desteklemek için küçük bir bahşiş gönderebilirsiniz. ⭐</i>\n\n⏱ <i>Bu bildirim 1 dakika sonra otomatik olarak silinecektir.</i>"),
    "⭐ Bəxşiş Göndər": _t("⭐ Send a tip", "⭐ Отправить чаевые", "⭐ Bahşiş Gönder"),
    "💎 Mix yalnız Premium üçündür": _t("💎 Mix is Premium only", "💎 Mix доступен только в Premium", "💎 Mix yalnızca Premium içindir"),
    "⚠️ <i>Botda texniki sazlama işləri aparılır. Qəfil yükləmələr və ya cavab dayana bilər.</i>": _t("⚠️ <i>Maintenance work is ongoing. Downloads or responses may occasionally pause.</i>", "⚠️ <i>Проводятся технические работы. Загрузки или ответы бота могут временно прерываться.</i>", "⚠️ <i>Botta bakım çalışmaları yapılıyor. İndirmeler veya yanıtlar geçici olarak duraklayabilir.</i>"),
    "⚠️ <i>Botda texniki işlər aparılır — qəfil dayana bilər.</i>\n\n": _t("⚠️ <i>Maintenance work in progress — may stop abruptly.</i>\n\n", "⚠️ <i>Ведутся техработы — процесс может внезапно прерваться.</i>\n\n", "⚠️ <i>Teknik bakım yapılıyor — aniden durabilir.</i>\n\n"),
    "⚠️ <i>Botda texniki sazlama işləri aparılır.</i>": _t("⚠️ <i>Maintenance work is ongoing.</i>", "⚠️ <i>Ведутся технические работы.</i>", "⚠️ <i>Botta bakım çalışmaları yapılıyor.</i>"),
    "Bu mahnının meta məlumatlarını dəyişmək istəyirsən?\n<i>🎵 ad · 🎤 artist · 🖼 üz qabığı şəkli</i>": _t("Do you want to change the metadata of this song?\n<i>🎵 title · 🎤 artist · 🖼 cover art</i>", "Хотите изменить метаданные этого трека?\n<i>🎵 название · 🎤 артист · 🖼 обложка</i>", "Bu şarkının meta verilerini değiştirmek ister misiniz?\n<i>🎵 başlık · 🎤 sanatçı · 🖼 kapak resmi</i>"),
    "✅ Bəli": _t("✅ Yes", "✅ Да", "✅ Evet"),
    "◀️ Geri": _t("◀️ Back", "◀️ Назад", "◀️ Geri"),
    "❌ Mahnı hazır olmadı (yükləmə dayandırılıb və ya alınmayıb).\n<i>Mahnı gələndən sonra onu mənə göndər — redaktə paneli açılacaq.</i>": _t("❌ Song is not ready (download was stopped or failed).\n<i>Send the song to me once received — edit panel will open.</i>", "❌ Трек не готов (загрузка остановлена или не удалась).\n<i>Отправьте мне трек после получения — откроется панель редактирования.</i>", "❌ Şarkı hazır olmadı (indirme durduruldu veya başarısız oldu).\n<i>Şarkı geldikten sonra bana gönderin — düzenleme paneli açılacaktır.</i>"),
    "<i>⚠️ Audio yüklənmədi: {error}</i>": _t("<i>⚠️ Audio could not be downloaded: {error}</i>", "<i>⚠️ Аудио не загрузилось: {error}</i>", "<i>⚠️ Ses indirilemedi: {error}</i>"),
    "⚠️ Asistan bərpa olunur — növbə saxlanıldı, bir azdan /play və ya /skip yaz.": _t("⚠️ Assistant is recovering — queue is preserved, try /play or /skip in a moment.", "⚠️ Ассистент восстанавливается — очередь сохранена, попробуйте /play или /skip чуть позже.", "⚠️ Asistan kurtarılıyor — sıra korundu, birazdan /play veya /skip yazın."),
    "🛡 Admin olmaq (media göndərmə)": _t("🛡 Admin rights (send media)", "🛡 Быть админом (отправка медиа)", "🛡 Yönetici olmak (medya gönderme)"),
    "🗑 Mesaj silmə": _t("🗑 Delete messages", "🗑 Удаление сообщений", "🗑 Mesaj silme"),
    "📌 Mesaj pinləmə": _t("📌 Pin messages", "📌 Закрепление сообщений", "📌 Mesaj sabitleme"),
    "🛡 Yetkiləri ver": _t("🛡 Grant permissions", "🛡 Выдать права", "🛡 Yetkileri ver"),
    "Lazımi yetkilər:": _t("Required permissions:", "Необходимые права:", "Gerekli yetkiler:"),
    "⚠️ <b>Tam işləməyim üçün aşağıdakı düyməyə basıb mənə admin yetkiləri verin.</b>\n": _t("⚠️ <b>To work properly, click the button below and grant me admin rights.</b>\n", "⚠️ <b>Для полноценной работы нажмите кнопку ниже и выдайте мне права админа.</b>\n", "⚠️ <b>Tam çalışmam için aşağıdaki butona basıp bana yönetici yetkisi verin.</b>\n"),
    "<i>Və ya: Qrup → Adminlər → Admin əlavə et → @{bot}</i>": _t("<i>Or: Group → Admins → Add Admin → @{bot}</i>", "<i>Или: Группа → Администраторы → Добавить админа → @{bot}</i>", "<i>Veya: Grup → Yöneticiler → Yönetici Ekle → @{bot}</i>"),
    "✅ <b>Bütün yetkilər verilib, hazıram!</b>": _t("✅ <b>All permissions granted, I'm ready!</b>", "✅ <b>Все права выданы, я готов!</b>", "✅ <b>Tüm yetkiler verildi, hazırım!</b>"),
    "Məni qrupa əlavə etdiyin üçün təşəkkürlər, {adder}! ": _t("Thanks for adding me to the group, {adder}! ", "Спасибо за добавление в группу, {adder}! ", "Beni gruba eklediğin için teşekkürler, {adder}! "),
    "⚠️ <b>{title}</b> qrupunda mesaj yazmaq icazəm yoxdur, ona görə bu mesajı sənə göndərirəm.\n<i>Qrupda işləməyim üçün mənə yazmaq icazəsi və ya admin yetkisi verin.</i>\n\n": _t("⚠️ I don't have permission to send messages in the group <b>{title}</b>, so I'm sending this message to you.\n<i>To let me work in the group, please grant me send permission or admin rights.</i>\n\n", "⚠️ У меня нет разрешения отправлять сообщения в группе <b>{title}</b>, поэтому отправляю это сообщение вам.\n<i>Чтобы я мог работать в группе, разрешите отправку сообщений или дайте права админа.</i>\n\n", "⚠️ <b>{title}</b> grubunda mesaj yazma iznim yok, bu yüzden bu mesajı sana gönderiyorum.\n<i>Grupta çalışabilmem için bana mesaj yazma izni veya yönetici yetkisi verin.</i>\n\n"),
    "❌ Xəta baş verdi, zəhmət olmasa yenidən cəhd edin.": _t("❌ An error occurred, please try again.", "❌ Произошла ошибка, попробуйте еще раз.", "❌ Bir hata oluştu, lütfen tekrar deneyin."),
    "✅ <b>Təşəkkürlər!</b> Bütün yetkilər verildi, hazıram 🚀\n\n🔎 <code>/music mahnı adı</code> yazaraq başlaya bilərsiniz.": _t("✅ <b>Thank you!</b> All permissions granted, I'm ready 🚀\n\n🔎 Type <code>/music song name</code> to get started.", "✅ <b>Спасибо!</b> Все права выданы, я готов 🚀\n\n🔎 Введите <code>/music название трека</code>, чтобы начать.", "✅ <b>Teşekkürler!</b> Tüm yetkiler verildi, hazırım 🚀\n\n🔎 <code>/music şarkı adı</code> yazarak başlayabilirsiniz."),
    "⚠️ <b>Hələ çatışmayan yetkilər var:</b>\n": _t("⚠️ <b>Some permissions are still missing:</b>\n", "⚠️ <b>Некоторых прав всё ещё не хватает:</b>\n", "⚠️ <b>Hâlâ eksik yetkiler var:</b>\n"),
    "<i>Aşağıdakı düyməyə basıb tamamlayın.</i>": _t("<i>Click the button below to complete.</i>", "<i>Нажмите кнопку ниже для завершения.</i>", "<i>Aşağıdaki butona basarak tamamlayın.</i>"),
    "❓ Help": _t("❓ Help", "❓ Справка", "❓ Yardım"),
    "⬅️ Menyu": _t("⬅️ Menu", "⬅️ Меню", "⬅️ Menü"),
    "⬅️ Help": _t("⬅️ Help", "⬅️ Справка", "⬅️ Yardım"),
    "🏠 Menyu": _t("🏠 Menu", "🏠 Главное меню", "🏠 Menü"),
    "⬅️ Bölmə": _t("⬅️ Section", "⬅️ Раздел", "⬅️ Bölüm"),
    "🔄 Yenilə": _t("🔄 Refresh", "🔄 Обновить", "🔄 Yenile"),
    "⏹ dayandırmaq:": _t("⏹ to stop:", "⏹ остановить:", "⏹ durdurmak:"),
    "Bağlandı": _t("Closed", "Закрыто", "Kapatıldı"),
    "⛔ Bu kart yalnız sahibi üçündür": _t("⛔ This card is only for the owner", "⛔ Эта карточка только для владельца", "⛔ Bu kart yalnızca sahibi içindir"),
    "✖️ <i>Panel bağlandı</i>": _t("✖️ <i>Panel closed</i>", "✖️ <i>Панель закрыта</i>", "✖️ <i>Panel kapatıldı</i>"),
    "bölmə seç ↓": _t("select a section ↓", "выберите раздел ↓", "bölüm seçin ↓"),
    "bölmə seç, sonra komandaya bas": _t("select section, then click command", "выберите раздел, затем команду", "bölüm seçin, sonra komuta basın"),
    "mətn rejimi — bu çatda inline düymə göndərmək olmur": _t("text mode — cannot send inline buttons in this chat", "текстовый режим — в этом чате нельзя отправлять инлайн кнопки", "metin modu — bu sohbette satır içi buton gönderilemiyor"),
    "Userbot": _t("Userbot", "Юзербот", "Userbot"),
    "● qoşulub": _t("● connected", "● подключен", "● bağlı"),
    "○ qoşulmayıb": _t("○ disconnected", "○ не подключен", "○ bağlı değil"),
    "Depo": _t("Storage", "Хранилище", "Depo"),
    "● işləyir": _t("● working", "● работает", "● çalışıyor"),
    "○ dayanıb": _t("○ stopped", "○ остановлен", "○ durduruldu"),
    "növbə": _t("queue", "очередь", "kuyruk"),
    "İşlər": _t("Tasks", "Задачи", "İşler"),
    "aktiv": _t("active", "активно", "aktif"),
    "Komanda": _t("Commands", "Команды", "Komut"),
    "bölmə": _t("sections", "разделов", "bölüm"),
    "Kart": _t("Cards", "Карточки", "Kart"),
    "açıq": _t("open", "открыто", "açık"),
    "● şəxs seçilib": _t("● person selected", "● пользователь выбран", "● kişi seçildi"),
    "Nədir": _t("Description", "Описание", "Nedir"),
    "Bölmə": _t("Section", "Раздел", "Bölüm"),
    "🧩 Userbot": _t("🧩 Userbot", "🧩 Юзербот", "🧩 Userbot"),
    "komanda nəticəsi": _t("command result", "результат команды", "komut sonucu"),
    "🛠 Userbot admin paneli": _t("🛠 Userbot admin panel", "🛠 Панель управления юзерботом", "🛠 Userbot yönetim paneli"),
    "Status, sistem, çat, depo, help": _t("Status, system, chat, storage, help", "Статус, система, чат, хранилище, помощь", "Durum, sistem, sohbet, depo, yardım"),
    "📤 İndi yedəklə": _t("📤 Backup now", "📤 Создать бэкап сейчас", "📤 Şimdi yedekle"),
    "⏱ Avto-yedək: {status}": _t("⏱ Auto-backup: {status}", "⏱ Авто-бэкап: {status}", "⏱ Otomatik yedek: {status}"),
    "📁 Saxlama limiti: {count} nüsxə": _t("📁 Keep limit: {count} copies", "📁 Лимит хранения: {count} копий", "📁 Saklama limiti: {count} kopya"),
    "🎯 Bu çata": _t("🎯 To this chat", "🎯 В этот чат", "🎯 Bu sohbete"),
    "💾 Saved-ə": _t("💾 To Saved", "💾 В Избранное", "💾 Kaydedilenlere"),
    "🔁 Yenidən": _t("🔁 Again", "🔁 Снова", "🔁 Yeniden"),
    "📋 Siyahı": _t("📋 List", "📋 Список", "📋 Liste"),
    "✏️ Mesajı dəyiş": _t("✏️ Edit message", "✏️ Изменить сообщение", "✏️ Mesajı düzenle"),
    "1️⃣ Bir dəfəlik: {status}": _t("1️⃣ Once: {status}", "1️⃣ Однократно: {status}", "1️⃣ Bir kerelik: {status}"),
    "🧹 Köhnəni sil: {status}": _t("🧹 Delete previous: {status}", "🧹 Удалять старое: {status}", "🧹 Eskiyi sil: {status}"),
    "🔕 Səssiz: {status}": _t("🔕 Silent: {status}", "🔕 Без звука: {status}", "🔕 Sessiz: {status}"),
    "💬 Yalnız bu çata keçir": _t("💬 Switch to this chat only", "💬 Только в этот чат", "💬 Yalnızca bu sohbete geçir"),
    "🌐 Qlobal et (bütün qruplar)": _t("🌐 Make global (all groups)", "🌐 Сделать глобальным (все группы)", "🌐 Genel yap (tüm gruplar)"),
    "🔙 Geri": _t("🔙 Back", "🔙 Назад", "🔙 Geri"),
    "🔙 İmtina / Geri": _t("🔙 Cancel / Back", "🔙 Отмена / Назад", "🔙 İptal / Geri"),
    "⏸ Dayandır": _t("⏸ Pause", "⏸ Пауза", "⏸ Duraklat"),
    "▶️ Başlat": _t("▶️ Start", "▶️ Запустить", "▶️ Başlat"),
    "▶️ Başla": _t("▶️ Start", "▶️ Начать", "▶️ Başla"),
    "📤 İndi göndər": _t("📤 Send now", "📤 Отправить сейчас", "📤 Şimdi gönder"),
    "✅ Təsdiq et": _t("✅ Confirm", "✅ Подтвердить", "✅ Onayla"),
    "⬇️ Demote": _t("⬇️ Demote", "⬇️ Разжаловать", "⬇️ Yetkiyi al"),
    "⚙️ Yetkiləri dəyiş": _t("⚙️ Edit rights", "⚙️ Изменить права", "⚙️ Yetkileri düzenle"),
    "⬆️ Yenidən admin et": _t("⬆️ Promote to admin", "⬆️ Снова сделать админом", "⬆️ Yeniden yönetici yap"),
    "🤖 Botlar da": _t("🤖 Include bots", "🤖 Включая ботов", "🤖 Botlar dahil"),
    "📚 Paketlərim": _t("📚 My packs", "📚 Мои стикерпаки", "📚 Paketlerim"),
    "🆕 Yeni paket başlat": _t("🆕 New pack", "🆕 Создать пак", "🆕 Yeni paket başlat"),
    "🔗 Paketi aç": _t("🔗 Open pack", "🔗 Открыть пак", "🔗 Paketi aç"),
    "✖️ Ləğv": _t("✖️ Cancel", "✖️ Отмена", "✖️ İptal"),
    "⚡ Sürətləndir ({speed}×)": _t("⚡ Speed up ({speed}×)", "⚡ Ускорить ({speed}×)", "⚡ Hızlandır ({speed}×)"),
    "✂️ {count} stikerə böl": _t("✂️ Split into {count} stickers", "✂️ Разрезать на {count} стикеров", "✂️ {count} çıkartmaya böl"),
    "🎬 İlk 3 san.": _t("🎬 First 3 sec", "🎬 Первые 3 сек", "🎬 İlk 3 sn."),
    "🎯 Tam uyğun": _t("🎯 Exact match", "🎯 Точное совпадение", "🎯 Tam eşleşme"),
    "🔍 İçində": _t("🔍 Contains", "🔍 Содержит", "🔍 İçerir"),
    "👥 Başqasına reply-sız": _t("👥 No reply to others", "👥 Без реплая другим", "👥 Başkasına yanıtsız"),
    "🚫 Tam reply-sız": _t("🚫 Without reply", "🚫 Без реплая вообще", "🚫 Tamamen yanıtsız"),
    "↩️ Mənə reply": _t("↩️ Reply to me", "↩️ Реплай мне", "↩️ Bana yanıt"),
    "💬 Hər vəziyyətdə": _t("💬 In all cases", "💬 В любых случаях", "💬 Her durumda"),
    "💬 Lokal standart": _t("💬 Local default", "💬 Локальный стандарт", "💬 Yerel varsayılan"),
    "🌐 Qlobal standart": _t("🌐 Global default", "🌐 Глобальный стандарт", "🌐 Genel varsayılan"),
    "🔔 Tağ cavabı": _t("🔔 Mention reply", "🔔 Ответ на упоминание", "🔔 Bahsetme yanıtı"),
    "😴 Yuxu": _t("😴 Sleep", "😴 Спящий режим", "😴 Uyku"),
    "⚙️ Standart": _t("⚙️ Default", "⚙️ По умолчанию", "⚙️ Varsayılan"),
    "🎴 Cavab stikeri": _t("🎴 Reply sticker", "🎴 Стикер в ответ", "🎴 Yanıt çıkartması"),
    "🎴 Cavab stikeri (dəyiş)": _t("🎴 Reply sticker (change)", "🎴 Стикер в ответ (изменить)", "🎴 Yanıt çıkartması (değiştir)"),
    "🗑 Stikeri sil": _t("🗑 Remove sticker", "🗑 Удалить стикер", "🗑 Çıkartmayı sil"),
    "🎴 Stiker seç": _t("🎴 Pick sticker", "🎴 Выбрать стикер", "🎴 Çıkartma seç"),
    "🎴 Stiker seç (dəyiş)": _t("🎴 Pick sticker (change)", "🎴 Выбрать стикер (изменить)", "🎴 Çıkartma seç (değiştir)"),
    "🟢 Açıqdır — söndür": _t("🟢 Enabled — disable", "🟢 Включено — выключить", "🟢 Açık — kapat"),
    "🔴 Söndürülüb — aç": _t("🔴 Disabled — enable", "🔴 Выключено — включить", "🔴 Kapalı — aç"),
    "🔙 Siyahı": _t("🔙 List", "🔙 К списку", "🔙 Liste"),
    "⬅️ Siyahı": _t("⬅️ List", "⬅️ К списку", "⬅️ Liste"),
    "💾 Yedəyi göndər": _t("💾 Send backup", "💾 Отправить бэкап", "💾 Yedeği gönder"),
    "🧹 Sıxlaşdır": _t("🧹 Vacuum", "🧹 Сжать базу", "🧹 Sıkıştır"),
    "⬅️ Userbotlar": _t("⬅️ Userbots", "⬅️ Юзерботы", "⬅️ Userbotlar"),
    "🔄 1️⃣ Yenidən qoşul": _t("🔄 1️⃣ Reconnect", "🔄 1️⃣ Переподключить", "🔄 1️⃣ Yeniden bağlan"),
    "🔁 1️⃣ Hesabı dəyiş": _t("🔁 1️⃣ Switch account", "🔁 1️⃣ Сменить аккаунт", "🔁 1️⃣ Hesabı değiştir"),
    "🚪 1️⃣ Hesabdan çıx": _t("🚪 1️⃣ Log out", "🚪 1️⃣ Выйти из аккаунта", "🚪 1️⃣ Hesaptan çık"),
    "🔑 1️⃣ Əsas userbotu qoş": _t("🔑 1️⃣ Connect main userbot", "🔑 1️⃣ Подключить основной юзербот", "🔑 1️⃣ Ana userbotu bağla"),
    "🎙 2️⃣ Canlı yayım asistanı →": _t("🎙 2️⃣ Stream assistant →", "🎙 2️⃣ Ассистент стрима →", "🎙 2️⃣ Canlı yayın asistanı →"),
    "🗄 Userbot bazası →": _t("🗄 Userbot database →", "🗄 База юзербота →", "🗄 Userbot veritabanı →"),
    "✅ Bəli, çıx": _t("✅ Yes, log out", "✅ Да, выйти", "✅ Evet, çık"),
    "↩️ Xeyr": _t("↩️ No", "↩️ Нет", "↩️ Hayır"),
    "🔒 {label}": _t("🔒 {label}", "🔒 {label}", "🔒 {label}"),
    "▫️ {label}": _t("▫️ {label}", "▫️ {label}", "▫️ {label}"),
    "✅ {label}": _t("✅ {label}", "✅ {label}", "✅ {label}"),
    "Söndürülüb": _t("Disabled", "Выключено", "Kapalı"),
    "qalıb": _t("left", "осталось", "kaldı"),
    "⏸ Söndür": _t("⏸ Turn off", "⏸ Отключить", "⏸ Kapat"),
    "▶️ İşə sal": _t("▶️ Turn on", "▶️ Включить", "▶️ Başlat"),
    "🔁 Növbəni qur": _t("🔁 Rebuild queue", "🔁 Перестроить очередь", "🔁 Kuyruğu kur"),
    "⏹ Skanı dayandır": _t("⏹ Stop scan", "⏹ Остановить сканирование", "⏹ Taramayı durdur"),
    "Uptime": _t("Uptime", "Время работы", "Çalışma süresi"),
    "Hesab": _t("Account", "Аккаунт", "Hesap"),
    "Başlayıb": _t("Started", "Запущен", "Başlatıldı"),
    "Qoşulub": _t("Connected", "Подключен", "Bağlandı"),
    "Versiya": _t("Version", "Версия", "Sürüm"),
    "Aktiv": _t("Active", "Активно", "Aktif"),
    "fon işi": _t("background task", "фоновая задача", "arka plan görevi"),
    "Mənbə": _t("Source", "Источник", "Kaynak"),
    "skan gedir": _t("scanning in progress", "идёт сканирование", "tarama yapılıyor"),
    "fayl": _t("file", "файл", "dosya"),
    "komanda": _t("command", "команда", "komut"),
    "✅ Bəli, işlət": _t("✅ Yes, run", "✅ Да, выполнить", "✅ Evet, çalıştır"),
    "❌ Ləğv": _t("❌ Cancel", "❌ Отмена", "❌ İptal"),
    "ölçülür...": _t("measuring...", "измеряется...", "ölçülüyor..."),
    "gözlənilir": _t("waiting", "ожидается", "bekleniyor"),
    "😀 Emoji: {emoji}": _t("😀 Emoji: {emoji}", "😀 Эмодзи: {emoji}", "😀 Emoji: {emoji}"),
    "📦 Avtomatik: {status}": _t("📦 Auto: {status}", "📦 Автоматически: {status}", "📦 Otomatik: {status}"),
    "✅ Bu çatı daxil et": _t("✅ Include this chat", "✅ Включить этот чат", "✅ Bu sohbeti dahil et"),
    "🚫 Bu çatı istisna et": _t("🚫 Exclude this chat", "🚫 Исключить этот чат", "🚫 Bu sohbeti hariç tut"),
    "🌐 Bütün qruplarda aktiv et (Qlobal)": _t("🌐 Enable in all groups (Global)", "🌐 Включить во всех группах (Глобально)", "🌐 Tüm gruplarda etkinleştir (Genel)"),
    "⏱ Avto-yedək intervalı": _t("⏱ Auto-backup interval", "⏱ Интервал автобэкапа", "⏱ Otomatik yedekleme aralığı"),
    "📁 Saxlama limiti": _t("📁 Retention limit", "📁 Лимит хранения", "📁 Saklama limiti"),
    "Cari dəyər": _t("Current value", "Текущее значение", "Geçerli değer"),
    "Tənzimləmə": _t("Adjustment", "Настройка", "Ayar"),
    "Düymələrdən istifadə edin və ya çata rəqəm yazın": _t("Use the buttons or type a number in chat", "Используйте кнопки или отправьте число в чат", "Düğmeleri kullanın veya sohbete bir sayı yazın"),
    "Dəyəri dəyişmək üçün aşağıdakı düymələrdən istifadə edin:": _t("Use the buttons below to adjust the value:", "Используйте кнопки ниже для изменения значения:", "Değeri değiştirmek için aşağıdaki düğmeleri kullanın:"),
    "maksimum / aktiv": _t("maximum / active", "максимум / активно", "maksimum / etkin"),
    "söndür / 0": _t("disable / 0", "отключить / 0", "kapat / 0"),
    "nüsxə": _t("copies", "копий", "kopya"),
    "✏️ Öz rəqəmini yaz": _t("✏️ Type custom value", "✏️ Написать своё число", "✏️ Kendi sayını yaz"),
    "✍️ Çata istədiyiniz intervalı, '+' və ya '-' yazın": _t("✍️ Type your desired interval, '+' or '-' in chat", "✍️ Напишите в чат желаемый интервал, '+' или '-'", "✍️ Sohbete istediğiniz aralığı, '+' veya '-' yazın"),
    "✍️ Çata istədiyiniz nüsxə sayını, '+' və ya '-' yazın": _t("✍️ Type your desired number of copies, '+' or '-' in chat", "✍️ Напишите в чат желаемое количество копий, '+' или '-'", "✍️ Sohbete istediğiniz kopya sayısını, '+' veya '-' yazın"),
}


# əlavə tərcümələr (common/i18n_extra.py) — CATALOG-da olmayanlar qoşulur
from common.i18n_extra import EXTRA as _EXTRA  # noqa: E402
for _k, _v in _EXTRA.items():
    CATALOG.setdefault(_k, _v)


# ═════════════════════════ dil faylları: yükləmə / yoxlama / ixrac ═════════════════════════
def _ph(text: str) -> set:
    return set(re.findall(r"\{(\w+)\}", text or ""))


def source_keys() -> list:
    """Tərcümə olunmalı bütün Azərbaycanca mətnlər (kodda _() ilə bükülənlər — CATALOG açarları)."""
    return list(CATALOG)


def _validate(data, filename: str = "") -> tuple:
    """(kod, ad, telegram_kodları, strings, templates, skipped) — səhvdə ValueError."""
    if not isinstance(data, dict):
        raise ValueError("JSON obyekt olmalıdır")
    meta = data.get("_meta") or {}
    code = str(meta.get("code") or Path(filename).stem or "").lower().strip()
    if not CODE_RE.match(code):
        raise ValueError(f"dil kodu yanlışdır: '{code}' (məs. de, fr, pt-br)")
    if code == "az":
        raise ValueError("az — əsas dildir, faylla dəyişdirilmir")
    name = str(meta.get("name") or code).strip()[:40]
    tg = [str(x).lower() for x in (meta.get("telegram_codes") or [code]) if isinstance(x, str)]
    skipped = []
    strings = {}
    for k, v in (data.get("strings") or {}).items():
        if not isinstance(k, str) or not isinstance(v, str) or not v.strip():
            continue                                         # boş — tərcümə edilməyib (az göstərilir)
        if _ph(k) != _ph(v):
            skipped.append(f"yer tutucu uyğun deyil: {k[:50]!r} → {sorted(_ph(v))} (lazım: {sorted(_ph(k))})")
            continue
        strings[k] = v
    templates = {}
    for slot, v in (data.get("templates") or {}).items():
        if not isinstance(v, str) or not v.strip():
            continue
        bad = _ph(v) - TEMPLATE_VARS
        if bad:
            skipped.append(f"şablon '{slot}': naməlum yer tutucu {sorted(bad)}")
            continue
        templates[slot] = v
    return code, name, tg, strings, templates, skipped


def _seed_files():
    """lang/ qovluğu yoxdursa — daxili tərcümələrdən yarat (redaktə üçün nümunə fayllar)."""
    if LANG_DIR.exists():
        return
    try:
        LANG_DIR.mkdir(parents=True)
        tg = {"en": ["en"], "ru": ["ru", "uk", "be", "kk", "ky", "uz", "tg", "hy", "ka"], "tr": ["tr"]}
        for code, name in BUILTIN_NAMES.items():
            data = {"_meta": {"code": code, "name": name, "telegram_codes": tg.get(code, [code]), "version": 1},
                    "templates": TEMPLATES.get(code, {}),
                    "strings": {k: v[code] for k, v in CATALOG.items() if code in v}}
            (LANG_DIR / f"{code}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def reload() -> dict:
    """Daxili tərcümələr + lang/*.json. Qaytarır: REPORT."""
    _seed_files()
    langs = {"az": "🇦🇿 Azərbaycanca"}
    strings, tpl, detect_map, report = {}, {}, dict(_BASE_DETECT), {}
    for code, name in BUILTIN_NAMES.items():                 # daxili ehtiyat
        langs[code] = name
        strings[code] = {k: v[code] for k, v in CATALOG.items() if code in v}
        tpl[code] = dict(TEMPLATES.get(code, {}))
        report[code] = {"file": None, "strings": len(strings[code]), "templates": len(tpl[code]),
                        "skipped": [], "error": ""}
    if LANG_DIR.is_dir():
        for f in sorted(LANG_DIR.glob("*.json")):
            try:
                code, name, tg, st, tp, skipped = _validate(json.loads(f.read_text(encoding="utf-8")), f.name)
            except Exception as e:
                report[f.stem] = {"file": f.name, "strings": 0, "templates": 0, "skipped": [], "error": str(e)[:200]}
                continue
            langs[code] = name
            strings.setdefault(code, {}).update(st)          # fayl daxili tərcümədən üstündür
            tpl.setdefault(code, {}).update(tp)
            for t in tg:
                detect_map[t] = code
            report[code] = {"file": f.name, "strings": len(strings[code]), "templates": len(tpl[code]),
                            "skipped": skipped, "error": ""}
    LANGS.clear()
    LANGS.update(langs)
    STRINGS.clear()
    STRINGS.update(strings)
    TPL.clear()
    TPL.update(tpl)
    _DETECT.clear()
    _DETECT.update(detect_map)
    REPORT.clear()
    REPORT.update(report)
    _cache.clear()
    return REPORT


def coverage(code: str) -> int:
    """Tərcümə faizi (mətnlər + şablonlar)."""
    if code == "az":
        return 100
    keys = source_keys()
    done = sum(1 for k in keys if k in STRINGS.get(code, {}))
    slots = 7
    done_t = min(slots, len(TPL.get(code, {})))
    return int(100 * (done + done_t) / max(1, len(keys) + slots))


def export_template(az_templates: dict = None) -> dict:
    """Tərcüməçi üçün boş fayl: bütün açarlar, dəyərlər boş (Azərbaycanca orijinal açarın özüdür)."""
    return {"_meta": {"code": "xx", "name": "🏳️ Language name", "telegram_codes": ["xx"], "version": 1,
                      "help": "code: ISO dil kodu (de, fr, pt-br) · telegram_codes: bu dili avtomatik seçəcək "
                              "Telegram dilləri · boş qalan sətir Azərbaycanca göstərilir · {yer_tutucular} "
                              "dəyişməməlidir"},
            "templates": {k: "" for k in (az_templates or {})} or {k: "" for k in TEMPLATES.get("en", {})},
            "templates_original_az": az_templates or {},
            "strings": {k: "" for k in source_keys()}}


def export_lang(code: str) -> dict:
    return {"_meta": {"code": code, "name": LANGS.get(code, code),
                      "telegram_codes": sorted(t for t, c in _DETECT.items() if c == code), "version": 1},
            "templates": dict(TPL.get(code, {})),
            "strings": {k: STRINGS.get(code, {}).get(k, "") for k in source_keys()}}


def install(raw: bytes, filename: str = "") -> tuple:
    """Göndərilən JSON faylı lang/<kod>.json kimi yaz, yenidən yüklə. → (kod, REPORT[kod])"""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception as e:
        raise ValueError(f"JSON oxunmadı: {e}")
    code, *_rest = _validate(data, filename)
    LANG_DIR.mkdir(parents=True, exist_ok=True)
    data.setdefault("_meta", {})["code"] = code
    data.pop("templates_original_az", None)
    (LANG_DIR / f"{code}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    reload()
    return code, REPORT.get(code, {})


def remove(code: str) -> bool:
    code = str(code or "").lower().strip()
    if not CODE_RE.match(code) or code == "az":      # callback-dən gəlir: "../x" kimi yollar silinməsin
        return False
    f = LANG_DIR / f"{code}.json"
    if not f.exists():
        return False
    f.unlink()
    reload()
    return True


def picker_title() -> str:
    """Dil seçimi başlığı — hər dildə (istifadəçi hələ dil seçməyib)."""
    seen, parts = set(), []
    for code in LANGS:
        t = _("🌐 Dil seçin:", code).replace("🌐", "").rstrip(":").strip()
        if t and t not in seen:
            seen.add(t)
            parts.append(t)
    return "🌐 " + " · ".join(parts)


reload()
