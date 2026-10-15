"""
🧩 Plugin meneceri və kod yeniləmələri — yalnız creator.

  📥 Bota fayl göndər (şəxsi çatda):
     • bir .py faylı           — plugin və ya nüvə faylı
     • bir neçə .py            — albom və ya ayrı-ayrı mesajlar, neçə dənə olursa olsun: bir neçə saniyə
                                 ərzində gələnlər BİR paketə yığılır; paket hələ təsdiqlənməyibsə sonradan
                                 göndərilən fayllar da ona əlavə olunur (eyni fayl yenidən gəlsə — sonuncu qalır)
     • .zip arxivi             — toplu yeniləmə; yollarla: bot/core/x.py, userbot/plugins/y.py, common/z.py ...
                                 (köhnə yollar da olar: core/x.py, plugins/x.py, userbot_plugins/x.py)
     Hər faylın yeri AVTOMATİK tapılır (common/code_update.py): mövcud fayl — öz yerinə; yeni fayl —
     başlıqdakı yol, setup/register, kim onu import edir, öz importları. Yanlışdırsa 📍 düyməsi ilə dəyişilir.
     Sintaksis yoxlanılır, 🆕 / ✏️ / ＝ göstərilir → təsdiq.
       🔌 bot plugin-ləri və 📱 userbot plugin-ləri — restartsız yenilənir
       ⚙️ common/, bot/core/, userbot/core/ və 🚀 giriş faylları — yazılır, sonra ♻️ restart
     Köhnə fayllar data/update_backups/<tarix>/-a saxlanılır → ⏪ bir toxunuşla geri qaytarılır.
     Yüklənməyən plugin avtomatik köhnə versiyasına qayıdır.

  /plugins — bütün plugin-lər: 🔄 yenidən yüklə · ⏸ söndür · ▶️ aktivləşdir · 🗑 sil · 📤 faylı göndər
             · 📦 yeniləmə tarixçəsi · 📤 Export
  📤 Export — kodu fayl kimi al: hamısı (.zip), zona üzrə (common, bot/core, bot/plugins, userbot/core,
              userbot/plugins, giriş faylları) və ya tək fayl. Zip-də yollar qorunur — həmin arxivi bota
              geri göndərsən 📥 yeniləmə kimi quraşdırılır. Sessiya, baza, config.env heç vaxt daxil edilmir.

Plugin-in köhnə handler-ləri yenidən yükləmədə avtomatik çıxarılır. Plugin-də istəyə görə
`teardown(context)` funksiyası olsa, söndürüləndə çağırılır (fon işlərini dayandırmaq üçün).
Söndürülən plugin bot/plugins/disabled/-a köçür — bot yenidən başlayanda da yüklənmir.
"""
import ast
import asyncio
import importlib
import importlib.util
import io
import inspect
import logging
import os
import shutil
import sys
import time
from html import escape
from pathlib import Path

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, FSInputFile, InlineKeyboardMarkup

from common import code_update as cu
from common.utilities import BASE_DIR, safe_plugin_name
from bot.core import panel as ui
from bot.core.panel import btn

logger = logging.getLogger(__name__)


PLUGINS_DIR = Path(BASE_DIR) / "bot" / "plugins"
PACKAGE = "bot.plugins"
DISABLED_DIR = PLUGINS_DIR / "disabled"
BACKUP_DIR = PLUGINS_DIR / ".backup"
SELF_NAME = Path(__file__).stem
MAX_UPLOAD = 20 * 1024 * 1024        # Bot API getFile limiti
EXPORT_PAGE = 8
GROUP_WAIT = 3.0                     # bu müddətdə gələn bütün fayllar (albom və ya ayrı) bir paketə yığılır
MERGE_TTL = 15 * 60                  # təsdiqlənməmiş paketə sonradan göndərilən fayllar da əlavə olunur
LOCAL_PKGS = {"bot", "userbot", "common", "core", "plugins"}




def safe_name(file_name: str) -> str:
    return safe_plugin_name(file_name, prefix_numeric=False)



def setup(context):
    dp = context.dp
    bot = context.bot
    batches = {}              # chat_id → təsdiq gözləyən yeniləmə paketi
    groups = {}               # chat_id → bir neçə saniyə ərzində gələn fayllar
    status = getattr(context, "plugin_status", None)
    if status is None:
        status = context.plugin_status = {}

    is_creator = context.is_creator


    # ───────────── aiogram daxilində handler-lərin idarəsi ─────────────
    def all_routers(router=None):
        router = router or dp
        yield router
        for sub in list(getattr(router, "sub_routers", [])):
            yield from all_routers(sub)

    def count_handlers(modname: str) -> int:
        n = 0
        for r in all_routers():
            for obs in r.observers.values():
                n += sum(1 for h in obs.handlers if getattr(h.callback, "__module__", None) == modname)
        return n

    def detach(modname: str) -> int:
        """Plugin-in handler, middleware və router-lərini dispetçerdən çıxarır."""
        removed = 0
        old_mod = sys.modules.get(modname)
        mod_routers = set()
        if old_mod:
            for v in vars(old_mod).values():
                if v.__class__.__name__ == "Router" and v is not dp:
                    mod_routers.add(id(v))
        for r in list(all_routers()):
            for obs in r.observers.values():
                before = len(obs.handlers)
                obs.handlers[:] = [h for h in obs.handlers
                                   if getattr(h.callback, "__module__", None) != modname]
                removed += before - len(obs.handlers)
                for mm_name in ("outer_middleware", "middleware"):
                    mm = getattr(obs, mm_name, None)
                    lst = getattr(mm, "_middlewares", None)
                    if isinstance(lst, list):
                        lst[:] = [m for m in lst if getattr(type(m), "__module__", None) != modname
                                  and getattr(m, "__module__", None) != modname]
            subs = getattr(r, "sub_routers", None)
            if isinstance(subs, list):
                for sub in [s for s in subs if id(s) in mod_routers]:
                    subs.remove(sub)
                    if hasattr(sub, "_parent_router"):
                        sub._parent_router = None
        return removed

    async def call_teardown(modname: str):
        mod = sys.modules.get(modname)
        td = getattr(mod, "teardown", None) if mod else None
        if td:
            try:
                res = td(context)
                if inspect.isawaitable(res):
                    await res
            except Exception as e:
                logger.warning(f"teardown xətası ({modname}): {e}")

    async def load_plugin(name: str) -> str:
        """Yükləyir (və ya yenidən yükləyir). Nəticə mətni qaytarır, xəta olsa exception atır."""
        modname = f"{PACKAGE}.{name}"
        removed = 0
        if modname in sys.modules:
            await call_teardown(modname)
            removed = detach(modname)
            importlib.invalidate_caches()
            module = importlib.reload(sys.modules[modname])
        else:
            importlib.invalidate_caches()
            module = importlib.import_module(modname)

        # setup-dan əvvəl hər observer-dəki handler sayı → yeniləri tapmaq üçün
        before = {(id(r), et): len(obs.handlers) for r in all_routers() for et, obs in r.observers.items()}
        res = module.setup(context)
        if inspect.isawaitable(res):
            await res

        # PRIORITY < 50 olan plugin-in handler-ləri öndə olsun (startdakı sıralama kimi)
        priority = getattr(module, "PRIORITY", 50)
        added = 0
        for r in all_routers():
            for et, obs in r.observers.items():
                n0 = before.get((id(r), et), 0)
                new = obs.handlers[n0:]
                added += len(new)
                if new and priority < 50:
                    obs.handlers[:] = new + obs.handlers[:n0]
        status[f"{name}.py"] = "ok"
        logger.info(f"🧩 Plugin yükləndi: {name} (+{added} handler, köhnə {removed} çıxarıldı)")
        return f"+{added} handler" + (f", köhnə {removed} çıxarıldı" if removed else "")

    async def unload_plugin(name: str) -> int:
        modname = f"{PACKAGE}.{name}"
        await call_teardown(modname)
        removed = detach(modname)
        sys.modules.pop(modname, None)
        status.pop(f"{name}.py", None)
        return removed

    # ───────────── 📦 fayl / toplu yeniləmə ─────────────
    def missing_libs(items) -> list:
        mods = set()
        for it in items:
            if not it.ok or it.state == "same":
                continue
            try:
                tree = ast.parse(it.data.decode("utf-8"))
            except Exception:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    mods |= {a.name.split(".")[0] for a in n.names}
                elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                    mods.add(n.module.split(".")[0])
        out = []
        for m in sorted(mods - LOCAL_PKGS):
            if m in sys.modules:
                continue
            try:
                if importlib.util.find_spec(m) is None:
                    out.append(m)
            except (ValueError, ImportError):
                pass
        return out

    STATE_ICON = {"new": "🆕", "changed": "✏️", "same": "＝"}

    def plan_view(items, note: str = ""):
        todo = [it for it in items if it.ok and it.state != "same"]
        bad = [it for it in items if not it.ok]
        same = [it for it in items if it.ok and it.state == "same"]
        lines = [f"📦 <b>Yeniləmə paketi</b> — {len(items)} fayl", "━━━━━━━━━━━━━━━━━━"]
        order = sorted(items, key=lambda it: (not it.ok, it.state == "same", it.kind, it.rel or it.name))
        for it in order[:30]:
            if not it.ok:
                lines.append(f"❌ <code>{escape(it.rel or it.name)}</code> — {escape(it.error)}")
            else:
                extra = " <i>(söndürülüb)</i>" if it.disabled else ""
                where = f"\n      <i>📍 {escape(it.where)}</i>" if it.state == "new" and getattr(it, "where", "") else ""
                lines.append(f"{STATE_ICON[it.state]} {cu.KIND_ICON[it.kind]} <code>{escape(it.rel)}</code>{extra}{where}")
        if len(order) > 30:
            lines.append(f"<i>… və daha {len(order) - 30} fayl</i>")
        lines += ["━━━━━━━━━━━━━━━━━━",
                  f"🆕 yeni: {sum(it.state == 'new' for it in todo)} · ✏️ dəyişən: {sum(it.state == 'changed' for it in todo)}"
                  f" · ＝ eyni: {len(same)}" + (f" · ❌ xəta: {len(bad)}" if bad else "")]
        if todo:
            if cu.needs_restart(todo):
                lines.append("♻️ <b>Nüvə / giriş faylı dəyişir</b> — tətbiqdən sonra botu restart et "
                             "(plugin-lər də restartda yenilənəcək)")
            else:
                lines.append("⚡ Plugin-lər <b>restartsız</b> yenilənəcək")
            miss = missing_libs(todo)
            if miss:
                lines.append(f"⚠️ Quraşdırılmamış kitabxanalar: <code>{escape(', '.join(miss))}</code>")
            lines.append("\n<i>⚠️ Kod botun içində tam icazə ilə işləyir — yalnız etibar etdiyin faylı tətbiq et. "
                         "Köhnə versiyalar yedəklənir (⏪).</i>")
        else:
            lines.append("\n<i>Tətbiq ediləcək dəyişiklik yoxdur.</i>")
        if note:
            lines.append(f"\n{note}")
        rows = []
        # 📍 yer seçimi: yeri tapılmayan / bir neçə yerdə olan / yeni nüvə faylları
        for i, it in enumerate(items):
            choices = getattr(it, "choices", None)          # köhnə code_update.py (restartdan əvvəl) — yoxdur
            if not choices or (it.ok and it.state != "new") or len(rows) >= 12:
                continue
            base = Path(choices[0]).name
            opts = [btn(f"📍 {Path(c).parent}", f"pm:loc:{i}:{j}") for j, c in enumerate(choices[:3])]
            if it.ok:
                rows.append([btn(f"{base} →", "pm:noop")] + opts)
            else:
                rows.append([btn(f"❓ {base}", "pm:noop")] + opts)
        if todo:
            label = f"✅ Xətasızları tətbiq et ({len(todo)})" if bad else f"✅ Tətbiq et ({len(todo)})"
            rows.append([btn(label, "pm:apply")])
        rows.append([btn("✖️ Ləğv et", "pm:cancel")])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    async def present(message: types.Message, items: list):
        if not items:
            await message.reply("ℹ️ Paketdə .py faylı tapılmadı.")
            return
        items = await asyncio.to_thread(cu.plan, items)
        text, kb = plan_view(items)
        sent = await message.reply(text, parse_mode="HTML", reply_markup=kb)
        batches[message.chat.id] = {"items": items, "msg": sent.message_id, "ts": time.time()}

    async def flush_group(chat_id):
        """Yığım bitdi → paket. Təsdiqlənməmiş köhnə paket varsa — onunla birləşir (köhnə plan silinir)."""
        await asyncio.sleep(GROUP_WAIT)
        g = groups.pop(chat_id, None)
        if not g:
            return
        items = g["items"]
        prev = batches.get(chat_id)
        if prev and time.time() - prev.get("ts", 0) < MERGE_TTL:
            batches.pop(chat_id, None)
            items = prev["items"] + items                 # cu.plan: eyni fayl iki dəfədirsə sonuncu qalır
            try:
                await bot.delete_message(chat_id, prev["msg"])
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/plugin_manager_plugin.py:283]: %r", _e)
        await present(g["msg"], items)

    async def is_creator_code(message: types.Message) -> bool:
        d = message.document
        name = (d.file_name or "").lower() if d else ""
        return bool(d and message.from_user and is_creator(message.from_user.id) and name.endswith((".py", ".zip")))

    @dp.message(F.chat.type == "private", F.document, is_creator_code)
    async def on_code_file(message: types.Message):
        doc = message.document
        if doc.file_size and doc.file_size > MAX_UPLOAD:
            await message.reply("❌ Fayl 20 MB-dan böyükdür (Bot API limiti).")
            return
        buf = io.BytesIO()
        try:
            await bot.download(doc, destination=buf)
        except Exception as e:
            await message.reply(f"❌ Fayl yüklənmədi: <code>{escape(str(e))[:200]}</code>", parse_mode="HTML")
            return
        name = doc.file_name or "fayl.py"
        try:
            if name.lower().endswith(".zip"):
                items = await asyncio.to_thread(cu.from_zip, buf.getvalue())
            else:
                items = [cu.Item(name=os.path.basename(name), data=buf.getvalue())]
        except Exception as e:
            await message.reply(f"❌ Arxiv oxunmadı: <code>{escape(str(e))[:200]}</code>", parse_mode="HTML")
            return
        # albom və ya ayrı-ayrı mesajlar — hamısı bir paketə (GROUP_WAIT saniyə sükutdan sonra)
        chat_id = message.chat.id
        g = groups.setdefault(chat_id, {"items": [], "msg": message, "task": None})
        g["items"] += items
        g["msg"] = message
        if g["task"] and not g["task"].done():
            g["task"].cancel()
        g["task"] = asyncio.create_task(flush_group(chat_id))

    async def hot_reload(changed: list, tag: str) -> list:
        """Dəyişən plugin-ləri restartsız yükləyir; yüklənməyəni köhnə versiyasına qaytarır."""
        out = []
        ub = getattr(context, "userbot", None)
        ub_load = ub.state.get("load_plugin_file") if ub else None
        for f in changed:
            rel, kind = f["rel"], f["kind"]
            stem = Path(rel).stem
            if kind == "lang":                              # 🌐 dil faylı — yenidən oxu (geri qaytarmada da)
                try:
                    from common import i18n
                    i18n.reload()
                    out.append(f"🌐 <code>{escape(rel)}</code> — dil aktivdir ({i18n.coverage(stem)}%)"
                               if stem in i18n.LANGS else f"🌐 <code>{escape(rel)}</code> — silindi")
                except Exception as e:
                    out.append(f"❌ <code>{escape(rel)}</code>: {escape(str(e))[:100]}")
                continue
            if rel.startswith("bot/plugins/disabled/"):
                out.append(f"⏸ <code>{escape(stem)}</code> — söndürülüb, yüklənmədi")
                continue
            path = Path(BASE_DIR) / rel
            if kind == "bot_plugin":
                try:
                    if not path.exists():
                        await unload_plugin(stem)
                        out.append(f"🗑 <code>{escape(stem)}</code> çıxarıldı")
                        continue
                    r = await load_plugin(stem)
                    out.append(f"✅ 🔌 <code>{escape(stem)}</code> · {escape(r)}")
                except Exception as e:
                    status[f"{stem}.py"] = f"yüklənmə xətası: {type(e).__name__}: {e}"
                    logger.error(f"Plugin yüklənmədi ({stem}): {e}", exc_info=True)
                    note = ""
                    if tag:
                        cu.restore_one(tag, rel)
                        if path.exists():
                            try:
                                await load_plugin(stem)
                                note = " — ♻️ köhnə versiya qaytarıldı"
                            except Exception:
                                note = " — ⚠️ köhnə versiya da yüklənmədi"
                    out.append(f"❌ 🔌 <code>{escape(stem)}</code>: {escape(str(e))[:150]}{note}")
            elif kind == "ub_plugin":
                if ub_load is None:
                    out.append(f"ℹ️ 📱 <code>{escape(stem)}</code> — userbot işləmir, növbəti başlanğıcda yüklənəcək")
                    continue
                try:
                    if not path.exists():
                        ub.remove_plugin(stem)
                        ub.plugin_files.pop(stem, None)
                        out.append(f"🗑 📱 <code>{escape(stem)}</code> çıxarıldı")
                        continue
                    r = ub_load(str(path))
                    if inspect.isawaitable(r):
                        await r
                    out.append(f"✅ 📱 <code>{escape(stem)}</code>")
                except Exception as e:
                    logger.error(f"Userbot plugini yüklənmədi ({stem}): {e}", exc_info=True)
                    note = ""
                    if tag:
                        cu.restore_one(tag, rel)
                        if path.exists():
                            try:
                                r = ub_load(str(path))
                                if inspect.isawaitable(r):
                                    await r
                                note = " — ♻️ köhnə versiya qaytarıldı"
                            except Exception:
                                note = " — ⚠️ köhnə versiya da yüklənmədi"
                    out.append(f"❌ 📱 <code>{escape(stem)}</code>: {escape(str(e))[:150]}{note}")
        return out

    def result_kb(tag: str = None, restart: bool = False):
        rows = []
        rb = getattr(context, "restart_button", None)
        if restart and rb:
            rows.append([rb("♻️ Botu restart et")])
        if tag:
            rows.append([btn("⏪ Geri qaytar", f"pm:rb:{tag}")])
        rows.append(ui.nav("pm:list", "Plugin-lər", close_data="pm:close"))
        return InlineKeyboardMarkup(inline_keyboard=rows)

    # ───────────── 📤 export ─────────────
    def export_view(note: str = ""):
        lines = ["📤 <b>Kod exportu</b>", "━━━━━━━━━━━━━━━━━━"]
        total = 0
        rows = []
        zone_btns = []
        for i, (key, label, _) in enumerate(cu.EXPORT_ZONES):
            n = len(cu.zone_files(key))
            total += n
            lines.append(f"{label}: <b>{n}</b> fayl")
            zone_btns.append(btn(f"{label} ({n})", f"pm:exz:{i}"))
        lines += ["━━━━━━━━━━━━━━━━━━",
                  "📦 <b>.zip</b> — yollar layihədəki kimi qalır: bota geri göndərsən 📥 yeniləmə kimi quraşdırılır",
                  "📄 <b>Tək fayl</b> — zonanı seç, sonra faylı",
                  "<i>Sessiya, baza və config.env heç vaxt daxil edilmir — yalnız .py kod.</i>"]
        if note:
            lines.append(f"\n{note}")
        rows.append([btn(f"📦 Hamısı — .zip ({total} fayl)", "pm:exall")])
        rows += [zone_btns[i:i + 2] for i in range(0, len(zone_btns), 2)]
        rows.append([btn("📄 Tək fayl seç", "pm:exl")])
        rows.append(ui.nav("pm:list", "Plugin-lər", close_data="pm:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def export_pick_view(zi: int = -1, page: int = 0):
        rows = []
        if zi < 0:
            lines = ["📄 <b>Tək fayl — zonanı seç</b>"]
            for i, (key, label, _) in enumerate(cu.EXPORT_ZONES):
                rows.append([btn(f"{label} ({len(cu.zone_files(key))})", f"pm:exl:{i}:0")])
            rows.append(ui.nav("pm:ex", "Export", close_data="pm:close"))
            return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)
        key, label, _ = cu.EXPORT_ZONES[zi]
        files = cu.zone_files(key)
        pages = max(1, -(-len(files) // EXPORT_PAGE))
        page = min(max(page, 0), pages - 1)
        lines = [f"📄 <b>{label}</b> — faylı seç", f"<i>Səhifə {page + 1}/{pages} · {len(files)} fayl</i>"]
        chunk = files[page * EXPORT_PAGE:(page + 1) * EXPORT_PAGE]
        for j, rel in enumerate(chunk, start=page * EXPORT_PAGE):
            nm = Path(rel).name + (" ⏸" if "/disabled/" in rel else "")
            rows.append([btn(f"📄 {nm}"[:60], f"pm:exf:{zi}:{j}")])
        if pages > 1:
            rows.append(ui.pager(f"pm:exl:{zi}:", page, pages, "pm:noop"))
        rows.append([btn(f"📦 Bütün {label} — .zip", f"pm:exz:{zi}")])
        rows.append(ui.nav("pm:exl", "Zonalar", close_data="pm:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    async def send_export(chat_id: int, keys: list, label: str) -> str:
        data, n, size = await asyncio.to_thread(cu.export_zip, keys)
        if not n:
            return "ℹ️ Göndəriləcək fayl yoxdur"
        if size > 50 * 1024 * 1024:
            return "❌ Arxiv 50 MB-dan böyükdür (Bot API limiti)"
        tag = "_".join(keys) if len(keys) < len(cu.EXPORT_ZONES) else "all"
        fname = f"dlmaster_{tag}_{time.strftime('%Y%m%d_%H%M')}.zip"
        await bot.send_document(chat_id, BufferedInputFile(data, filename=fname),
                                caption=f"📤 <b>{escape(label)}</b> · {n} fayl · {size // 1024 + 1} KB\n"
                                        "<i>Geri quraşdırmaq: bu arxivi bota göndər.</i>",
                                parse_mode="HTML")
        logger.info(f"📤 Kod exportu: {label} ({n} fayl)")
        return f"✅ Göndərildi: {escape(label)} — {n} fayl"

    async def send_file(chat_id: int, rel: str) -> str:
        path = Path(BASE_DIR) / rel
        if not path.is_file():
            return "❌ Fayl tapılmadı"
        await bot.send_document(chat_id, FSInputFile(str(path), filename=path.name),
                                caption=f"📄 <code>{escape(rel)}</code> · {path.stat().st_size} bayt\n"
                                        "<i>Dəyişib bota geri göndərsən — eyni yerə yenilənir.</i>",
                                parse_mode="HTML")
        return f"✅ Göndərildi: <code>{escape(rel)}</code>"

    def hist_view(note: str = ""):
        lines = ["📦 <b>Yeniləmə tarixçəsi</b>", "━━━━━━━━━━━━━━━━━━"]
        rows = []
        hist = cu.history(6)
        if not hist:
            lines.append("<i>Hələ yeniləmə tətbiq edilməyib.</i>")
        for tag, m in hist:
            files = m.get("files", [])
            kinds = " ".join(f"{cu.KIND_ICON.get(k, '📄')}{n}" for k, n in sorted(
                {f["kind"]: sum(1 for x in files if x["kind"] == f["kind"]) for f in files}.items()))
            done = " · <i>⏪ geri qaytarılıb</i>" if m.get("rolled_back") else ""
            when = time.strftime("%d.%m %H:%M", time.localtime(m.get("created", 0)))
            lines.append(f"🕒 <b>{when}</b> · {len(files)} fayl · {kinds}{done}")
            for f in files[:4]:
                lines.append(f"     {'✏️' if f['existed'] else '🆕'} <code>{escape(f['rel'])}</code>")
            if len(files) > 4:
                lines.append(f"     <i>… +{len(files) - 4}</i>")
            if not m.get("rolled_back"):
                rows.append([btn(f"⏪ {when} yeniləməsini geri qaytar", f"pm:rb:{tag}")])
        if note:
            lines.append(f"\n{note}")
        rows.append(ui.nav("pm:list", "Plugin-lər", close_data="pm:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    # ───────────── /plugins siyahısı ─────────────
    def plugin_files():
        active = sorted(p.stem for p in PLUGINS_DIR.glob("*.py") if p.stem != "__init__")
        off = sorted(p.stem for p in DISABLED_DIR.glob("*.py")) if DISABLED_DIR.exists() else []
        return active, off

    def list_view():
        active, off = plugin_files()
        lines = ["🧩 <b>Plugin-lər</b>\n"]
        for n in active:
            st = status.get(f"{n}.py")
            icon = "✅" if st == "ok" else ("❌" if st else "⚪")
            lines.append(f"{icon} <code>{escape(n)}</code> · {count_handlers(f'{PACKAGE}.{n}')} handler")
        for n in off:
            lines.append(f"⏸ <code>{escape(n)}</code> <i>(söndürülüb)</i>")
        lines.append("\n<i>Yeni plugin və ya yeniləmə: .py faylını, bir neçə faylı (albom) və ya .zip-i bota göndər. "
                     "common/ və core faylları da olar.</i>")
        rows = [[btn(f"{'⏸' if n in off else '🧩'} {n}"[:40], f"pm:open:{n}")] for n in active + off]
        rows.append([btn("📤 Export", "pm:ex"), btn("📦 Yeniləmə tarixçəsi", "pm:hist")])
        rows.append(ui.nav("menu:dev", "Bot idarəsi", close_data="pm:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def card_view(name: str, note: str = ""):
        active, off = plugin_files()
        path = (DISABLED_DIR if name in off else PLUGINS_DIR) / f"{name}.py"
        size = path.stat().st_size if path.exists() else 0
        st = status.get(f"{name}.py")
        state = "⏸ söndürülüb" if name in off else ("✅ işləyir" if st == "ok" else f"❌ {st}" if st else "⚪ yüklənməyib")
        text = (f"🧩 <b>{escape(name)}</b>\n\n📶 {escape(state)[:300]}\n"
                f"📄 {size} bayt · 🔌 {count_handlers(f'{PACKAGE}.{name}')} handler"
                + (f"\n\n{note}" if note else ""))
        rows = []
        if name in off:
            rows.append([btn("▶️ Aktiv et", f"pm:enable:{name}")])
        elif name != SELF_NAME:
            rows.append([btn("🔄 Yenidən yüklə", f"pm:reload:{name}"), btn("⏸ Deaktiv et", f"pm:disable:{name}")])
        rows.append([btn("📤 Faylı göndər", f"pm:exp:{name}")])
        if name != SELF_NAME:
            rows.append([btn("🗑 Sil", f"pm:delete:{name}")])
        rows.append(ui.nav("pm:list", "", close_data="pm:close"))
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    @dp.message(Command("plugins"))
    async def plugins_cmd(message: types.Message):
        if not message.from_user or not is_creator(message.from_user.id):
            return
        text, kb = list_view()
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp.callback_query(F.data.startswith("pm:"))
    async def pm_callback(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        action, name = parts[1], (parts[2] if len(parts) > 2 else "")

        async def show(text, kb):
            try:
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                if "not modified" not in str(e):
                    logger.warning(f"Plugin paneli yenilənmədi: {e}")

        if action == "close":
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/plugin_manager_plugin.py:573]: %r", _e)
            return
        if action == "list":
            await cb.answer()
            await show(*list_view())
            return
        if action == "open":
            await cb.answer()
            await show(*card_view(name))
            return

        if action == "noop":
            await cb.answer()
            return
        if action == "ex":
            await cb.answer()
            await show(*export_view())
            return
        if action == "exl":
            await cb.answer()
            zi = int(name) if name.isdigit() and int(name) < len(cu.EXPORT_ZONES) else -1
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            await show(*export_pick_view(zi, page))
            return
        if action in ("exall", "exz"):
            if action == "exall":
                keys, label = [z[0] for z in cu.EXPORT_ZONES], "Bütün kod"
            elif name.isdigit() and int(name) < len(cu.EXPORT_ZONES):
                z = cu.EXPORT_ZONES[int(name)]
                keys, label = [z[0]], z[1]
            else:
                await cb.answer()
                return
            await cb.answer("📦 Hazırlanır...")
            try:
                note = await send_export(cb.message.chat.id, keys, label)
            except Exception as e:
                logger.error(f"Export alınmadı: {e}", exc_info=True)
                note = f"❌ <code>{escape(str(e))[:200]}</code>"
            await show(*export_view(note))
            return
        if action in ("exf", "exp"):
            if action == "exp":
                rel = next((r for r in cu.zone_files("bplug") if Path(r).stem == name), None)
                back = card_view
            else:
                zi = int(name) if name.isdigit() else -1
                j = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else -1
                files = cu.zone_files(cu.EXPORT_ZONES[zi][0]) if 0 <= zi < len(cu.EXPORT_ZONES) else []
                rel = files[j] if 0 <= j < len(files) else None
                back = None
            if not rel:
                await cb.answer("Fayl tapılmadı — siyahını yenilə", show_alert=True)
                return
            await cb.answer("📤 Göndərilir...")
            try:
                note = await send_file(cb.message.chat.id, rel)
            except Exception as e:
                note = f"❌ <code>{escape(str(e))[:200]}</code>"
            if back is not None:
                await show(*card_view(name, note))
            elif note.startswith("❌"):
                await cb.message.answer(note, parse_mode="HTML")
            return

        if action == "hist":
            await cb.answer()
            await show(*hist_view())
            return

        if action == "loc":                               # 📍 faylın yerini əl ilə seç
            b = batches.get(cb.message.chat.id)
            try:
                it = b["items"][int(name)]
                choice = it.choices[int(parts[3])]
            except (TypeError, IndexError, ValueError, KeyError, AttributeError):
                await cb.answer("Paket köhnəlib — faylları yenidən göndər", show_alert=True)
                return
            it.forced = choice
            b["items"] = await asyncio.to_thread(cu.plan, b["items"])
            b["ts"] = time.time()
            await cb.answer(f"📍 {choice}")
            await show(*plan_view(b["items"]))
            return

        if action == "cancel":
            batches.pop(cb.message.chat.id, None)
            await cb.answer("Ləğv edildi")
            await show("❌ Yeniləmə ləğv edildi.", None)
            return

        if action == "apply":
            b = batches.pop(cb.message.chat.id, None)
            if not b:
                await cb.answer("Paket tapılmadı — faylları yenidən göndər", show_alert=True)
                return
            items = b["items"]
            todo = [it for it in items if it.ok and it.state != "same"]
            if not todo:
                await cb.answer("Tətbiq ediləcək dəyişiklik yoxdur", show_alert=True)
                return
            await cb.answer("Tətbiq olunur...")
            await show("⏳ <b>Tətbiq olunur...</b>", None)
            try:
                tag = await asyncio.to_thread(cu.apply, items)
            except Exception as e:
                logger.error(f"Yeniləmə yazılmadı: {e}", exc_info=True)
                await show(f"❌ <b>Fayllar yazılmadı:</b> <code>{escape(str(e))[:300]}</code>", result_kb())
                return
            restart = cu.needs_restart(todo)
            changed = [{"rel": it.rel, "kind": it.kind} for it in todo]
            lines = [f"✅ <b>Yeniləmə tətbiq olundu</b> — {len(todo)} fayl", "━━━━━━━━━━━━━━━━━━"]
            if restart:
                lines += [f"{cu.KIND_ICON[it.kind]} <code>{escape(it.rel)}</code>" for it in todo[:25]]
                lines += ["━━━━━━━━━━━━━━━━━━",
                          "♻️ <b>Restart lazımdır</b> — nüvə faylları yalnız yenidən başlayanda yüklənir. "
                          "Plugin-lər də restartda yeni versiya ilə açılacaq."]
            else:
                lines += await hot_reload(changed, tag)
            lines.append(f"\n<i>⏪ Köhnə versiyalar: data/update_backups/{tag}/</i>")
            logger.warning(f"📦 Kod yeniləməsi tətbiq olundu ({tag}): " + ", ".join(it.rel for it in todo))
            await show("\n".join(lines)[:4000], result_kb(tag, restart))
            return

        if action == "rb":
            tag = name
            try:
                files = await asyncio.to_thread(cu.rollback, tag)
            except Exception as e:
                await cb.answer("Geri qaytarılmadı", show_alert=True)
                await show(*hist_view(f"❌ <code>{escape(str(e))[:200]}</code>"))
                return
            await cb.answer("⏪ Geri qaytarıldı")
            restart = any(f["kind"] in ("core", "entry") or f["rel"] in cu.RESTART_PLUGINS
                          or Path(f["rel"]).stem == "__init__" for f in files)
            lines = [f"⏪ <b>Yeniləmə geri qaytarıldı</b> — {len(files)} fayl", "━━━━━━━━━━━━━━━━━━"]
            if restart:
                lines += [f"{'↩️' if f['existed'] else '🗑'} <code>{escape(f['rel'])}</code>" for f in files[:25]]
                lines.append("━━━━━━━━━━━━━━━━━━\n♻️ <b>Restart lazımdır</b> — nüvə faylları köhnə versiyaya qayıtdı.")
            else:
                lines += await hot_reload(files, "")
            await show("\n".join(lines)[:4000], result_kb(None, restart))
            return

        if not name or name == SELF_NAME:
            await cb.answer("Bu plugin-ə toxunmaq olmaz", show_alert=True)
            return

        if action == "reload":
            await cb.answer("Yenidən yüklənir...")
            try:
                result = await load_plugin(name)
                await show(*card_view(name, f"✅ Yenidən yükləndi: {escape(result)}"))
            except Exception as e:
                status[f"{name}.py"] = f"yüklənmə xətası: {type(e).__name__}: {e}"
                await show(*card_view(name, f"❌ <code>{escape(str(e))[:300]}</code>"))
            return

        if action == "disable":
            await cb.answer()
            removed = await unload_plugin(name)
            DISABLED_DIR.mkdir(exist_ok=True)
            src = PLUGINS_DIR / f"{name}.py"
            if src.exists():
                shutil.move(src, DISABLED_DIR / f"{name}.py")
            await show(*card_view(name, f"⏸ Söndürüldü ({removed} handler çıxarıldı). Restartdan sonra da yüklənməyəcək."))
            return

        if action == "enable":
            await cb.answer()
            src = DISABLED_DIR / f"{name}.py"
            if src.exists():
                shutil.move(src, PLUGINS_DIR / f"{name}.py")
            try:
                result = await load_plugin(name)
                await show(*card_view(name, f"▶️ Aktivləşdirildi: {escape(result)}"))
            except Exception as e:
                status[f"{name}.py"] = f"yüklənmə xətası: {type(e).__name__}: {e}"
                await show(*card_view(name, f"❌ <code>{escape(str(e))[:300]}</code>"))
            return

        if action == "delete":
            await cb.answer()
            await show(f"⚠️ <b>{escape(name)}</b> silinsin?\n<i>Fayl .backup/-a köçürüləcək.</i>",
                       InlineKeyboardMarkup(inline_keyboard=[ui.confirm(f"pm:deletey:{name}", f"pm:open:{name}", "sil")]))
            return

        if action == "deletey":
            await cb.answer("Silindi")
            await unload_plugin(name)
            BACKUP_DIR.mkdir(exist_ok=True)
            for d in (PLUGINS_DIR, DISABLED_DIR):
                f = d / f"{name}.py"
                if f.exists():
                    shutil.move(f, BACKUP_DIR / f"{name}_deleted_{time.strftime('%Y%m%d_%H%M%S')}.py")
            await show(*list_view())
            return

        await cb.answer()

    logger.info("✅ Plugin meneceri yükləndi")
