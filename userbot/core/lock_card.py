"""
🔒 .locks — inline kilid paneli (Out kartı): çatı seç, növləri düymə ilə bağla / aç, ümumi ayarları idarə et.

Görünüşlər (ctx["view"]):
  chat      — seçilmiş çatın kilidləri: 🖼 Media / 💬 Digər tabları, növ düymələri, hazır dəstlər, fasilə
  chats     — çat seçimi: 📍 bu çat · 🔒 kilidlilər · 👥 qruplar · 👤 şəxsilər (söhbət siyahısından, səhifələnir)
  users     — çatda şəxs üzrə kilidlər (🗑 ilə sil; əlavə etmək: .lock <növ> @user və ya reply)
  settings  — ümumi: kilidlər açıq / silmə sırası / admin istisnası / botlar / bildiriş + hamısını sil
Düymə əməliyyatları `out.on(...)` ilə tənbəl qeydə alınır (callback: ubc:<tok>:<əməl>, ≤ 64 bayt).
"""
import logging
from html import escape

from userbot.core.lock_core import KINDS, MEDIA, can_delete, label
from userbot.core.userbot_api import Out, box_text, plain_name

logger = logging.getLogger(__name__)

OTHER = tuple(k for k in KINDS if k not in MEDIA)
PER_PAGE = 8
DIALOG_LIMIT = 300
FILTERS = {"here": "📍 Bu çat", "locked": "🔒 Kilidlilər", "group": "👥 Qruplar", "user": "👤 Şəxsilər"}


def _short(text: str, n: int = 22) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


async def chat_info(client, chat_id: int) -> dict:
    """{title, private, can} — çatın adı, şəxsidirmi, orada başqasının mesajını silə bilərsənmi."""
    try:
        ent = await client.get_entity(chat_id)
    except Exception as e:
        logger.debug(f"lock kartı: çat tapılmadı ({chat_id}): {e}")
        return {"title": f"ID {chat_id}", "private": chat_id > 0, "can": chat_id > 0}
    private = type(ent).__name__ == "User"
    try:
        can = await can_delete(client, ent)
    except Exception:
        can = False
    return {"title": _short(plain_name(ent), 40), "private": private, "can": can}


async def load_dialogs(client) -> list:
    """[{id, title, kind}] — qruplar və şəxsi söhbətlər (kanallar və botlar istisna)."""
    out = []
    try:
        async for d in client.iter_dialogs(limit=DIALOG_LIMIT):
            ent = getattr(d, "entity", None)
            if getattr(ent, "bot", False) or (getattr(d, "is_channel", False) and not getattr(d, "is_group", False)):
                continue
            kind = "group" if getattr(d, "is_group", False) else "user" if getattr(d, "is_user", False) else None
            if kind:
                out.append({"id": d.id, "title": d.name or str(d.id), "kind": kind})
    except Exception as e:
        logger.info(f"lock kartı: söhbət siyahısı alınmadı: {e}")
    return out


def card_text(ctx: dict, state) -> str:
    chat = state.chats.get(str(ctx["chat"])) or {}
    lines = [f"🔒 <b>Kilidlər</b> — {escape(ctx['title'])}  <i>({'şəxsi' if ctx['private'] else 'qrup'})</i>"]
    if not state.cfg["enabled"]:
        lines.append("🔴 <b>Kilidlər ümumilikdə söndürülüb</b> (⚙️ Ayarlar)")
    if chat.get("paused"):
        lines.append("⏸ <b>Bu çatda fasilədədir</b> — heç nə silinmir")
    lines.append("Bağlı: " + (label(chat["all"]) if chat.get("all") else "<i>heç nə</i>"))
    if chat.get("users"):
        lines.append(f"👥 Şəxs üzrə: {len(chat['users'])} nəfər")
    if not ctx["private"] and not ctx["can"]:
        lines.append("⚠️ <i>Bu qrupda başqasının mesajını silmək icazən yoxdur — kilid işləməz.</i>")
    lines.append(f"🗑 Silinib (bu açılışdan): {state.deleted}")
    if ctx.get("note"):
        lines += ["", ctx["note"]]
    return "\n".join(lines)


def attach(ub, event, state, out: Out, ctx: dict):
    """Kartın bütün əməlləri. Hər biri ctx-i dəyişib show() ilə yenidən çəkir."""

    def act(name, fn):
        if name not in out.actions:
            out.on(name)(fn)
        return name

    def b(text, name, fn):
        return out.btn(text, act(name, fn))

    async def show(o=None, note=""):
        ctx["note"] = note
        text, rows = await render()
        await out.update(text, rows=rows)

    async def goto(view, **kw):
        ctx["view"] = view
        ctx.update(kw)
        await show()

    # ───────── chat ─────────
    async def kind_toggle(o, cb, kind):
        if not ctx["private"] and not ctx["can"] and kind not in (state.chats.get(str(ctx["chat"])) or {}).get("all", []):
            return await show(note="⛔ Bu qrupda silmək icazən yoxdur — kilid qoymadım.")
        now = state.toggle(ctx["chat"], kind)
        await show(note=f"{'🔒 Bağlandı' if now else '🔓 Açıldı'}: {KINDS[kind][0]} {KINDS[kind][1]}")

    def toggle_fn(kind):
        async def _(o, cb):
            await kind_toggle(o, cb, kind)
        return _

    async def preset(kinds, text):
        if kinds and not ctx["private"] and not ctx["can"]:
            return await show(note="⛔ Bu qrupda silmək icazən yoxdur — kilid qoymadım.")
        state.set_all(ctx["chat"], kinds)
        await show(note=text)

    async def p_media(o, cb):
        await preset(MEDIA, "🔒 Bütün media bağlandı")

    async def p_all(o, cb):
        await preset(tuple(KINDS), "🔒 Hər şey bağlandı (mətn də)")

    async def p_none(o, cb):
        await preset((), "🔓 Ümumi kilid açıldı")

    def tab_fn(which):
        async def _(o, cb):
            ctx["tab"] = which
            await show()
        return _

    async def pause(o, cb):
        chat = state.chats.get(str(ctx["chat"])) or {}
        state.pause(ctx["chat"], not chat.get("paused"))
        await show(note="⏸ Fasilə verildi" if not chat.get("paused") else "▶️ Davam edir")

    def chat_rows():
        chat = state.chats.get(str(ctx["chat"])) or {}
        locked = set(chat.get("all") or [])
        t = ctx.get("tab", "m")
        rows = [[b(("✅ " if t == "m" else "") + "🖼 Media", "tab-m", tab_fn("m")),
                 b(("✅ " if t == "o" else "") + "💬 Digər", "tab-o", tab_fn("o"))]]
        kinds = MEDIA if t == "m" else OTHER
        btns = [b(f"{'🔒' if k in locked else '▫️'} {KINDS[k][0]} {KINDS[k][1]}", f"k:{k}", toggle_fn(k)) for k in kinds]
        rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
        rows.append([b("🖼 Media hamısı", "pm", p_media), b("🔒 Hamısı", "pa", p_all)])
        rows.append([b("🔓 Hamısını aç", "pn", p_none), b("📍 Çat seç", "chats", open_chats)])
        extra = [b("⚙️ Ayarlar", "settings", lambda o, cb: goto("settings"))]
        if chat.get("users"):
            extra.insert(0, b(f"👥 Şəxslər ({len(chat['users'])})", "users", lambda o, cb: goto("users")))
        if chat:
            extra.insert(0, b("▶️ Davam" if chat.get("paused") else "⏸ Fasilə", "pause", pause))
        rows += [extra[i:i + 2] for i in range(0, len(extra), 2)]
        return rows

    # ───────── chats ─────────
    def pick_fn(i):
        async def _(o, cb):          # düymə səhifəyə / süzgəcə görə dəyişir → həmin anki siyahıdan oxunur
            items = ctx.get("page_items") or []
            if i >= len(items):
                return await show(note="⚠️ Siyahı dəyişib — yenidən seç")
            ctx.update(chat=items[i]["id"], tab="m", **await chat_info(out.client, items[i]["id"]))
            await goto("chat")
        return _

    async def open_chats(o, cb):
        await goto("chats", page=0, filter=ctx.get("filter") or ("locked" if state.chats else "group"))

    def set_filter(name):
        async def _(o, cb):
            if name == "here":
                ctx.update(chat=ctx["home"], tab="m", **await chat_info(out.client, ctx["home"]))
                return await goto("chat")
            await goto("chats", filter=name, page=0)
        return _

    def page_fn(delta):
        async def _(o, cb):
            ctx["page"] = max(0, ctx.get("page", 0) + delta)
            await show()
        return _

    def listing():
        flt = ctx.get("filter", "locked")
        known = {d["id"]: d for d in ctx["dialogs"]}
        if flt == "locked":
            items = [known.get(c) or {"id": c, "title": f"ID {c}", "kind": "group"} for c in state.locked_chats()]
        else:
            items = [d for d in ctx["dialogs"] if d["kind"] == flt]
        return items

    def chats_view():
        flt = ctx.get("filter", "locked")
        items = listing()
        pages = max(1, -(-len(items) // PER_PAGE))
        page = ctx["page"] = max(0, min(ctx.get("page", 0), pages - 1))
        text = (f"📍 <b>Çat seç</b> — {FILTERS[flt]} ({len(items)})\n"
                "<i>Çata bas — həmin çatın kilidlərini idarə et. 🔒 — kilidi olan çatlar.</i>")
        rows = [[b(("✅ " if flt == k else "") + v, f"f:{k}", set_filter(k)) for k, v in list(FILTERS.items())[i:i + 2]]
                for i in range(0, len(FILTERS), 2)]
        ctx["page_items"] = items[page * PER_PAGE:(page + 1) * PER_PAGE]
        btns = [b(f"{'🔒 ' if str(d['id']) in state.chats else ''}{_short(d['title'], 20)}", f"c:{i}", pick_fn(i))
                for i, d in enumerate(ctx["page_items"])]
        rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
        if pages > 1:
            rows.append([b("◀️", "pg-", page_fn(-1)), b(f"{page + 1}/{pages}", "pg0", page_fn(0)), b("▶️", "pg+", page_fn(1))])
        rows.append([b("⬅️ Çata qayıt", "back", lambda o, cb: goto("chat"))])
        return text, rows

    # ───────── users ─────────
    def user_del_fn(uid):
        async def _(o, cb):
            state.remove(ctx["chat"], None, uid=int(uid))
            await goto("users" if (state.chats.get(str(ctx["chat"])) or {}).get("users") else "chat")
        return _

    async def users_view():
        chat = state.chats.get(str(ctx["chat"])) or {}
        lines = [f"👥 <b>Şəxs üzrə kilidlər</b> — {escape(ctx['title'])}", ""]
        rows = []
        for uid, kinds in list(chat.get("users", {}).items())[:PER_PAGE]:
            name = ctx["names"].get(uid)
            if name is None:
                try:
                    name = ctx["names"][uid] = _short(plain_name(await out.client.get_entity(int(uid))), 24)
                except Exception:
                    name = ctx["names"][uid] = str(uid)
            lines.append(f"• {escape(name)}: {label(kinds)}")
            rows.append([b(f"🗑 {_short(name, 18)}", f"ud:{uid}", user_del_fn(uid))])
        lines += ["", "<i>🗑 şəxsin bütün kilidlərini açır. Yeni: <code>.lock link @user</code> və ya mesajına reply.</i>"]
        rows.append([b("⬅️ Çata qayıt", "back", lambda o, cb: goto("chat"))])
        return "\n".join(lines), rows

    # ───────── settings ─────────
    def cfg_fn(key):
        async def _(o, cb):
            state.set_cfg(key, not state.cfg[key])
            await show(note="✅ Ayar dəyişdi")
        return _

    async def notice(o, cb):
        ub.db.kv_set("cmdopt", "lock:notice", "off" if notice_on() else "on")
        await show(note="✅ Ayar dəyişdi")

    def notice_on():
        try:
            return ub.db.kv_get("cmdopt", "lock:notice", "off") == "on"
        except Exception:
            return False

    async def wipe_ask(o, cb):
        await goto("wipe")

    async def wipe_yes(o, cb):
        n = state.clear_everything()
        ctx.update(view="chat")
        await show(note=f"🧹 {n} çatın bütün kilidləri silindi")

    def settings_view():
        c = state.cfg
        on = lambda v: "🟢" if v else "🔴"  # noqa: E731
        text = "\n".join([
            "⚙️ <b>Kilid ayarları</b> (bütün çatlar üçün ortaq)", "",
            f"{on(c['enabled'])} Kilidlər: {'işləyir' if c['enabled'] else 'ümumilikdə söndürülüb'}",
            f"🗑 Şəxsi çatda silmə: <b>{'hər iki tərəfdən' if c['revoke'] else 'yalnız səndən'}</b>",
            f"{on(c['admins'])} Qrup kilidi adminlərə tətbiq olunmur: {'bəli' if c['admins'] else 'xeyr, adminlər də silinir'}",
            f"{on(c['bots'])} Botların mesajı da silinir: {'bəli' if c['bots'] else 'xeyr (toxunulmaz)'}",
            f"{on(notice_on())} Silinəndə qısa bildiriş: {'bəli' if notice_on() else 'xeyr (səssiz)'}",
            "", f"Kilidli çat: {len(state.chats)} · silinib: {state.deleted}",
            "<i>Özün, Telegram xidməti (777000) və — qrup kilidində — adminlər (ayara görə) həmişə qorunur.</i>"])
        rows = [[b(f"{on(c['enabled'])} Kilidlər", "cfg-enabled", cfg_fn("enabled")),
                 b("🗑 Hər iki tərəfdən" if c["revoke"] else "🗑 Yalnız səndən", "cfg-revoke", cfg_fn("revoke"))],
                [b(f"{on(c['admins'])} Admin istisnası", "cfg-admins", cfg_fn("admins")),
                 b(f"{on(c['bots'])} Botlar", "cfg-bots", cfg_fn("bots"))],
                [b(f"{on(notice_on())} Bildiriş", "cfg-notice", notice), b("🧹 Hamısını sil", "wipe", wipe_ask)],
                [b("⬅️ Çata qayıt", "back", lambda o, cb: goto("chat"))]]
        return text, rows

    def wipe_view():
        text = (f"🧹 <b>Bütün kilidlər silinsin?</b>\n\n{len(state.chats)} çatın kilidi (şəxs üzrə olanlar da) silinəcək. "
                "Ümumi ayarlar qalır. Geri qaytarmaq olmur.")
        return text, [[b("✅ Bəli, hamısını sil", "wipe-yes", wipe_yes), b("✖️ İmtina", "settings", lambda o, cb: goto("settings"))]]

    # ───────── render ─────────
    async def render():
        view = ctx["view"]
        if view == "chat":
            return card_text(ctx, state), chat_rows()
        if view == "chats" and ctx.get("dialogs") is None:
            ctx["dialogs"] = await load_dialogs(out.client)
        text, rows = {"chats": chats_view, "settings": settings_view, "wipe": wipe_view}.get(view, users_view)() \
            if view != "users" else await users_view()
        return (text + (f"\n\n{ctx['note']}" if ctx.get("note") else ""), rows)

    out.render = render
    out.show = show

    async def refresh(o):
        ctx["dialogs"] = None
        await show()
    out.refresh = refresh
    return render


async def open_card(ub, event, state, chat_id=None):
    """`.locks` — kartı açır (inline mümkün deyilsə mətn siyahısı)."""
    from userbot.core.lock_core import status_text
    cid = chat_id if chat_id is not None else event.chat_id
    out = Out(ub, event)
    info = await chat_info(event.client, cid)
    ctx = {"view": "chat", "tab": "m", "chat": cid, "home": cid, "dialogs": None, "names": {}, "note": "", "page": 0, **info}
    render = attach(ub, event, state, out, ctx)
    text, rows = await render()
    out.ctx = ctx
    await out.open(text, rows)
    if out.mode == "text":                 # inline düymə yoxdur — sadə mətn siyahısı (şəxslərin adları ilə)
        names = {}
        for uid in (state.chats.get(str(cid)) or {}).get("users", {}):
            try:
                names[uid] = plain_name(await event.client.get_entity(int(uid)))
            except Exception as e:
                logger.debug(f"lock kartı: ad alınmadı ({uid}): {e}")
        await out.update(box_text(status_text(state, cid, names)), rows=[])
    return out
