"""
🛠 Userbot host — Telethon userbot-un plugin yükləyicisi və admin paneli.

Ayrıca client yaratmır: depo_filler_plugin-in qoşduğu userbot-u (context.telethon_client) istifadə edir.
Hər komanda ayrıca fayldır → userbot/plugins/ qovluğunda. Bu host:
  1) UB reyestrini qurur,
  2) userbot/plugins/*.py fayllarının hər birini yükləyib register(ub) çağırır,
  3) userbot qoşulan kimi bütün komanda və xam handler-ləri client-ə bağlayır,
  4) .menu (plugin idarəsi, userbot/core/plugin_menu.py) və .panel / .help (admin paneli) qurur.

Yeni komanda əlavə etmək: userbot/plugins/ içində yeni .py faylı yarat, içində register(ub) yaz.
Nümunə üçün userbot/plugins/_example.py.txt fayllarına bax.
"""
import asyncio
import importlib.util
import logging
import os
import sys
import time

from aiogram import F, types as ag_types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent

from userbot.core.userbot_api import UB, tr

logger = logging.getLogger(__name__)

# komanda pluginlərinin qovluğu: <kök>/userbot/plugins
PLUGINS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")


def load_plugin_file(ub, path, attach=True):
    """Bir .py faylını yükləyib register(ub) çağırır; owner = fayl adı. (name, info) qaytarır.

    Köhnə eyniadlı plugin varsa əvvəlcə onun qeydləri geri alınır (reload).
    """
    name = os.path.splitext(os.path.basename(path))[0]
    mod_name = f"userbot.plugins.{name}"
    if name in ub.plugin_files:
        ub.remove_plugin(name)                       # reload — köhnəni təmizlə
    prev = ub._loading
    ub._loading = name
    try:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        if not hasattr(mod, "register"):
            raise RuntimeError("register(ub) funksiyası yoxdur")
        mod.register(ub)
    except Exception:
        ub.remove_plugin(name)                       # yarımçıq qeydləri geri al
        sys.modules.pop(mod_name, None)
        ub._loading = prev
        raise
    ub._loading = prev
    ub.plugin_files[name] = path
    if attach:
        ub.attach_pending()
    return name, ub.plugin_summary(name)


def load_command_plugins(ub) -> list:
    names = []
    if not os.path.isdir(PLUGINS_DIR):
        logger.warning(f"⚠️ userbot/plugins qovluğu yoxdur: {PLUGINS_DIR}")
        return names
    from userbot.core import plugin_menu
    off = set(plugin_menu.disabled(ub)) - plugin_menu.PROTECTED
    for fn in sorted(os.listdir(PLUGINS_DIR)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        if fn[:-3] in off:
            logger.info(f"⏸ Userbot plugini söndürülüb (.menu): {fn}")
            continue
        try:
            name, _ = load_plugin_file(ub, os.path.join(PLUGINS_DIR, fn), attach=False)
            names.append(name)
        except Exception as e:
            logger.error(f"❌ Userbot plugini yüklənmədi ({fn}): {e}", exc_info=True)
    return names


def setup(context):
    from userbot.core import plugin_menu, plugin_style, userbot_restart
    dp = context.dp
    ub = UB(context)
    context.userbot = ub

    # help kateqoriyaları (komandalar bu kateqoriyalara add_help edir)
    ub.help_category("acc", "💬 Hesab komandaları", "Öz hesabından istənilən çatda yazılır", order=10)
    ub.help_category("bot", "🤖 Bot komandaları", "Botun özündə (@dllmasterbot) yazılır", order=20)
    ub.help_category("panel", "🛠 Panel", "Bu admin panelinin bölmələri", order=30)

    loaded = load_command_plugins(ub)
    logger.info(f"🧩 Userbot pluginləri yükləndi ({len(loaded)}): {', '.join(loaded) or '—'}")

    # bot tərəfindəki komandalar (userbot deyil — sadəcə help üçün)
    ub.add_help("userbot", "/userbot", "userbot statusu",
                "Botda yazılır: userbot qoşulubmu, hesab, Telegram mənbələrinin sayı.\n\n<i>Paneldə:</i> 📊 Status", "bot")
    ub.add_help("depo_add", "/depo add", "daimi Telegram mənbəyi",
                "Kanalı / qrupu depoya daimi mənbə kimi əlavə edir: dərhal skan, sonra yeni audiolar canlı növbəyə.\n\n"
                "<b>İstifadə (botda):</b>\n• <code>/depo add @kanal</code>\n• <code>/depo add https://t.me/+dəvət 500</code>", "bot")
    ub.add_help("depo", "/depo", "depo doldurucu paneli",
                "Doldurucunun tam paneli (botda): sürət, mənbələr, növbə.\n\n"
                "<b>İstifadə:</b> <code>/depo</code>, <code>/depo on</code>, <code>/depo off</code>\n"
                "<i>Paneldə:</i> 📦 Depo", "bot")
    ub.add_help("fix", "/fix", "depo təmizliyi",
                "Dublikatları birləşdirir, adlardakı tarix / zibili təmizləyir, YouTube adı ilə uyğun gəlməyənləri "
                "düzəldir.\n\n<b>İstifadə (botda):</b> <code>/fix</code>", "bot")

    # ───────────── 🧩 .menu / .help — userbot menyusu (userbot/core/plugin_menu.py, plugin_guide.py) ─────────────
    async def open_panel(event, kind="pm"):
        """kind: "pm" — userbot menyusu (.menu, .panel); "pg" — bələdçi (.help)."""
        text_fallback = lambda: plugin_style.apply_text(ub.state["plugin_menu_text" if kind == "pm" else "plugin_guide_text"]())
        if getattr(ub, "is_inline_disabled", None) and ub.is_inline_disabled(event.chat_id):
            from userbot.core.userbot_api import safe_edit
            await safe_edit(event, text_fallback())
            return

        client = event.client
        tok = os.urandom(4).hex()[:6]
        reply_uid = None
        if event.is_reply:
            try:
                reply = await event.get_reply_message()
                reply_uid = reply.sender_id if reply else None
            except Exception as _e:
                logger.debug("udulan xəta [userbot/core/host.py:139]: %r", _e)
        ub.menus[tok] = {"chat_id": event.chat_id, "reply_uid": reply_uid, "msg_id": None, "time": time.time()}
        if len(ub.menus) > 200:
            ub.menus.pop(next(iter(ub.menus)))
        try:
            results = await client.inline_query(await ub.bot_username(), f"ub:{kind}:{tok}",
                                                entity=await event.get_input_chat())
            if not results:
                raise RuntimeError("inline nəticə gəlmədi")
            sent = await results[0].click(event.chat_id, reply_to=event.reply_to_msg_id, hide_via=True)
            if sent is not None:
                ub.menus[tok]["msg_id"] = sent.id
            await event.delete()
        except Exception as e:
            from userbot.core.userbot_api import safe_edit
            logger.info(f".{kind} inline alınmadı: {e}")
            await safe_edit(event, text_fallback())

    async def on_menu(event):
        await open_panel(event, "pm")

    async def on_help(event):
        await open_panel(event, "pg")

    # komandaları reyestrə qoymaq əvəzinə birbaşa handler kimi əlavə edirik (panelləri host idarə edir)
    ub.commands.append({"name": "menu", "pattern": r"^\.(?:menu|panel)$", "outgoing": True, "fn": on_menu})
    ub.commands.append({"name": "help", "pattern": r"^\.help$", "outgoing": True, "fn": on_help})
    ub.add_help("menu", ".menu", "userbot menyusu",
                "Userbot-un idarə mərkəzi (botun /menu-sundan ayrıdır): 🧩 pluginləri söndür / yandır / yenilə / sil, "
                "komandaları ayrıca söndür, ⚡ panelləri düymə ilə aç, ▶️ əmrləri işlət, 📖 bələdçi.\n\n"
                "<b>İstifadə:</b> istənilən çatda <code>.menu</code> (<code>.panel</code> də eynidir)\n"
                "<i>Botlar vasitəsilə göndərmə qadağan olan çatda sadə mətn göstərilir.</i>", "acc")
    ub.add_help("help", ".help", "komanda bələdçisi",
                "Bütün userbot komandalarının izahı: qruplar üzrə, hər komandanın istifadəsi və ▶️ işlət düyməsi.\n\n"
                "<b>İstifadə:</b> <code>.help</code> (menyudan: 📖 Bələdçi)", "acc")

    userbot_restart.setup(ub)                       # .restart — host komandası (söndürülə bilməz)

    # ───────────── panel görünüşləri ─────────────
    def Bt(tok, text, data):
        return InlineKeyboardButton(text=text, callback_data=f"ubm:{tok}:{data}"[:64])

    def main_view(tok):
        return ub.state["plugin_menu_view"](tok)

    def cancel_ptask(tok):
        t = ub.ptasks.pop(tok, None)
        if t and not t.done():
            t.cancel()

    # ── bot tərəfi: inline sorğu və düymələr ──
    @dp.inline_query(F.query.startswith("ub:"))
    async def ub_inline(q: ag_types.InlineQuery):
        if not ub.is_creator(q.from_user.id):
            await q.answer([], cache_time=0, is_personal=True)
            return
        parts = q.query.split(":")
        kind = parts[1] if len(parts) > 1 else ""
        tok = parts[2] if len(parts) > 2 and parts[2].isalnum() else "0"

        if kind == "card":                                   # komanda kartı (.speedtest, .ff, .purge ...)
            out = ub.cards.get(tok)
            if out is None:
                await q.answer([], cache_time=0, is_personal=True)
                return
            card_text, card_kb = plugin_style.apply(out.text, out.kb())          # botun kod bloku görünüşü ilk mesajda da
            await q.answer([InlineQueryResultArticle(
                id=f"ubcard_{tok}", title=tr("🧩 Userbot", uid=q.from_user.id), description=tr("komanda nəticəsi", uid=q.from_user.id),
                input_message_content=InputTextMessageContent(message_text=card_text, parse_mode="HTML",
                                                              disable_web_page_preview=True),
                reply_markup=card_kb,
            )], cache_time=0, is_personal=True)
            return

        text, kb = plugin_style.apply(*ub.state["plugin_menu_view"](tok, "g" if kind == "pg" else "main"))
        await q.answer([InlineQueryResultArticle(
            id=f"ubmenu_{tok}", title=tr("🧩 Userbot menyusu", uid=q.from_user.id),
            description=tr("Pluginlər, funksiyalar, əmrlər, bələdçi", uid=q.from_user.id),
            input_message_content=InputTextMessageContent(message_text=text, parse_mode="HTML"),
            reply_markup=kb,
        )], cache_time=0, is_personal=True)

    # inline_message_id — BotFather-də /setinlinefeedback aktivdirsə buradan gəlir (yoxdursa Out özü alır)
    @dp.chosen_inline_result(F.result_id.startswith(("ubcard_", "ubmenu_")))
    async def ub_chosen(r: ag_types.ChosenInlineResult):
        if not ub.is_creator(r.from_user.id) or not r.inline_message_id:
            return
        if r.result_id.startswith("ubcard_"):
            out = ub.cards.get(r.result_id[len("ubcard_"):])
            if out is not None:
                out.set_iid(r.inline_message_id)
        else:
            m = ub.menus.get(r.result_id[len("ubmenu_"):])
            if m is not None:
                m["iid"] = r.inline_message_id

    # ── kart düymələri: ubc:<tok>:<action> ──
    @dp.callback_query(F.data.startswith("ubc:"))
    async def ub_card_cb(cb: ag_types.CallbackQuery):
        if not ub.is_creator(cb.from_user.id):
            await cb.answer(tr("⛔ Bu kart yalnız sahibi üçündür", uid=cb.from_user.id), show_alert=True)
            return
        _, tok, action = (cb.data.split(":", 2) + ["", ""])[:3]
        out = ub.cards.get(tok)
        if out is None:
            await cb.answer("Bu kart köhnədir — komandanı yenidən yaz", show_alert=True)
            return
        out.set_iid(cb.inline_message_id)
        if out._acquiring:                                  # userbot-un özü-klik (id almaq üçün)
            await cb.answer()
            return
        try:
            if action == "x":
                await cb.answer(tr("Bağlandı", uid=cb.from_user.id))
                await out.close()
            elif action == "s":
                if out.running():
                    out.task.cancel()
                    await cb.answer("⏹ Dayandırılır...")
                else:
                    await cb.answer("İşləyən iş yoxdur")
            elif action == "r":
                await cb.answer("🔄")
                if out.refresh:
                    await out.refresh(out)
                else:
                    await out.update()
            elif action in out.actions:
                await cb.answer()
                await out.actions[action](out, cb)
            else:
                await cb.answer()
        except Exception as e:
            logger.warning(f"kart ({action}) xətası: {e}", exc_info=True)
            try:
                await cb.answer(f"Xəta: {str(e)[:150]}", show_alert=True)
            except Exception as _e:
                logger.debug("udulan xəta [userbot/core/host.py:317]: %r", _e)

    @dp.callback_query(F.data.startswith("ubm:"))
    async def ub_panel_cb(cb: ag_types.CallbackQuery):
        if not ub.is_creator(cb.from_user.id):
            await cb.answer(tr("⛔ Bu panel yalnız sahibi üçündür", uid=cb.from_user.id), show_alert=True)
            return
        parts = cb.data.split(":")
        if len(parts) < 3:
            await cb.answer("Bu menyu köhnədir — yenidən .menu yaz", show_alert=True)
            return
        tok, screen, args = parts[1], parts[2], parts[3:]
        view = ":".join([screen] + args)
        if view != "sys:live":                       # canlı rejim yalnız özü davam etsin
            cancel_ptask(tok)
        plugin_menu.drop_panel(ub, tok)              # ▶️ Əmrlər-dən işləyən komanda dayansın
        ub.views[tok] = view
        if len(ub.views) > 300:
            ub.views.pop(next(iter(ub.views)))

        try:
            if screen == "main":
                await cb.answer()
                await ub.edit_panel(cb, *main_view(tok))
            elif screen in ("storage", "storage_refresh", "storage_clean", "storage_clean_records"):
                await cb.answer()
                from common.menu import storage_view
                note = ""
                if screen == "storage_clean":
                    from common.diagnostics import project_storage_snapshot
                    from common.utilities import cleanup_downloads
                    freed_b, freed_cnt = await cleanup_downloads()
                    await asyncio.to_thread(project_storage_snapshot, True)     # keşi yenilə
                    note = f"🧹 <b>Keş təmizləndi:</b> {freed_cnt} fayl ({freed_b / 1024 / 1024:.1f} MB)"
                elif screen == "storage_clean_records":
                    from common.diagnostics import project_storage_snapshot
                    from common.utilities import cleanup_records
                    freed_b, freed_cnt = await cleanup_records()
                    await asyncio.to_thread(project_storage_snapshot, True)
                    note = f"🎙 <b>Səs qeydləri təmizləndi:</b> {freed_cnt} fayl ({freed_b / 1024 / 1024:.1f} MB)" if freed_cnt else "ℹ️ Qeyd tapılmadı"
                t, _ = storage_view(note=note)
                k_rows = [
                    [Bt(tok, "🔄 Yenilə", "storage_refresh"), Bt(tok, "🧹 Keşi Təmizlə", "storage_clean")],
                    [Bt(tok, "🎙 Qeydləri Təmizlə", "storage_clean_records")],
                    [Bt(tok, "⬅️ Menyu", "main")]
                ]
                await ub.edit_panel(cb, t, InlineKeyboardMarkup(inline_keyboard=k_rows))
            elif screen in ("perf", "perf_refresh", "perf_scale", "perf_t_auto", "perf_t_l1", "perf_c_l1", "perf_t_rd", "perf_c_rd", "perf_t_sc", "perf_t_pdl",
                            "accel", "accel_refresh", "accel_t_auto", "accel_t_l1", "accel_c_l1", "accel_t_rd", "accel_c_rd", "accel_t_sc", "accel_t_pdl"):
                await cb.answer()
                from bot.core.audio_cache import is_l1_cache_enabled, set_l1_cache_enabled, clear_l1_cache
                from common.utilities import is_ram_disk_enabled, set_ram_disk_enabled, cleanup_ram_disk, is_accel_auto_enabled, set_accel_auto_enabled
                from bot.core.youtube_handler import is_smart_copy_enabled, set_smart_copy_enabled, is_parallel_dl_enabled, set_parallel_dl_enabled
                from bot.plugins.menu_plugin import perf_hub_view
                note = ""
                if screen == "perf_scale":
                    try:
                        from bot.core import cpu_pool as cp
                        on = cp.toggle_autoscale()
                        note = "🚀 Avto-miqyas: " + ("AÇIQ" if on else "BAĞLI")
                    except Exception as e:
                        note = f"Xəta: {e}"
                elif screen in ("perf_t_auto", "accel_t_auto"):
                    nv = not is_accel_auto_enabled()
                    set_accel_auto_enabled(nv)
                    note = f"⚙️ Rejim: {'🤖 Avto' if nv else '✋ Manual'}"
                else:
                    if (screen.startswith("perf_t_") or screen.startswith("accel_t_")) and is_accel_auto_enabled():
                        set_accel_auto_enabled(False)
                    if screen in ("perf_t_l1", "accel_t_l1"):
                        nv = not is_l1_cache_enabled()
                        set_l1_cache_enabled(nv)
                        note = f"🧠 L1 RAM Keşi: {'AÇIQ' if nv else 'BAĞLI'}"
                    elif screen in ("perf_c_l1", "accel_c_l1"):
                        clear_l1_cache()
                        note = "🧹 L1 RAM keşi təmizləndi"
                    elif screen in ("perf_t_rd", "accel_t_rd"):
                        nv = not is_ram_disk_enabled()
                        set_ram_disk_enabled(nv)
                        note = f"⚡ RAM Diski: {'AÇIQ' if nv else 'BAĞLI'}"
                    elif screen in ("perf_c_rd", "accel_c_rd"):
                        freed_b, freed_cnt = await cleanup_ram_disk()
                        note = f"🧹 {freed_cnt} müvəqqəti fayl ({freed_b / 1024 / 1024:.1f} MB) silindi"
                    elif screen in ("perf_t_sc", "accel_t_sc"):
                        nv = not is_smart_copy_enabled()
                        set_smart_copy_enabled(nv)
                        note = f"🎯 Ağıllı Stream Copy: {'AÇIQ' if nv else 'BAĞLI'}"
                    elif screen in ("perf_t_pdl", "accel_t_pdl"):
                        nv = not is_parallel_dl_enabled()
                        set_parallel_dl_enabled(nv)
                        note = f"🚀 Paralel Fraqmentlər: {'AÇIQ' if nv else 'BAĞLI'}"
                t, _ = perf_hub_view(note=note)
                auto_on = is_accel_auto_enabled()
                l1_on = is_l1_cache_enabled()
                rd_on = is_ram_disk_enabled()
                sc_on = is_smart_copy_enabled()
                pdl_on = is_parallel_dl_enabled()
                k_rows = [
                    [Bt(tok, f"⚙️ Rejim: {'🤖 Avto' if auto_on else '✋ Manual'}", "perf_t_auto")],
                ]
                if auto_on:
                    k_rows.append([
                        Bt(tok, "🧹 L1 Təmizlə", "perf_c_l1"),
                        Bt(tok, "🧹 Disk Təmizlə", "perf_c_rd")
                    ])
                else:
                    k_rows.extend([
                        [Bt(tok, f"🧠 L1: {'🟢' if l1_on else '🔴'}", "perf_t_l1"), Bt(tok, "🧹 L1 Təmizlə", "perf_c_l1")],
                        [Bt(tok, f"⚡ Disk: {'🟢' if rd_on else '🔴'}", "perf_t_rd"), Bt(tok, "🧹 Disk Təmizlə", "perf_c_rd")],
                        [Bt(tok, f"🎯 Copy: {'🟢' if sc_on else '🔴'}", "perf_t_sc"), Bt(tok, f"🚀 Paralel: {'🟢' if pdl_on else '🔴'}", "perf_t_pdl")],
                    ])
                k_rows.extend([
                    [Bt(tok, "🚀 Avto-miqyas", "perf_scale"), Bt(tok, "🔄 Yenilə", "perf_refresh")],
                    [Bt(tok, "⬅️ Menyu", "main")]
                ])
                await ub.edit_panel(cb, t, InlineKeyboardMarkup(inline_keyboard=k_rows))
            elif screen == "help":                       # köhnə düymələr → yeni bələdçi
                await cb.answer()
                await ub.edit_panel(cb, *ub.state["plugin_menu_view"](tok, "g"))
            elif screen == "close":
                await cb.answer(tr("Bağlandı", uid=cb.from_user.id))
                cancel_ptask(tok)
                m = ub.menus.pop(tok, None)
                deleted = False
                if m and m.get("msg_id") and ub.client:
                    try:
                        await ub.client.delete_messages(m["chat_id"], [m["msg_id"]])
                        deleted = True
                    except Exception as e:
                        logger.debug(f"panel silinmədi: {e}")
                if not deleted:
                    await ub.edit_panel(cb, tr("✖️ <i>Panel bağlandı</i>", uid=cb.from_user.id), None)
            elif screen in ub.sections:
                sec = ub.sections[screen]
                from userbot.core.userbot_api import SectionCtx
                sctx = SectionCtx(ub, screen, tok, cb, args[0] if args else "", args[1:] if len(args) > 1 else [])
                handle = sec.get("handle")
                if args and handle:
                    await handle(sctx)
                else:
                    await cb.answer()
                    text, rows = await sec["render"](sctx)
                    await sctx.show(text, rows)
            else:
                await cb.answer()
                await ub.edit_panel(cb, *main_view(tok))
        except Exception as e:
            logger.warning(f"ub panel ({view}) xətası: {e}", exc_info=True)
            try:
                await cb.answer(f"Xəta: {str(e)[:150]}", show_alert=True)
            except Exception as _e:
                logger.debug("udulan xəta [userbot/core/host.py:380]: %r", _e)

    # host-un özünün köməkçiləri pluginlərə lazım ola bilər
    ub.state["main_view"] = main_view
    ub.state["Bt"] = Bt
    ub.state["plugins_dir"] = PLUGINS_DIR
    ub.state["load_plugin_file"] = lambda path, attach=True: load_plugin_file(ub, path, attach)
    plugin_menu.setup(ub, dp)

    # ───────────── userbot-a qoşulma ─────────────
    async def attach():
        for _ in range(120):
            client = getattr(context, "telethon_client", None)
            if client:
                break
            await asyncio.sleep(1)
        else:
            logger.warning("⚠️ userbot host: telethon_client tapılmadı (sessiya yoxdur? 📱 Userbotlar → 🔑 ilə qoş)")
            return
        ub.client = client
        ub.state.setdefault("attach_time", time.time())
        ub.attach_pending()
        cmds = " ".join("." + c["name"] for c in ub.commands)
        logger.info(f"✅ Userbot komandaları qoşuldu: {cmds}")
        asyncio.create_task(userbot_restart.announce(ub))     # .restart-dan sonra "yenidən başladı" mesajı

    try:
        ub.state["attach_task"] = asyncio.create_task(attach())
    except RuntimeError:
        logger.error("userbot host: event loop işləmir")

    logger.info("✅ Userbot host yükləndi (.menu paneli + inline kartlar)")


async def teardown(context):
    ub = getattr(context, "userbot", None)
    if not ub:
        return
    t = ub.state.get("attach_task")
    if t and not t.done():
        t.cancel()
    for kinds in ub.tasks.values():
        for task in kinds.values():
            if task and not task.done():
                task.cancel()
    for task in ub.ptasks.values():
        if task and not task.done():
            task.cancel()
    for out in list(ub.cards.values()):
        if out.running():
            out.task.cancel()
    if ub.client:
        for fn, _ in ub._handlers:
            try:
                ub.client.remove_event_handler(fn)
            except Exception as _e:
                logger.debug("udulan xəta [userbot/core/host.py:434]: %r", _e)
