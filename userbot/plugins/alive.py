"""🫀 alive.py — Userbot və bot uptime, ping və server statusu.

Komanda:
  • .alive — botun, userbot-un və serverin işləmə müddətini (uptime),
             başlama vaxtını və ping-i fastfetch kartında göstərir. 🔄 ilə yenilənir.
"""
from datetime import datetime
import time

from common.utilities import format_uptime, read_sys_uptime
from userbot.core.userbot_api import (
    ff,
    plain_name,
    static_info,
    telethon_version,
)


def _long_span(sec) -> str:
    """200000 → '2 gün 07:33:20', 42 → '00:00:42'"""
    return format_uptime(sec, full=True)


def _since(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


def register(ub):
    async def build_alive() -> str:
        st = await static_info()
        now = time.time()
        bot_up = now - st["bot_start"]
        body = [
            (ub.tr("Status"), "● alive"),
            "# Bot",
            (ub.tr("Uptime"), _long_span(bot_up)),
            (ub.tr("Başlayıb"), _since(st["bot_start"])),
        ]

        attach = ub.state.get("attach_time")
        client = ub.client
        if client is not None and client.is_connected():
            t0 = time.monotonic()
            try:
                me = await client.get_me()
                ping = (time.monotonic() - t0) * 1000
                body += [
                    "# Userbot",
                    (ub.tr("Hesab"), plain_name(me)),
                    ("Ping", f"{ping:.0f} ms"),
                ]
            except Exception:
                body += ["# Userbot", (ub.tr("Hesab"), "?")]
            if attach:
                body.append((ub.tr("Qoşulub"), _long_span(now - attach)))
        else:
            body += ["# Userbot", (ub.tr("Hesab"), ub.tr("○ qoşulmayıb"))]

        sys_up = read_sys_uptime()
        body += [
            "# Server",
            (ub.tr("Uptime"), _long_span(sys_up)),
            ("Host", st["host"]),
            (ub.tr("Versiya"), f"Py {st['py']} · Telethon {telethon_version}"),
        ]
        return ff(ub.title("alive"), body, logo=True, footer=f"{datetime.now():%H:%M:%S}")

    @ub.command(
        "alive",
        help=(
            "bot nə qədərdir işləyir",
            "Botun, userbot-un və serverin işləmə müddətini (uptime), başlama vaxtını və "
            "ping-i fastfetch kartında göstərir. 🔄 ilə yenilənir.\n\n"
            "<b>İstifadə:</b> <code>.alive</code>",
        ),
    )
    async def on_alive(event):
        out = await ub.out(event, await build_alive())

        async def refresh(o):
            await o.update(await build_alive())

        out.refresh = refresh

