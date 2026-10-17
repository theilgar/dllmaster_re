"""
♻️ .restart — userbot-u (botla birgə) yenidən başladır. Host komandasıdır: .menu-dan söndürülə bilməz.

Userbot botun prosesinin içində işləyir (eyni Telethon client), ona görə restart bütün prosesi əvəz edir —
botun /restart komandası ilə eyni yol: bot/plugins/restart_plugin.py `restart(context)` (yayım sessiyaları saxlanır,
işçi proseslər bağlanır, os.execv — PID dəyişmir).

  .restart        — aktiv yükləmə / yayım yoxdursa dərhal; varsa xəbərdarlıq və nə qədər olduğunu göstərir
  .restart now    — gözləmədən, aktiv işlər olsa da (yayım 20–40 san. kəsilir, sonra avtomatik bərpa olunur)

Restartdan sonra komandanı yazdığınız mesaj (və ya .menu-dan basdığınız menyu mesajı) "✅ yenidən başladı · müddət"
olur. Mesajın ünvanı data/userbot_restart.json-da saxlanır.
"""
import asyncio
import json
import logging
import os
import time
from html import escape

from userbot.core.userbot_api import safe_edit

logger = logging.getLogger(__name__)

STATE = os.path.join("data", "userbot_restart.json")
FORCE = ("now", "force", "-f", "dərhal", "derhal", "yes", "bəli", "beli", "1")
PATTERN = r"^\.(?:restart|reboot)(?:\s+(\S+))?$"
MIN_UPTIME = 15            # saniyə — yenicə başlayan proses təkrar restart olunmasın


def busy_text(active: dict) -> str:
    lines = ["⚠️ <b>Aktiv işlər var</b> — restart onları kəsər:"]
    if active.get("downloads"):
        lines.append(f"• 🎵 Yükləmələr: <b>{active.get('download_count', 0)}</b>")
    for s in (active.get("streams") or [])[:3]:
        lines.append(f"• 🎙 Yayım: <i>{escape(str(s)[:60])}</i>")
    lines.append("\nYenə də restart üçün: <code>.restart now</code> <i>(yayım 20–40 san. kəsilir, sonra avtomatik bərpa olunur)</i>")
    return "\n".join(lines)


def save_state(event) -> None:
    """Restartdan sonra redaktə ediləcək mesajın ünvanı: adi mesaj (chat, id) və ya menyu mesajı (inline id)."""
    st = {"at": time.time()}
    if getattr(event, "panel", None) is not None:
        st["iid"] = event.iid
    else:
        st.update(chat=getattr(event, "chat_id", None), msg=getattr(event, "id", None))
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f)


async def announce(ub) -> bool:
    """Restartdan sonra: əmr mesajını "yenidən başladı" ilə əvəz edir. Qaytarır: redaktə cəhdi olubmu."""
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        os.remove(STATE)
    except (OSError, ValueError):
        return False
    await asyncio.sleep(2)
    took = time.time() - st.get("at", time.time())
    took_str = f"{took:.1f} san." if took < 60 else f"{int(took // 60)} dəq. {int(took % 60)} san."
    text = f"✅ <b>Userbot yenidən başladı</b>\n⏱ Müddət: {took_str}"
    try:
        if st.get("iid"):
            from userbot.core.plugin_guide import _nav
            from bot.core import panel as ui
            await ub.edit_panel(st["iid"], text, ui.kb(_nav("0", "main", "Menyu")))
        elif st.get("chat") and st.get("msg") and ub.client:
            await ub.client.edit_message(st["chat"], st["msg"], text, parse_mode="html")
    except Exception as e:
        logger.info(f"restart mesajı yenilənmədi: {e}")
    return True


async def do_restart(context):
    """Restartı komanda handler-indən AYRI tapşırıqda icra edir.

    Səbəb: .restart Telethon-un hadisə handler-indən çağırılır. restart() içindəki `client.disconnect()` həmin client-in
    yeniləmə dövrəsini ləğv edir — handler-in öz tapşırığı da ləğv olunur (CancelledError) və os.execv heç çağırılmır:
    proses işləyir, amma userbot-lar bağlıdır (cavab vermir). Ayrı tapşırığı bu ləğv etmir. Hər halda restart yarımçıq
    qalarsa (xəta / ləğv) — son çarə olaraq prosesi birbaşa əvəz edirik."""
    from bot.plugins import restart_plugin as rp
    try:
        await rp.restart(context)
    except (Exception, asyncio.CancelledError) as e:
        logger.error(f"restart yarımçıq qaldı ({type(e).__name__}: {e}) — proses birbaşa əvəzlənir", exc_info=True)
        from common.utilities import restart_argv
        logging.shutdown()
        argv = restart_argv()
        os.execv(argv[0], argv)


def setup(ub):
    from bot.plugins import restart_plugin as rp
    from common.utilities import spawn

    async def on_restart(event):
        arg = (event.pattern_match.group(1) or "").strip().lower()
        if arg and arg not in FORCE:
            await safe_edit(event, "ℹ️ <b>İstifadə:</b> <code>.restart</code> və ya <code>.restart now</code>")
            return
        up = time.time() - rp._STARTED
        if up < MIN_UPTIME:
            await safe_edit(event, f"⚠️ <b>Yenicə başlayıb ({int(up)} san. əvvəl).</b> Bir neçə saniyə gözləyin.")
            return
        active = rp.get_active_operations(ub.context)
        if active["is_busy"] and arg not in FORCE:
            await safe_edit(event, busy_text(active))
            return
        await safe_edit(event, "♻️ <b>Userbot yenidən başladılır…</b>\n<i>Bot da birlikdə restart olunur (eyni proses).</i>")
        save_state(event)
        logger.warning("♻️ Restart — userbot .restart komandası")
        spawn(do_restart(ub.context))               # handler-dən ayrı: client.disconnect() onu ləğv etməsin

    ub.commands.append({"name": "restart", "pattern": PATTERN, "outgoing": True, "fn": on_restart, "owner": None})
    ub.add_help("restart", ".restart", "userbotu yenidən başlat",
                "Userbot botun prosesinin içində işləyir — restart bütün prosesi yenidən başladır (botun /restart-ı ilə eyni, "
                "PID dəyişmir). Yayım sessiyaları saxlanır və avtomatik bərpa olunur.\n\n"
                "<b>İstifadə:</b>\n• <code>.restart</code> — aktiv yükləmə / yayım yoxdursa dərhal; varsa xəbərdarlıq\n"
                "• <code>.restart now</code> — gözləmədən (<code>.reboot</code> də eynidir)\n"
                "<i>Restartdan sonra bu mesaj «yenidən başladı» olur.</i>", "acc")
