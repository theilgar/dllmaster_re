"""
bot.menus.views.spotify
Spotify API tənzimləmələri, OAuth pəncərəsi və callback listener köməkçiləri.
"""

import asyncio
import contextlib
import logging
import os
from html import escape

from aiogram.types import InlineKeyboardMarkup

from bot.menus.navigation import nav_row
from bot.menus.state import get_context
from bot.menus.views.base import LINE, SECTIONS, crumb
from common.database import get_db
from common.menu import _btn
from common.utilities import spawn

logger = logging.getLogger(__name__)

_oauth_runner = None


def _sp_setting(key: str) -> str:
    try:
        db = get_db()
        return (db.get_setting(key) if db else "") or ""
    except Exception:
        return ""


def _spotify_button_label() -> str:
    ctx = get_context()
    sp = getattr(ctx, "spotify", None)
    if sp and getattr(sp, "is_user_auth", lambda: False)():
        return "🟢 Spotify OAuth · ✅"
    if sp and getattr(sp, "sp", None):
        return "🟢 Spotify API · ⚠️"
    cid = getattr(sp, "client_id", None) or os.getenv("SPOTIFY_CLIENT_ID") or _sp_setting("spotify:client_id")
    if cid:
        return "🟢 Spotify API · ⚠️"
    return "🟢 Spotify API · ❌"


def _mask_secret(sec: str) -> str:
    if not sec:
        return "<i>təyin edilməyib</i>"
    if len(sec) <= 8:
        return "••••••••"
    return sec[:4] + "•" * (len(sec) - 8) + sec[-4:]


def spotify_panel(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    ctx = get_context()
    sp = getattr(ctx, "spotify", None)
    cid = getattr(sp, "client_id", None) or os.getenv("SPOTIFY_CLIENT_ID") or _sp_setting("spotify:client_id") or ""
    sec = (
        getattr(sp, "client_secret", None)
        or os.getenv("SPOTIFY_CLIENT_SECRET")
        or _sp_setting("spotify:client_secret")
        or ""
    )
    is_user = bool(sp and getattr(sp, "is_user_auth", lambda: False)())
    is_app = bool(sp and getattr(sp, "sp", None) and not is_user)

    user = (getattr(sp, "user_info", {}) or {}) if is_user else {}
    user_name = user.get("display_name") or user.get("id") or "İstifadəçi"
    user_prod = (user.get("product") or "Premium").capitalize()

    if is_user:
        status_str = (
            f"✅ <b>İstifadəçi Hesabı Qoşulub (OAuth)</b>\n"
            f"👤 <code>{escape(user_name)}</code> ({user_prod})\n"
            "🎉 <i>Limitsiz: Bütün mahnılar və şəxsi playlistlər aktivdir!</i>"
        )
    elif is_app:
        status_str = (
            "⚠️ <b>Tətbiq Rejimi (Client Credentials)</b>\n"
            "<i>(Mahnı axtarışı və albomlar aktivdir; playlistlər max 100 mahnı)</i>"
        )
    elif cid:
        status_str = "⚠️ <b>Qoşulmayıb / Xəta</b> (Ehtiyat Embed rejimi)"
    else:
        status_str = "❌ <b>Təyin edilməyib</b> (Açarlar daxil edilməyib)"

    cid_display = f"<code>{escape(cid)}</code>" if cid else "<i>təyin edilməyib</i>"
    sec_display = f"<code>{_mask_secret(sec)}</code>" if sec else "<i>təyin edilməyib</i>"

    lines = [
        crumb(SECTIONS["mus"], "🟢 Spotify API"),
        LINE,
        f"🔑 <b>Client ID:</b> {cid_display}",
        f"🔒 <b>Client Secret:</b> {sec_display}",
        f"📶 <b>Vəziyyət:</b> {status_str}",
        "",
        "ℹ️ <b>Məlumat:</b>",
        "• <b>Tətbiq rejimi:</b> Client ID/Secret ilə axtarış və örtük şəkilləri işləyir.",
        "• <b>İstifadəçi rejimi (OAuth):</b> Şəxsi playlistlərinizdə 100 limiti aradan qalxır və <b>bütün mahnılar</b> yüklənir.",
    ]
    if note:
        lines.append(f"\n{note}")

    rows = [
        [
            _btn("✏️ Client ID", "sp:set_id"),
            _btn("✏️ Client Secret", "sp:set_sec"),
        ],
    ]
    if is_user:
        rows.append([_btn("🚪 Hesabdan çıx", "sp:logout")])
    else:
        rows.append([_btn("🔗 Spotify ilə giriş", "sp:oauth")])

    rows += [
        [_btn("🧪 Bağlantı testi", "sp:test")],
        nav_row("mus", refresh="sp:panel"),
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def stop_oauth_listener():
    global _oauth_runner
    if _oauth_runner:
        try:
            await _oauth_runner.cleanup()
        except Exception as _e:
            logger.debug("OAuth runner cleanup xətası: %r", _e)
        _oauth_runner = None


async def start_oauth_listener(context, chat_id: int, panel_id: int, sp_input: dict):
    global _oauth_runner
    await stop_oauth_listener()
    from aiohttp import web

    async def callback_handler(request):
        code = request.query.get("code")
        error = request.query.get("error")
        if error:
            html = (
                "<html><body style='font-family: sans-serif; text-align: center; padding-top: 50px;'>"
                f"<h1 style='color: red;'>❌ Xəta: {escape(error)}</h1>"
                "<p>Giriş ləğv edildi. Bu pəncərəni bağlaya bilərsiniz.</p></body></html>"
            )
            return web.Response(text=html, content_type="text/html")
        if not code:
            return web.Response(text="Kod tapılmadı.", content_type="text/html", status=400)

        sp = getattr(context, "spotify", None)
        ok, msg = False, "Spotify obyekti tapılmadı"
        if sp:
            ok, msg = await asyncio.to_thread(sp.complete_oauth, code)
        spawn(stop_oauth_listener())
        sp_input["stage"] = None

        note = (
            f"✅ <b>Spotify hesabı uğurla qoşuldu!</b>\n{escape(str(msg))}"
            if ok
            else f"❌ <b>Xəta:</b> {escape(str(msg))}"
        )
        text, kb = spotify_panel(note)
        panel_dict = dict(chat_id=chat_id, message_id=panel_id, parse_mode="HTML")
        with contextlib.suppress(Exception):
            await context.bot.edit_message_text(text=text, reply_markup=kb, **panel_dict)

        if ok:
            html = (
                "<html><body style='font-family: sans-serif; text-align: center; padding-top: 50px;'>"
                "<h1 style='color: green;'>✅ Spotify Girişi Uğurludur!</h1>"
                "<p>Hesabınız bot ilə əlaqələndirildi. Bu səhifəni bağlaya bilərsiniz.</p></body></html>"
            )
        else:
            html = (
                "<html><body style='font-family: sans-serif; text-align: center; padding-top: 50px;'>"
                f"<h1 style='color: red;'>❌ Xəta baş verdi: {escape(str(msg))}</h1></body></html>"
            )
        return web.Response(text=html, content_type="text/html")

    app = web.Application()
    app.router.add_get("/callback", callback_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 8888)
        await site.start()
        _oauth_runner = runner
        logger.info("🟢 Spotify OAuth local listener 127.0.0.1:8888-də başladıldı.")
    except Exception as e:
        logger.warning("Spotify OAuth local listener başladıla bilmədi (8888 məşğul ola bilər): %s", e)
        with contextlib.suppress(Exception):
            await runner.cleanup()
        _oauth_runner = None


__all__ = [
    "_mask_secret",
    "_sp_setting",
    "_spotify_button_label",
    "spotify_panel",
    "start_oauth_listener",
    "stop_oauth_listener",
]
