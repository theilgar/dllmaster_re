"""
📦 GitHub arxivi — bot/plugins/archive_plugin.py (yalnız creator)

/menu → 🖥 Sistem → 🛠 Bot idarəsi → 📦 GitHub arxivi
  🌿 Push-dan sonra          — hər uğurlu GitHub push-dan sonra arxiv hədəfə gedir
  🔄 Kod dəyişəndə           — GitHub-a göndərilməsə də: 📥 yeniləmə, git pull, əl ilə redaktə → arxiv
                               (hər N dəqiqədən bir yoxlanılır, ⏱ ilə dəyişilir; caption-da dəyişən fayllar)
  📍 Hədəf                   — kanal / qrup / community mövzusu (📍 Hədəflər və ya mövzuda /setarchive)
  🗂 Bölmələr                — common, bot/core, bot/plugins, userbot/core, userbot/plugins, giriş faylları,
                               requirements.txt, deps/, bot/assets — ✅/❌
  📄 Fayllar                 — bölmənin içindən ayrı-ayrı faylları çıxar / əlavə et
  📤 İndi göndər             — push gözləmədən (sınaq və ya əl ilə)
Sirlər və bazalar (config.env, *.session, data/ ...) heç vaxt arxivə düşmür — bot/core/archive.py.
"""
import asyncio
import contextlib
import logging
import time
from html import escape

from aiogram import F, types
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from common import archive as ar
from common import destinations as dest
from bot.core import panel as ui
from bot.core.panel import btn, show as panel_show

logger = logging.getLogger(__name__)

PRIORITY = 75
FILE_PAGE = 8




def setup(context):
    dp, bot = context.dp, context.bot
    state = {"last": None}



    def changes_text(d: dict) -> str:
        if not d:
            return ""
        parts = []
        for key, icon in (("new", "🆕"), ("changed", "✏️"), ("gone", "🗑")):
            for rel in d[key]:
                parts.append(f"{icon} {rel}")
        if not parts:
            return ""
        more = f"\n… və daha {len(parts) - 12}" if len(parts) > 12 else ""
        return "\n" + escape("\n".join(parts[:12])) + more

    # ── göndərmə ──
    async def send_archive(info: dict = None, manual: bool = False) -> str:
        kw = dest.kwargs("archive")
        if not kw:
            return "⚠️ hədəf yoxdur (📍 Hədəflər və ya mövzuda /setarchive)"
        man, _ = await asyncio.to_thread(ar.manifest)
        old = await asyncio.to_thread(ar.sent_manifest)
        d = ar.diff(old, man) if old is not None else None
        data, files = await asyncio.to_thread(ar.build, info)
        if not files:
            return "⚠️ seçilmiş fayl yoxdur"
        if len(data) > ar.SEND_LIMIT:
            return f"❌ arxiv {len(data) // 1048576} MB — 50 MB limitindən böyükdür"
        info = info or {}
        stamp = time.strftime("%Y%m%d_%H%M")
        name = f"dlmaster_{info.get('hash') or stamp}.zip"
        caption = ("📦 <b>DLLMaster kod arxivi</b>"
                   + (f" · <code>{escape(info['hash'])}</code>" if info.get("hash") else "")
                   + (f" · 🌿 <code>{escape(info['branch'])}</code>" if info.get("branch") else "")
                   + f"\n📁 {len(files)} fayl · {len(data) // 1024 + 1} KB · {escape(', '.join(ar.items()))}"
                   + (f"\n🔗 <a href=\"{escape(info['url'])}\">commit</a>" if info.get("url") else "")
                   + ("\n🔄 <i>kod dəyişdi</i>" if info.get("reason") == "change" else "")
                   + ("\n<i>əl ilə göndərilib</i>" if manual else "")
                   + changes_text(d))
        try:
            await bot.send_document(document=BufferedInputFile(data, filename=name), caption=caption[:1024],
                                    parse_mode="HTML", disable_notification=not manual, **kw)
        except Exception as e:
            logger.warning(f"📦 Arxiv göndərilmədi: {e}")
            return f"❌ göndərilmədi: {str(e)[:150]}"
        state["last"] = time.time()
        await asyncio.to_thread(ar.mark_sent, man)         # 🔄 izləyici bu vəziyyəti artıq göndərilmiş sayır
        logger.info(f"📦 Kod arxivi göndərildi → {dest.describe('archive')} ({len(files)} fayl)")
        return f"✅ {len(files)} fayl → {dest.describe('archive')}"

    async def after_push(res: dict) -> str:
        """update_notifier uğurlu push-dan sonra çağırır. Söndürülübsə / hədəf yoxdursa boş."""
        if not ar.enabled() or not dest.get("archive"):
            return ""
        return await send_archive({"hash": res.get("hash"), "branch": res.get("branch"), "url": res.get("url")})

    context.archive_after_push = after_push

    # ── 🔄 kod dəyişəndə (GitHub-dan asılı olmadan) ──
    QUIET = 60                    # son yazılışdan bu qədər saniyə keçməlidir — yeniləmə yarımçıq getməsin

    async def check_change() -> str:
        if not dest.get("archive"):
            return "⚠️ hədəf yoxdur"
        man, newest = await asyncio.to_thread(ar.manifest)
        old = await asyncio.to_thread(ar.sent_manifest)
        if old is None:                                   # ilk dəfə — indiki vəziyyət başlanğıc sayılır
            await asyncio.to_thread(ar.mark_sent, man)
            return "ℹ️ başlanğıc vəziyyəti yadda saxlandı — növbəti dəyişiklikdə göndəriləcək"
        d = ar.diff(old, man)
        if not any(d.values()):
            return "＝ dəyişiklik yoxdur"
        if time.time() - newest < QUIET:
            return "⏳ fayllar hələ yazılır — bir az sonra"
        return await send_archive({"reason": "change"})

    async def watcher():
        await asyncio.sleep(30)
        while True:
            try:
                if ar.on_change():
                    res = await check_change()
                    if res.startswith(("✅", "❌")):
                        logger.info(f"📦 🔄 Kod dəyişikliyi arxivi: {res}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"📦 Dəyişiklik yoxlaması: {e}")
            await asyncio.sleep(ar.interval() * 60)

    old_task = getattr(context, "_archive_watch", None)
    if old_task and not old_task.done():
        old_task.cancel()
    try:
        context._archive_watch = asyncio.get_running_loop().create_task(watcher())
    except RuntimeError:                                  # startup-da loop hələ işləmir
        async def _start(**_):
            context._archive_watch = asyncio.create_task(watcher())
        dp.startup.register(_start)

    def label() -> str:
        on = (ar.enabled() or ar.on_change()) and dest.get("archive")
        return "📦 Kod arxivi · " + ("✅" if on else "❌")

    context.archive_label = label

    # ── görünüş ──
    def panel(note: str = ""):
        on = ar.enabled()
        keys = ar.items()
        chg = ar.on_change()
        lines = ["<i>🏠 › 🖥 Sistem › 🛠 Bot idarəsi</i>", "📦 <b>Kod arxivi</b>", "━━━━━━━━━━━━━━━━━━",
                 "Seçilmiş hissələrin <b>.zip</b> arxivi hədəfə göndərilir — GitHub-dan asılı olmayaraq da.",
                 "",
                 f"🌿 Push-dan sonra: <b>{ui.state(on)}</b>",
                 f"🔄 Kod dəyişəndə: <b>{'🟢 hər ' + str(ar.interval()) + ' dəq. yoxlanılır' if chg else '🔴 deaktiv'}</b>",
                 f"📍 Hədəf: <b>{escape(dest.describe('archive'))}</b>",
                 f"🗂 Tərkib: <b>{escape(ar.summary())}</b>",
                 "━━━━━━━━━━━━━━━━━━"]
        for k in ar.ORDER:
            n = len(ar.files_of(k))
            lines.append(f"{'✅' if k in keys else '▫️'} {ar.ITEMS[k][0]} — {n} fayl")
        lines += ["━━━━━━━━━━━━━━━━━━",
                  "<i>Qrup / community mövzusu üçün həmin mövzuda /setarchive yaz.\n"
                  "🔒 config.env, sessiyalar, bazalar heç vaxt daxil edilmir.</i>"]
        if note:
            lines.append(f"\n{note}")
        rows = [[ui.toggle("🌿 Push-dan sonra", on, "ar:on", compact=True),
                 ui.toggle("🔄 Dəyişəndə", chg, "ar:chg", compact=True)],
                [btn(f"⏱ {ar.interval()} dəq.", "ar:int"), btn("🔍 İndi yoxla", "ar:check"),
                 btn("📍 Hədəf", "ds:set:archive")]]
        row = []
        for k in ar.ORDER:
            row.append(ui.check(ar.ITEMS[k][0].split(' ', 1)[1], k in keys, f"ar:it:{k}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([btn("📄 Faylları seç", "ar:fz"), btn("📤 İndi göndər", "ar:send")])
        rows.append([btn("♻️ Standart ayarlar", "ar:reset")])
        rows.append(ui.nav("menu:arch", "Arxiv", close_data="ar:close", refresh_data="ar:panel"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def zones_view():
        rows = [[btn(f"{ar.ITEMS[k][0]} ({len(ar.files_of(k))})", f"ar:fl:{i}:0")]
                for i, k in enumerate(ar.ORDER)]
        rows.append(ui.nav("ar:panel", "Kod arxivi", close_data="ar:close"))
        return ("📄 <b>Faylları seç</b> — bölməni aç, faylları ✅ / ▫️ et.\n"
                "<i>Bölmə özü ▫️-dirsə, faylları arxivə düşmür.</i>"), InlineKeyboardMarkup(inline_keyboard=rows)

    def files_view(zi: int, page: int = 0):
        key = ar.ORDER[zi]
        files = ar.files_of(key)
        ex = ar.excluded()
        pages = max(1, -(-len(files) // FILE_PAGE))
        page = min(max(page, 0), pages - 1)
        on = key in ar.items()
        lines = [f"📄 <b>{ar.ITEMS[key][0]}</b> — {len(files)} fayl · səhifə {page + 1}/{pages}",
                 ("" if on else "⚠️ <i>Bu bölmə söndürülüb — faylları arxivə düşmür</i>")]
        rows = []
        chunk = files[page * FILE_PAGE:(page + 1) * FILE_PAGE]
        for j, rel in enumerate(chunk, start=page * FILE_PAGE):
            rows.append([ui.check(rel.rsplit('/', 1)[-1][:56], rel not in ex, f"ar:ft:{zi}:{j}:{page}")])
        if pages > 1:
            rows.append(ui.pager(f"ar:fl:{zi}:", page, pages, "ar:noop"))
        rows.append(ui.nav("ar:fz", "Bölmələr", close_data="ar:close"))
        return "\n".join(l for l in lines if l), InlineKeyboardMarkup(inline_keyboard=rows)

    show = panel_show

    @dp.callback_query(F.data.startswith("ar:"))
    async def ar_cb(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        act = parts[1]
        note = ""
        if act == "noop":
            await cb.answer()
            return
        if act == "close":
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return
        if act == "fz":
            await cb.answer()
            await show(cb, *zones_view())
            return
        if act == "fl" and len(parts) > 3:
            await cb.answer()
            await show(cb, *files_view(int(parts[2]), int(parts[3])))
            return
        if act == "ft" and len(parts) > 4:
            zi, j, page = int(parts[2]), int(parts[3]), int(parts[4])
            files = ar.files_of(ar.ORDER[zi]) if 0 <= zi < len(ar.ORDER) else []
            if 0 <= j < len(files):
                inc = ar.toggle_file(files[j])
                await cb.answer(("✅ daxil: " if inc else "❌ çıxarıldı: ") + files[j].rsplit("/", 1)[-1])
            else:
                await cb.answer("Siyahı dəyişib", show_alert=True)
            await show(cb, *files_view(zi, page))
            return
        if act == "on":
            ar.set_enabled(not ar.enabled())
            await cb.answer("🌿 Push-dan sonra: " + ("açıldı" if ar.enabled() else "söndürüldü"))
        elif act == "chg":
            ar.set_on_change(not ar.on_change())
            if ar.on_change() and ar.sent_manifest() is None:   # başlanğıc: indiki kod "göndərilmiş" sayılır
                ar.mark_sent((await asyncio.to_thread(ar.manifest))[0])
            await cb.answer("🔄 Kod dəyişəndə: " + ("açıldı" if ar.on_change() else "söndürüldü"))
        elif act == "int":
            v = ar.cycle_interval()
            await cb.answer(f"⏱ hər {v} dəq.")
        elif act == "check":
            await cb.answer("🔍 Yoxlanılır...")
            note = escape(await check_change())
        elif act == "it" and len(parts) > 2 and parts[2] in ar.ITEMS:
            keys = ar.items()
            k = parts[2]
            keys = [x for x in keys if x != k] if k in keys else keys + [k]
            ar.set_items([x for x in ar.ORDER if x in keys])
            await cb.answer(("✅ " if k in keys else "❌ ") + ar.ITEMS[k][0])
        elif act == "send":
            await cb.answer("📤 Hazırlanır...")
            note = escape(await send_archive(manual=True))
        elif act == "reset":
            ar.reset()
            await cb.answer("♻️ Defolt")
        else:
            await cb.answer()
        await show(cb, *panel(note))

    logger.info("✅ Kod arxivi plugin-i yükləndi")


def teardown(context):
    task = getattr(context, "_archive_watch", None)
    if task and not task.done():
        task.cancel()
