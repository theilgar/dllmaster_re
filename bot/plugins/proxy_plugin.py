"""
🌐 Proxy / 🧅 Tor paneli — bot/plugins/proxy_plugin.py (yalnız creator)

/menu → 🖥 Sistem → 🌐 Proxy / Tor
  Rejim: ❌ Birbaşa · 🌐 Proxy · 🧅 Tor
  Əhatə: ⬇️ istifadəçi yükləmələri · 📦 depo doldurucu · 🔎 axtarış / YT Music (hər biri ✅/❌)
  🌐 Proxy: ➕ əlavə et (bir neçəsi birdən, hər sətirdə bir) · 🗑 sil · 🔁 rotasiya · 🧪 sına
  🧅 Tor: vəziyyət · 🔄 Yeni IP · 403-də avtomatik yeni IP · 🔧 quraşdırma köməyi
Hamısı bot/core/proxy.py-da. Telegram trafikinə toxunulmur.
"""
import asyncio
import contextlib
import logging
from html import escape

from aiogram import F, types
from aiogram.types import InlineKeyboardMarkup

from common import proxy as px
from common.database import get_db
from bot.core.input_wait import InputWait, chain_menu_text
from bot.core import panel as ui
from bot.core.panel import btn
from common.kv import next_of

logger = logging.getLogger(__name__)

PRIORITY = 77
WAIT_TTL = 600
TOR_HELP = (
    "🧅 <b>Tor quraşdırmaq</b>\n\n"
    "<b>Arch:</b>\n<code>sudo pacman -S tor</code>\n"
    "<b>Debian / Ubuntu:</b>\n<code>sudo apt install tor</code>\n\n"
    "🔄 <b>Yeni IP</b> düyməsi üçün <code>/etc/tor/torrc</code> faylına əlavə et:\n"
    "<code>ControlPort 9051\nCookieAuthentication 1\nCookieAuthFileGroupReadable 1</code>\n\n"
    "Sonra:\n<code>sudo systemctl enable --now tor</code>\n"
    "<code>sudo usermod -aG tor $USER</code> <i>(Debian-da qrup: debian-tor)</i>\n"
    "<i>Qrupa əlavədən sonra botu yenidən başlat ki, cookie faylını oxuya bilsin.</i>\n\n"
    "⚠️ <i>YouTube Tor çıxış IP-lərinin çoxunu tanıyır — tez-tez \"bot olmadığını təsdiqlə\" verir. "
    "Tor ən yaxşı halda ehtiyat yoldur; yaxşı residential / mobil proxy daha etibarlıdır.</i>"
)




def setup(context):
    dp, bot = context.dp, context.bot
    state = InputWait(WAIT_TTL, probe={}, busy=False)

    is_creator = context.is_creator


    waiting = state.waiting

    chain_menu_text(context, lambda uid: waiting() and is_creator(uid))

    def label() -> str:
        m = px.mode()
        if m == "tor":
            return "🧅 Tor · " + ("🟢" if px.tor_running() else "🔴")
        if m == "proxy":
            return f"🌐 Proxy · {len(px.proxies())}"
        return "🌐 Proxy / Tor"

    context.proxy_label = label

    # ── görünüş ──
    def probe_line(key) -> str:
        r = state["probe"].get(key)
        if not r:
            return ""
        if not r["ok"]:
            return f" — ❌ <i>{escape(r['error'][:70])}</i>"
        return (f" — ✅ <code>{escape(r['ip'] or '?')}</code> · {r['ms']} ms"
                + (f" · YouTube {r['yt']} ms" if r["yt"] is not None else " · YouTube ❌"))

    def panel(note: str = ""):
        m = px.mode()
        lines = ["<i>🏠 › 🖥 Sistem</i>", "🌐 <b>Proxy / Tor</b>", "━━━━━━━━━━━━━━━━━━",
                 f"<b>Rejim:</b> {px.MODES[m]}",
                 "<i>YouTube trafiki (yükləmə / axtarış) üçün çıxış IP-si. Telegram birbaşa qalır.</i>",
                 "",
                 "<b>Əhatə:</b> " + " · ".join(f"{'✅' if px.scope_on(k) else '❌'} {v}" for k, v in px.SCOPES.items()),
                 f"🖥 Birbaşa IP{probe_line('direct')}"]
        if m == "proxy" or px.proxies():
            lst = px.proxies()
            st = px.stats()
            lines += ["", f"🌐 <b>Proxy-lər ({len(lst)})</b> · {px.ROTATE[px.rotate_mode()]}"]
            if not lst:
                lines.append("<i>Siyahı boşdur — ➕ ilə əlavə et</i>")
            for i, p in enumerate(lst[:12], 1):
                s = st.get(p) or {}
                bad = px.is_bad(p)
                lines.append(f"{i}. <code>{escape(px.mask(p))}</code>"
                             + (" ⏸" if bad else "")
                             + (f" · ✅{s.get('ok', 0)} ❌{s.get('fail', 0)}" if s else "")
                             + probe_line(p))
            if len(lst) > 12:
                lines.append(f"<i>… və daha {len(lst) - 12}</i>")
        if m == "tor":
            host, port = px.tor_ctrl()
            run = px.tor_running()
            ctrl = px.port_open(host, port)
            lines += ["", f"🧅 <b>Tor:</b> {'🟢 işləyir' if run else '🔴 işləmir'} · <code>{escape(px.tor_proxy())}</code>",
                      f"🎛 ControlPort {port}: {'✅ açıq' if ctrl else '❌ bağlı — 🔄 Yeni IP işləməz'}",
                      f"🔄 403-də avtomatik yeni IP: {'✅' if px.tor_auto() else '❌'}",
                      f"🧅 Tor çıxışı{probe_line('tor')}"]
            if not run:
                lines.append("<i>Tor işləmir — 🔧 Tor quraşdırma köməyi</i>")
        if note:
            lines.append(f"\n{note}")
        mkeys = list(px.MODES)                                      # tək düymə: hər basışda növbəti rejim
        nmode = next_of(mkeys, m)
        rows = [[btn(f"Rejim: {px.MODES.get(m, m)}  ›  {px.MODES[nmode]}", f"px:mode:{nmode}")],
                [ui.check(v.split(' ', 1)[0], px.scope_on(k), f"px:sc:{k}")
                 for k, v in px.SCOPES.items()]]
        if m == "proxy":
            rows.append([btn("➕ Proxy əlavə et", "px:add"), btn(px.ROTATE[px.rotate_mode()].split(" ", 1)[0] + " Rotasiya",
                                                                 "px:rot")])
            lst = px.proxies()
            if lst:
                rows.append([btn(f"🗑 {i}", f"px:del:{i - 1}") for i in range(1, min(len(lst), 6) + 1)])
                rows.append([btn("🧹 Hamısını sil", "px:clr")])
        if m == "tor":
            rows.append([btn("🔄 Yeni IP", "px:newnym"), ui.toggle("🔁 Avto IP", px.tor_auto(), "px:auto", compact=True)])
            rows.append([btn("🔧 Tor quraşdırma köməyi", "px:torhelp")])
        rows.append([btn("🧪 Sına (IP + YouTube)", "px:probe")])
        rows.append(ui.nav("menu:sys", "Sistem", close_data="px:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    show = state.show                 # 🧩 InputWait: yenilə + panel kimi yadda saxla

    async def to_panel(message, text, kb):
        await state.to_panel(bot, message, text, kb)

    async def run_probe() -> str:
        targets = [("direct", None)]
        m = px.mode()
        if m == "tor":
            targets.append(("tor", px.tor_proxy()))
        elif m == "proxy":
            targets += [(p, p) for p in px.proxies()[:8]]
        res = await asyncio.gather(*(asyncio.to_thread(px.probe, p) for _, p in targets))
        for (key, _), r in zip(targets, res):
            state["probe"][key] = r
        bad = [k for (k, _), r in zip(targets, res) if not r["ok"] and k != "direct"]
        if m == "proxy":
            for p in bad:
                px.report(p, False)
        return (f"🧪 Sınandı: {len(targets)}" + (f" · ❌ {len(bad)} işləmir (müvəqqəti kənara qoyuldu)" if bad else " · ✅"))

    @dp.callback_query(F.data.startswith("px:"))
    async def px_cb(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        act = parts[1] if len(parts) > 1 else "panel"
        note = ""
        if act == "close":
            state.clear()
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return
        if act == "mode" and len(parts) > 2 and parts[2] in px.MODES:
            px.set_mode(parts[2])
            await cb.answer(px.MODES[parts[2]])
            if parts[2] == "tor" and not px.tor_running():
                note = "⚠️ Tor işləmir — 🔧 quraşdırma köməyinə bax. İşləyənə qədər yükləmələr alınmayacaq."
            if parts[2] == "proxy" and not px.proxies():
                note = "➕ Proxy əlavə et — siyahı boş olduqca birbaşa işləyir."
        elif act == "sc" and len(parts) > 2 and parts[2] in px.SCOPES:
            on = px.toggle_scope(parts[2])
            await cb.answer(f"{px.SCOPES[parts[2]]}: {'✅' if on else '❌'}")
        elif act == "rot":
            get_db().set_setting("px:rotate", "round" if px.rotate_mode() == "sticky" else "sticky")
            await cb.answer(px.ROTATE[px.rotate_mode()])
        elif act == "add":
            state.start("add")
            await cb.answer()
            await show(cb, ("<i>🏠 › 🖥 Sistem › 🌐 Proxy</i>\n➕ <b>Proxy əlavə et</b>\n\n"
                            "Bir və ya bir neçəsini göndər (hər sətirdə bir):\n"
                            "<code>http://host:port</code>\n<code>http://user:pass@host:port</code>\n"
                            "<code>socks5://user:pass@host:port</code>\n<code>host:port:user:pass</code>\n\n"
                            "<i>Mesajın parol olduğu üçün dərhal silinəcək.</i>"),
                       InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "px:panel")]]))
            return
        elif act == "del" and len(parts) > 2 and parts[2].isdigit():
            px.remove_proxy(int(parts[2]))
            await cb.answer("🗑 Silindi")
        elif act == "clr":
            px.clear_proxies()
            await cb.answer("🧹 Siyahı təmizləndi")
        elif act == "newnym":
            await cb.answer("🔄 Yeni IP istənilir...")
            err = await asyncio.to_thread(px.tor_newnym)
            if err:
                note = f"❌ {escape(err)}"
            else:
                state["probe"]["tor"] = await asyncio.to_thread(px.probe, px.tor_proxy())
                note = "✅ Tor yeni çıxış IP-si aldı"
        elif act == "auto":
            get_db().set_setting("tor:auto", "0" if px.tor_auto() else "1")
            await cb.answer("🔄 Avto: " + ("✅" if px.tor_auto() else "❌"))
        elif act == "torhelp":
            await cb.answer()
            await show(cb, TOR_HELP, InlineKeyboardMarkup(inline_keyboard=[ui.nav("px:panel", "Proxy", close_data="px:close")]))
            return
        elif act == "probe":
            if state["busy"]:
                await cb.answer("⏳ Sınaq gedir", show_alert=True)
                return
            await cb.answer("🧪 Sınanır (≤15 san.)...")
            state["busy"] = True
            await show(cb, "🧪 <b>Sınanır...</b>\n<i>Çıxış IP-si və YouTube-a gecikmə ölçülür</i>", None)
            try:
                note = await run_probe()
            finally:
                state["busy"] = False
        else:
            state.clear()
            await cb.answer()
        await show(cb, *panel(note))

    @dp.message(F.chat.type == "private", state.for_creator(is_creator))
    async def on_input(message: types.Message):
        good, bad = px.parse_proxies(message.text or "")
        with contextlib.suppress(Exception):
            await message.delete()                     # parol çatda qalmasın
        if not good:
            await message.answer("❌ Proxy tanınmadı. Nümunə: <code>socks5://user:pass@1.2.3.4:1080</code>",
                                 parse_mode="HTML")
            return
        n = px.add_proxies(good)
        state.clear()
        if px.mode() == "off":
            px.set_mode("proxy")
        note = f"✅ {n} proxy əlavə olundu" + (f" · ❌ {len(bad)} tanınmadı" if bad else "") + \
               "\n<i>🧪 Sına ilə yoxla</i>"
        logger.info(f"🌐 {n} proxy əlavə olundu")
        await to_panel(message, *panel(note))

    logger.info(f"✅ Proxy / Tor paneli yükləndi — rejim: {px.mode()}")
