"""
📦 Asılılıqlar paneli — bot/plugins/deps_plugin.py (yalnız creator)

/menu → 🖥 Sistem → 📦 Asılılıqlar
  • koddakı BÜTÜN Python paketləri: ✅ quraşdırılıb (versiya) · ❌ yoxdur · ⬆️ yeni versiya var
    💎 vacib (onsuz bot / plugin işləməz) · ➕ istəyə bağlı (bir funksiya üçün)
  • paketə bas → izah, hansı fayllarda işlənir, ⬇️ quraşdır / ⬆️ yenilə
  • 🔍 Yeniləmələri yoxla · ⬇️ Çatışmayanları quraşdır · ⬆️ Hamısını yenilə · 📝 requirements.txt
  • 🔁 Avtomatik: söndür / yalnız yt-dlp + ytmusicapi (default) / hamısı — gündə bir dəfə
Başlanğıcda: çatışmayan VACİB paketlər avtomatik quraşdırılır, creator-a xəbər + ♻️ restart düyməsi.
pip ayrıca prosesdə, asinxron işləyir — bot donmur.
"""
import asyncio
import contextlib
import logging
import time
from html import escape

from aiogram import F, types
from aiogram.types import InlineKeyboardMarkup

from common import deps

from common.database import Settings
from bot.core import panel as ui
from bot.core.panel import btn, show as panel_show
from common.kv import next_of

logger = logging.getLogger(__name__)

PRIORITY = 76
PAGE = 8
AUTO_MODES = {"off": "❌ söndürülüb", "ytdlp": "🎬 yalnız yt-dlp + ytmusicapi", "all": "📦 hamısı"}
DAY = 24 * 3600




_S = Settings()
_s, _put = _S.get, _S.put


def setup(context):
    dp, bot = context.dp, context.bot
    state = {"rows": [], "busy": False, "restart": False}



    def auto_mode() -> str:
        m = _s("deps:auto", "ytdlp")
        return m if m in AUTO_MODES else "ytdlp"

    def refresh_rows():
        state["rows"] = deps.packages()
        return state["rows"]

    def icon(r: dict) -> str:
        if r["version"] is None:
            return "❌" if r["required"] else "➖"
        if deps._norm(r["name"]) in deps.outdated_cached():
            return "⬆️"
        return "✅"

    def label() -> str:
        rows = state["rows"] or refresh_rows()
        miss = sum(1 for r in rows if r["required"] and r["version"] is None)
        upd = len(deps.outdated_cached())
        return "📦 Asılılıqlar · " + (f"❌ {miss}" if miss else f"⬆️ {upd}" if upd else "✅")

    context.deps_label = label

    restart_kb = lambda: [btn("♻️ Restart et", "botrestart:ask")] if state["restart"] else []

    # ── görünüşlər ──
    def panel(page: int = 0, note: str = ""):
        rows = refresh_rows()
        od = deps.outdated_cached()
        miss_req = [r for r in rows if r["required"] and r["version"] is None]
        miss_opt = [r for r in rows if not r["required"] and r["version"] is None]
        ok = [r for r in rows if r["version"] is not None]
        checked = deps._OUTDATED["ts"]
        lines = ["<i>🏠 › 🖥 Sistem</i>", "📦 <b>Python asılılıqları</b>", "━━━━━━━━━━━━━━━━━━",
                 f"Kodda <b>{len(rows)}</b> xarici paket tapıldı:",
                 f"✅ quraşdırılıb: <b>{len(ok)}</b>"
                 + (f" · ⬆️ yenilənə bilər: <b>{len(od)}</b>" if od else ""),
                 f"❌ vacib, yoxdur: <b>{len(miss_req)}</b> · ➖ istəyə bağlı, yoxdur: {len(miss_opt)}",
                 "🔍 Son yoxlama: " + (f"{int((time.time() - checked) / 60)} dəq. əvvəl" if checked
                                       else "<i>edilməyib — 🔍 bas</i>")
                 + (f" ⚠️ {escape(deps._OUTDATED['error'])}" if deps._OUTDATED["error"] else ""),
                 f"🔁 Avtomatik yeniləmə: <b>{AUTO_MODES[auto_mode()]}</b> (gündə bir)",
                 "━━━━━━━━━━━━━━━━━━"]
        pages = max(1, -(-len(rows) // PAGE))
        page = min(max(page, 0), pages - 1)
        for r in rows[page * PAGE:(page + 1) * PAGE]:
            v = r["version"] or "yoxdur"
            up = od.get(deps._norm(r["name"]))
            lines.append(f"{icon(r)} <b>{escape(r['name'])}</b> <code>{escape(v)}</code>"
                         + (f" → <code>{escape(up[1])}</code>" + (" ⚠️" if deps.major_jump(up[0], up[1]) else "")
                            if up else "")
                         + ("" if r["required"] else " <i>(istəyə bağlı)</i>")
                         + (f"\n   <i>{escape(r['purpose'])}</i>" if r["purpose"] else ""))
        lines.append("━━━━━━━━━━━━━━━━━━\n<i>❌ vacib · ➖ istəyə bağlı · ⬆️ yeni versiya · ⚠️ böyük versiya dəyişikliyi</i>")
        if note:
            lines.append(f"\n{note}")
        kb = []
        rowb = []
        for i, r in enumerate(rows[page * PAGE:(page + 1) * PAGE], start=page * PAGE):
            rowb.append(btn(f"{icon(r)} {r['name']}"[:30], f"dp:p:{i}:{page}"))
            if len(rowb) == 2:
                kb.append(rowb)
                rowb = []
        if rowb:
            kb.append(rowb)
        if pages > 1:
            kb.append(ui.pager("dp:pg:", page, pages, "dp:noop"))
        act = [btn("🔍 Yeniləmələri yoxla", "dp:check")]
        if miss_req or miss_opt:
            act.append(btn(f"⬇️ Çatışmayanlar ({len(miss_req) + len(miss_opt)})", "dp:inst"))
        kb.append(act)
        row2 = []
        if od:
            safe = sum(1 for v in od.values() if not deps.major_jump(*v))
            row2.append(btn(f"⬆️ Hamısını yenilə ({safe})", "dp:upall"))
        row2.append(btn("📝 requirements.txt", "dp:req"))
        kb.append(row2)
        kb.append([btn(f"🔁 Avto: {AUTO_MODES[auto_mode()].split(' ', 1)[0]}", "dp:auto")] + restart_kb())
        kb.append(ui.nav("menu:dev", "Bot idarəsi", close_data="dp:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)

    def detail(i: int, page: int, note: str = ""):
        rows = state["rows"] or refresh_rows()
        if not 0 <= i < len(rows):
            return panel(page, "Siyahı dəyişib")
        r = rows[i]
        up = deps.outdated_cached().get(deps._norm(r["name"]))
        files = r["files"]
        lines = [f"📦 <b>{escape(r['name'])}</b>",
                 f"{'💎 vacib' if r['required'] else '➕ istəyə bağlı'}"
                 + (f" · {escape(r['purpose'])}" if r["purpose"] else ""),
                 f"Versiya: <code>{escape(r['version'] or 'quraşdırılmayıb')}</code>"
                 + (f" → ən son <code>{escape(up[1])}</code>" if up else ""),
                 f"Import: <code>{escape(', '.join(r['imports']))}</code>",
                 f"\n📁 <b>{len(files)}</b> faylda işlənir:"]
        lines += [f"• <code>{escape(f)}</code>" for f in files[:10]]
        if len(files) > 10:
            lines.append(f"<i>… və daha {len(files) - 10}</i>")
        if up and deps.major_jump(up[0], up[1]):
            lines.append("\n⚠️ <i>Böyük versiya dəyişikliyi — yeniləmə köhnə kodu poza bilər. "
                         "Əvvəl yedək / GitHub push et.</i>")
        if note:
            lines.append(f"\n{note}")
        kb = []
        if r["version"] is None:
            kb.append([btn("⬇️ Quraşdır", f"dp:pi:{i}:{page}")])
        elif up:
            kb.append([btn(f"⬆️ {up[1]} versiyasına yenilə", f"dp:pi:{i}:{page}")])
        kb.append(restart_kb())
        kb.append(ui.nav(f"dp:pg:{page}", "Asılılıqlar", close_data="dp:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=[k for k in kb if k])

    show = panel_show

    async def run_pip(cb, names: list, upgrade: bool, title: str) -> str:
        state["busy"] = True
        await show(cb, f"⏳ <b>{escape(title)}</b>\n<code>{escape(' '.join(names))[:600]}</code>\n\n"
                       "<i>pip işləyir — bot normal işləməyə davam edir</i>", None)
        try:
            ok, tail = await deps.pip_install(names, upgrade)
        finally:
            state["busy"] = False
        if ok:
            state["restart"] = True
            refresh_rows()
            logger.info(f"📦 pip {'-U ' if upgrade else ''}{' '.join(names)} — uğurlu")
            return (f"✅ <b>Hazırdır:</b> {escape(', '.join(names))}\n"
                    "♻️ Yeni versiyaların işə düşməsi üçün <b>restart et</b>.")
        logger.warning(f"📦 pip xətası ({' '.join(names)}): {tail}")
        return f"❌ <b>pip xətası</b>\n<code>{escape(tail[-700:])}</code>"

    @dp.callback_query(F.data.startswith("dp:"))
    async def dp_cb(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        act = parts[1] if len(parts) > 1 else "panel"
        if act == "noop":
            await cb.answer()
            return
        if act == "close":
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return
        if state["busy"] and act in ("inst", "upall", "pi", "check"):
            await cb.answer("⏳ pip hələ işləyir", show_alert=True)
            return
        if act == "pg":
            await cb.answer()
            await show(cb, *panel(int(parts[2]) if len(parts) > 2 else 0))
            return
        if act == "p" and len(parts) > 3:
            await cb.answer()
            await show(cb, *detail(int(parts[2]), int(parts[3])))
            return
        if act == "pi" and len(parts) > 3:
            i, page = int(parts[2]), int(parts[3])
            rows = state["rows"] or refresh_rows()
            if not 0 <= i < len(rows):
                await cb.answer("Siyahı dəyişib", show_alert=True)
                return
            r = rows[i]
            await cb.answer("⏳")
            note = await run_pip(cb, [r["name"]], upgrade=r["version"] is not None,
                                 title=("Yenilənir" if r["version"] else "Quraşdırılır"))
            new_rows = state["rows"]
            j = next((k for k, x in enumerate(new_rows) if x["name"] == r["name"]), i)
            await show(cb, *detail(j, page, note))
            return
        note = ""
        if act == "check":
            await cb.answer("🔍 pip yoxlayır...")
            await show(cb, "🔍 <b>Yeniləmələr yoxlanılır...</b>\n<i>pip list --outdated (10-60 san.)</i>", None)
            od = await deps.outdated(force=True)
            note = (f"⬆️ <b>{len(od)}</b> paketin yeni versiyası var" if od else "✅ Hamısı ən son versiyadadır") \
                if not deps._OUTDATED["error"] else f"⚠️ {escape(deps._OUTDATED['error'])}"
        elif act == "inst":
            names = [r["name"] for r in (state["rows"] or refresh_rows()) if r["version"] is None]
            await cb.answer("⏳")
            note = await run_pip(cb, names, upgrade=False, title="Çatışmayanlar quraşdırılır")
        elif act == "upall":
            od = deps.outdated_cached()
            outd = [r["name"] for r in (state["rows"] or refresh_rows()) if deps._norm(r["name"]) in od]
            # ⚠️ böyük versiya dəyişikliyi (məs. 3.x → 4.x) kodu poza bilər — toplu yeniləmədə atlanır
            big = [n for n in outd if deps.major_jump(*od[deps._norm(n)])]
            names = [n for n in outd if n not in big]
            await cb.answer("⏳")
            note = await run_pip(cb, names, upgrade=True, title="Yenilənir") if names else ""
            if big:
                note += ("\n\n⚠️ <b>Atlandı</b> (böyük versiya dəyişikliyi, kodu poza bilər): "
                         + escape(", ".join(f"{n} {od[deps._norm(n)][0]} → {od[deps._norm(n)][1]}" for n in big))
                         + "\n<i>Lazımdırsa paketin öz səhifəsindən ayrıca yenilə — əvvəl yedək / GitHub push et.</i>")
        elif act == "req":
            r = await asyncio.to_thread(deps.write_requirements)
            await cb.answer("📝")
            note = ("📝 <b>requirements.txt</b> yeniləndi\n"
                    + (f"➕ vacib əlavə olundu: {escape(', '.join(r['added']))}\n" if r["added"] else "")
                    + (f"💬 istəyə bağlı (şərh kimi): {escape(', '.join(r['optional']))}\n" if r["optional"] else "")
                    + (f"ℹ️ faylda var, kodda işlənmir: {escape(', '.join(r['unused']))}" if r["unused"] else "")
                    ) if (r["added"] or r["optional"] or r["unused"]) else "✅ requirements.txt artıq koda uyğundur"
        elif act == "auto":
            modes = list(AUTO_MODES)
            nxt = next_of(modes, auto_mode())
            _put("deps:auto", nxt)
            await cb.answer("🔁 " + AUTO_MODES[nxt])
        else:
            await cb.answer()
        await show(cb, *panel(0, note))

    # ── başlanğıc + gündəlik avtomatik ──
    async def notify(text: str, kb_restart: bool = False):
        if not context.creator_id:
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[[btn("♻️ Restart et", "botrestart:ask"),
                                                    btn("📦 Asılılıqlar", "dp:panel")]]) if kb_restart else None
        with contextlib.suppress(Exception):
            await bot.send_message(context.creator_id, text, parse_mode="HTML", reply_markup=kb)

    async def auto_loop():
        await asyncio.sleep(40)
        # 1) çatışmayan VACİB paketlər — avtomatik quraşdır
        try:
            miss = await asyncio.to_thread(deps.missing_required)
            if miss:
                logger.warning(f"📦 Çatışmayan vacib paketlər: {', '.join(miss)} — quraşdırılır")
                ok, tail = await deps.pip_install(miss)
                state["restart"] = state["restart"] or ok
                await notify(("✅ <b>Çatışmayan paketlər quraşdırıldı:</b> " if ok else "❌ <b>Quraşdırılmadı:</b> ")
                             + escape(", ".join(miss)) + ("" if ok else f"\n<code>{escape(tail[-400:])}</code>"),
                             kb_restart=ok)
        except Exception as e:
            logger.warning(f"📦 Asılılıq yoxlaması: {e}")
        # 2) gündəlik yeniləmə
        while True:
            try:
                mode = auto_mode()
                last = float(_s("deps:auto_last", "0") or 0)
                if mode != "off" and time.time() - last > DAY:
                    _put("deps:auto_last", str(time.time()))
                    od = await deps.outdated(force=True)
                    rows = await asyncio.to_thread(deps.packages)
                    names = [r["name"] for r in rows if deps._norm(r["name"]) in od
                             and (mode == "all" or r["name"] in deps.AUTO_YTDLP)
                             and not (mode == "all" and deps.major_jump(*od[deps._norm(r["name"])]))]
                    if names:
                        ok, tail = await deps.pip_install(names, upgrade=True)
                        state["restart"] = state["restart"] or ok
                        await notify(("⬆️ <b>Avtomatik yeniləndi:</b> " if ok else "❌ <b>Avto-yeniləmə alınmadı:</b> ")
                                     + escape(", ".join(f"{n} → {od[deps._norm(n)][1]}" for n in names))
                                     + ("\n♻️ Restartdan sonra işə düşəcək" if ok else f"\n<code>{escape(tail[-400:])}</code>"),
                                     kb_restart=ok)
            except Exception as e:
                logger.info(f"📦 Avto-yeniləmə: {e}")
            await asyncio.sleep(3600)

    old = getattr(context, "deps_task", None)
    if old and not old.done():
        old.cancel()
    try:
        context.deps_task = asyncio.get_running_loop().create_task(auto_loop())
    except RuntimeError:
        async def _start():
            context.deps_task = asyncio.create_task(auto_loop())
        dp.startup.register(_start)

    logger.info("✅ Asılılıqlar paneli yükləndi")
