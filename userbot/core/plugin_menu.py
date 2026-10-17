"""
🧩 Userbot menyusu (.menu) — userbot-un özünün idarə mərkəzi. Botun /menu-sundan ayrıdır.

  🧩 Pluginlər     — 🟢 yandır / 🔴 söndür, 🔄 yenilə, 🗑 sil, 🧰 ayrı-ayrı komandaları söndür, 📊 çağırış sayğacı;
  ⚡ Funksiyalar   — userbot panelləri düymə ilə (status, depo, səs & audit, yedək, performans, yaddaş, görünüş);
  ▶️ Əmrlər        — zərərsiz komandalar düymə ilə işləyir (alive, fastfetch, speedtest ...);
  📖 Bələdçi       — bütün komandaların izahı (userbot/core/plugin_guide.py), .help də bura açır.

Söndürülmüş pluginlər / komandalar userbot db-də (pluginmgr/disabled, pluginmgr/cmd_off) saxlanır; restartda da qalır.
`system` pluginini və bərpa komandalarını (.menu .help .plugins .reload .install .uninstall .bmenu) söndürmək olmaz.
"""
import logging
import os
import re
import time
from html import escape

from aiogram import F

from bot.core import panel as ui
from userbot.core import plugin_cmds as cmds
from userbot.core import plugin_guide as guide
from userbot.core import plugin_style as pstyle

logger = logging.getLogger(__name__)

PROTECTED = {"system"}
PROTECTED_CMDS = {"menu", "help", "panel", "plugins", "reload", "install", "uninstall", "bmenu", "restart"}
PER_PAGE = 8
CMD_PER_PAGE = 10
AWAIT_TTL = 180            # ✍️ arqument gözləməsi — saniyə
NS, KEY = "pluginmgr", "disabled"
CMD_OFF = "cmd_off"
QUIET_KEY = "quiet"        # cavab mesajı gizlədilən komandalar
QUIET_LATE = {"inline", "info", "audit_report"}   # komanda mesajına reply atır — mesaj iş bitəndə silinir, qalanlarda dərhal
USAGE = "usage"            # {plugin: {"n": cəmi, "last": ts, "cmds": {komanda: say}}, "_since": ts}
# ⚡ Funksiyalar: host-un daxili ekranları (plugin bölmələri ub.sections-dan avtomatik gəlir)
BUILTIN_SCREENS = (("⚙️ Performans", "perf"), ("🗂 Yaddaş & Keş", "storage"))


# ───────────── vəziyyət ─────────────
def disabled(ub) -> list:
    try:
        return [str(x) for x in (ub.db.kv_get(NS, KEY, []) or [])]
    except Exception:
        return []


def _set_disabled(ub, names):
    ub.db.kv_set(NS, KEY, sorted(set(names)))


def cmd_off(ub) -> set:
    try:
        return {str(x) for x in (ub.db.kv_get(NS, CMD_OFF, []) or [])} - PROTECTED_CMDS
    except Exception:
        return set()


def set_cmd(ub, name: str, on: bool) -> tuple:
    if name in PROTECTED_CMDS:
        return False, f".{name} söndürülə bilməz — menyunun özü / bərpa komandasıdır"
    if not any(c["name"] == name for c in ub.commands):
        return False, f".{name} tapılmadı"
    off = cmd_off(ub)
    off = off - {name} if on else off | {name}
    ub.db.kv_set(NS, CMD_OFF, sorted(off))
    return True, f"{'🟢' if on else '🔴'} .{name} {'yandırıldı' if on else 'söndürüldü'}"


# ───────────── ⚙️ komanda ayarları (plugin qeydiyyatı: ub.state["cmd_options"][komanda] = [seçim, ...]) ─────────────
def cmd_options(ub, name: str) -> list:
    return list((ub.state.get("cmd_options") or {}).get(name) or [])


def opt_get(ub, name: str, key: str):
    spec = next((o for o in cmd_options(ub, name) if o["key"] == key), None)
    default = spec["default"] if spec else None
    try:
        v = ub.db.kv_get("cmdopt", f"{name}:{key}", default)
    except Exception:
        return default
    return v if spec is None or v in [c for c, _ in spec["choices"]] else default


def opt_set(ub, name: str, key: str, value: str) -> tuple:
    spec = next((o for o in cmd_options(ub, name) if o["key"] == key), None)
    if spec is None or value not in [c for c, _ in spec["choices"]]:
        return False, "❌ Naməlum ayar"
    ub.db.kv_set("cmdopt", f"{name}:{key}", value)
    label = dict(spec["choices"])[value]
    return True, f"⚙️ .{name}: {spec['label']} → {label}"


def options_view(ub, tok: str, name: str, where: str = "q", note: str = ""):
    """⚙️ Komandanın ayarları: hər seçim üçün variantlar (✅ cari)."""
    specs = cmd_options(ub, name)
    back = f"{'qc' if where == 'q' else 'gc'}:{name}"
    if not specs:
        return hub_view(ub, tok, snapshot(ub), note)
    lines = [f"<i>🏠 › 🧩 Userbot › Əmrlər › {escape(name)} › Ayarlar</i>", f"⚙️ <b>.{escape(name)} — ayarlar</b>",
             "━━━━━━━━━━━━━━━━━━"]
    rows = []
    for o in specs:
        cur = opt_get(ub, name, o["key"])
        lines += [f"{o['label']}: <b>{escape(dict(o['choices'])[cur])}</b>"] + ([f"<i>{escape(o['note'])}</i>"] if o.get("note") else [])
        rows += [[ui.choice(label, value == cur, _cb(tok, f"sv:{where}:{name}:{o['key']}:{value}"))] for value, label in o["choices"]]
    if note:
        lines += ["", note]
    rows.append(_nav(tok, back, f".{name}", refresh=f"so:{where}:{name}"))
    return "\n".join(lines), ui.kb(*rows)


def quiet_set(ub) -> set:
    try:
        return {str(x) for x in (ub.db.kv_get(NS, QUIET_KEY, []) or [])}
    except Exception:
        return set()


def set_quiet(ub, name: str, hide: bool) -> tuple:
    """💬 Cavab mesajı: komanda işləyəndə çatda status / nəticə mətni göstərilsin (hide=False) ya yox."""
    c = next((c for c in ub.commands if c["name"] == name), None)
    if c is None or not c.get("owner"):
        return False, f".{name} üçün cavab gizlədilmir (menyunun özüdür)"
    q = quiet_set(ub)
    ub.db.kv_set(NS, QUIET_KEY, sorted(q | {name} if hide else q - {name}))
    return True, f".{name}: cavab mesajı {'🔴 gizlədilib' if hide else '🟢 göstərilir'}"


def quiet_start(ub, name, event) -> str:
    """Komanda başlayanda: cavabı gizlədilibsə mesaj işarələnir (safe_edit / kart susur) və "early" (dərhal sil) ya da
    "late" (iş bitəndə sil — komanda həmin mesaja reply atırsa) qaytarılır. Menyudan işləyəndə tətbiq olunmur → ""."""
    from userbot.core import userbot_api as api
    key = api.quiet_key(event)
    if key is None or getattr(event, "panel", None) is not None or name not in quiet_set(ub):
        return ""
    api.QUIET[key] = time.time()
    if len(api.QUIET) > 500:
        for k in list(api.QUIET)[:200]:
            api.QUIET.pop(k, None)
    return "late" if name in QUIET_LATE else "early"


# ───────────── çağırış sayğacı + komanda qapısı ─────────────
def usage(ub) -> dict:
    try:
        return ub.db.kv_get(NS, USAGE, {}) or {}
    except Exception:
        return {}


def note_use(ub, owner, name):
    """Plugin / komanda üzrə sayır, son vaxtı yazır."""
    if not owner:
        return
    data = usage(ub)
    data.setdefault("_since", int(time.time()))
    rec = data.setdefault(owner, {"n": 0, "last": 0, "cmds": {}})
    rec["n"] += 1
    rec["last"] = int(time.time())
    rec["cmds"][name] = rec["cmds"].get(name, 0) + 1
    ub.db.kv_set(NS, USAGE, data)


def reset_usage(ub, name=None):
    data = usage(ub)
    if name is None:
        data = {}
    else:
        data.pop(name, None)
    ub.db.kv_set(NS, USAGE, data)


def gate(ub, owner, name, event) -> bool:
    """UB.command sarğısı: komanda işləməlimi? Söndürülübsə və ya eyni mesaj ikinci dəfə gəlibsə — yox.
    (Menyu düyməsi hadisəni özü yaradır — PanelEvent dublikat sayılmır, hər basış işləyir.)"""
    if name in cmd_off(ub):
        return False
    cid, mid = getattr(event, "chat_id", None), getattr(event, "id", None)
    if isinstance(cid, int) and isinstance(mid, int) and mid and getattr(event, "panel", None) is None:
        ran = ub.state.setdefault("_ran", {})
        key = (cid, mid, name)
        if key in ran:
            return False
        ran[key] = time.time()
        if len(ran) > 300:
            for k in list(ran)[:100]:
                ran.pop(k, None)
    note_use(ub, owner, name)
    return True


# ───────────── pluginlər: fayl və yüklənmə vəziyyəti ─────────────
def plugins_dir(ub) -> str:
    return ub.state.get("plugins_dir") or ""


def disk_plugins(ub) -> list:
    d = plugins_dir(ub)
    names = set(ub.plugin_files)
    if d and os.path.isdir(d):
        names |= {fn[:-3] for fn in os.listdir(d) if fn.endswith(".py") and not fn.startswith("_")}
    return sorted(names)


def is_on(ub, name: str) -> bool:
    return name in ub.plugin_files


def path_of(ub, name: str) -> str:
    return ub.plugin_files.get(name) or os.path.join(plugins_dir(ub), f"{name}.py")


def file_lines(ub, name: str) -> int:
    try:
        with open(path_of(ub, name), "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def snapshot(ub) -> list:
    """[{name, on, protected, cmds, secs, lines, uses, since}] — görünüşlər yalnız bunu formatlayır."""
    out, used = [], usage(ub)
    for name in disk_plugins(ub):
        info = ub.plugin_summary(name) if is_on(ub, name) else {"commands": [], "sections": [], "raw": 0}
        out.append({"name": name, "on": is_on(ub, name), "protected": name in PROTECTED,
                    "cmds": info["commands"], "secs": info["sections"], "raw": info["raw"],
                    "lines": file_lines(ub, name), "uses": (used.get(name) or {"n": 0, "last": 0, "cmds": {}}),
                    "since": used.get("_since", 0)})
    return out


# ───────────── əməliyyatlar ─────────────
def set_enabled(ub, name: str, on: bool) -> tuple:
    """→ (ok, mesaj). Söndürmək: handlerləri götürür + db-yə yazır. Yandırmaq: faylı yükləyir."""
    if name not in disk_plugins(ub):
        return False, f"{name} tapılmadı"
    if not on:
        if name in PROTECTED:
            return False, f"{name} söndürülə bilməz — .install / .reload / .menu orada"
        _set_disabled(ub, disabled(ub) + [name])
        if is_on(ub, name):
            ub.remove_plugin(name)
            ub.plugin_files.pop(name, None)
        return True, f"🔴 {name} söndürüldü"
    _set_disabled(ub, [n for n in disabled(ub) if n != name])
    if not is_on(ub, name):
        load = ub.state.get("load_plugin_file")
        if not load:
            return False, "yükləyici hazır deyil"
        try:
            load(path_of(ub, name), True)
        except Exception as e:
            _set_disabled(ub, disabled(ub) + [name])
            return False, f"yüklənmədi: {str(e)[:120]}"
    return True, f"🟢 {name} yandırıldı"


def enable_all(ub) -> tuple:
    off = [n for n in disk_plugins(ub) if not is_on(ub, n)]
    bad = [f"{n}: {msg}" for n in off for ok, msg in [set_enabled(ub, n, True)] if not ok]
    return not bad, f"🟢 {len(off) - len(bad)} plugin yandırıldı" + (f" · ⚠️ {len(bad)} xəta: {bad[0]}" if bad else "")


def reload_one(ub, name: str) -> tuple:
    load = ub.state.get("load_plugin_file")
    if not load or not os.path.isfile(path_of(ub, name)):
        return False, f"{name} tapılmadı"
    if not is_on(ub, name):
        return False, f"{name} söndürülüb — əvvəl yandır"
    try:
        load(path_of(ub, name), True)
        return True, f"🔄 {name} yeniləndi"
    except Exception as e:
        return False, f"yenilənmədi: {str(e)[:120]}"


def reload_all(ub) -> tuple:
    load = ub.state.get("load_plugin_file")
    if not load:
        return False, "yükləyici hazır deyil"
    done, bad = 0, []
    for name, path in list(ub.plugin_files.items()):
        try:
            load(path, False)
            done += 1
        except Exception as e:
            bad.append(f"{name}: {str(e)[:60]}")
    ub.attach_pending()
    return not bad, f"🔄 {done} plugin yeniləndi" + (f" · ⚠️ {len(bad)} xəta: {bad[0]}" if bad else "")


def uninstall(ub, name: str) -> tuple:
    if name in PROTECTED:
        return False, f"{name} silinə bilməz"
    path = path_of(ub, name)
    if not os.path.isfile(path):
        return False, f"{name} tapılmadı"
    try:
        ub.remove_plugin(name)
        ub.plugin_files.pop(name, None)
        os.remove(path)
        _set_disabled(ub, [n for n in disabled(ub) if n != name])
        return True, f"🗑 {name}.py silindi"
    except OSError as e:
        return False, f"silinmədi: {str(e)[:120]}"


class PanelEvent:
    """Telethon hadisəsinin əvəzi: komanda çata mesaj YAZMIR — nəticəni menyunun öz mesajında göstərir.

    `safe_edit(event, ...)`, `event.edit/respond/reply` və `ub.out(event, ...)` / `Out(ub, event)` (kart) — hamısı
    menyu mesajını redaktə edir. ✍️ arqumentlə işləyəndə `text` = ".ad arqument", `reply_id` = hədəf mesaj.
    Menyu başqa səhifəyə keçəndə `dead` olur, köhnə iş üstündən yazmır."""

    def __init__(self, ub, cb, tok, name, pattern, text=None, reply_id=None):
        self.panel = self
        self.ub, self.cb, self.tok, self.name = ub, cb, tok, name
        self.iid = cb if isinstance(cb, str) else getattr(cb, "inline_message_id", None)
        self.chat_id = (ub.menus.get(tok) or {}).get("chat_id") or ub.creator_id    # .id / .filters / .mutelist üçün cari çat
        self.client, self.sender_id = ub.client, ub.creator_id
        self.id, self.reply_to_msg_id, self.is_reply = None, reply_id, bool(reply_id)
        self.out, self.message = True, self
        self.text = self.raw_text = text or f".{name}"
        self.pattern_match = re.compile(pattern).match(self.text)
        self.outs, self.dead, self.shown, self._reply = [], False, False, None

    def nav_rows(self, refresh: bool = False) -> list:
        return [[ui.btn("📖 İzah", _cb(self.tok, f"gc:{self.name}"))],
                _nav(self.tok, f"qc:{self.name}", f".{self.name}", refresh=f"run:{self.name}" if refresh else "")]

    async def edit(self, text="", **kw):
        if not self.dead:
            self.shown = True
            await self.ub.edit_panel(self.iid or self.cb, guide._fit(str(text), 3800), ui.kb(*self.nav_rows(refresh=True)))
        return self

    respond = reply = edit

    async def delete(self, *a, **kw):
        return None

    async def get_reply_message(self):
        if self._reply is None and self.reply_to_msg_id and self.client:
            try:
                self._reply = await self.client.get_messages(self.chat_id, ids=self.reply_to_msg_id)
            except Exception as e:
                logger.debug(f"reply mesajı alınmadı: {e}")
        return self._reply

    async def get_input_chat(self):
        return await self.client.get_input_entity(self.chat_id)


def drop_panel(ub, tok):
    """Menyu başqa səhifəyə keçəndə / bağlananda: işləyən komandanın kartı, fon işi və gözləyən arqument ləğv olunur."""
    ub.state.get("_pending", {}).pop(tok, None)
    if (ub.state.get("_await") or {}).get("tok") == tok:
        ub.state.pop("_await", None)
    ev = ub.state.get("_panels", {}).pop(tok, None)
    if ev is None:
        return
    ev.dead = True
    for o in ev.outs:
        o.closed = True
        if o.running():
            o.task.cancel()
        ub.cards.pop(o.tok, None)


async def _latest_id(client, chat_id) -> int:
    """purge kimi komandalar 'komanda mesajının id-si'nə qədər işləyir — menyudan işləyəndə çatın son mesajı."""
    try:
        msgs = await client.get_messages(chat_id, limit=1)
        return int(msgs[0].id) if msgs else 0
    except Exception:
        return 0


async def run_direct(ub, cb, tok, name, args: str = "", reply_id=None) -> str:
    """Komandanın funksiyasını birbaşa çağırır; cavab menyunun öz mesajında. → xəta mətni ('' = uğurlu).
    Təsdiq siyasətini çağıran (callback) həll edir; burada yalnız komandanın düymə ilə işləyə bilib-bilmədiyi yoxlanır."""
    c = next((c for c in ub.commands if c["name"] == name), None)
    i = cmds.info(ub, name)
    if c is None or i["mode"] == "none" or name in cmd_off(ub):
        return "bu komanda düymə ilə işlədilmir"
    text = cmds.compose(name, args)
    if not cmds.matches(ub, name, text):
        return f"arqument formatı uyğun deyil: {text}"
    iid = cb if isinstance(cb, str) else getattr(cb, "inline_message_id", None)
    if not iid:
        return "yalnız inline menyuda işləyir"
    if not ub.client:
        return "userbot qoşulmayıb"
    drop_panel(ub, tok)
    ev = PanelEvent(ub, iid, tok, name, c["pattern"], text, reply_id)
    if i["mode"] != "safe":
        ev.id = await _latest_id(ub.client, ev.chat_id)
    ub.state.setdefault("_panels", {})[tok] = ev
    await ub.edit_panel(iid, f"⏳ <b>{escape(text)}</b> işləyir…", ui.kb(_nav(tok, f"qc:{name}", f".{name}")))
    try:
        await c["fn"](ev)
    except Exception as e:
        logger.warning(f"{text} düymədən işlədilmədi: {e}", exc_info=True)
        if not ev.dead:
            await ub.edit_panel(iid, f"❌ <b>{escape(text)}</b>: <code>{escape(str(e))[:300]}</code>",
                                ui.kb(*ev.nav_rows(refresh=True)))
        return ""
    if not ev.shown and not ev.dead:
        await ub.edit_panel(iid, f"✅ <b>{escape(text)}</b> icra olundu", ui.kb(*ev.nav_rows(refresh=True)))
    return ""


# ───────────── görünüşlər ─────────────
def _cb(tok: str, act: str) -> str:
    return f"ubp:{tok}:{act}"[:64]


def _nav(tok: str, back: str = "", to: str = "", refresh: str = "", home: bool = True):
    return ui.nav(_cb(tok, back) if back else "", to, close_data=_cb(tok, "close"),
                  home_data=_cb(tok, "main") if home else "", refresh_data=_cb(tok, refresh) if refresh else "")


def _when(ts: int) -> str:
    return time.strftime("%d.%m %H:%M", time.localtime(ts)) if ts else "—"


def hub_view(ub, tok: str, snap: list, note: str = ""):
    on = sum(1 for p in snap if p["on"])
    cmds = sum(len(p["cmds"]) for p in snap)
    off = len(cmd_off(ub))
    try:
        conn = "🟢 qoşulub" if ub.client is not None and ub.client.is_connected() else "🔴 qoşulmayıb"
    except Exception:
        conn = "🔴 qoşulmayıb"
    lines = ["<i>🏠 › 🧩 Userbot</i>", "🧩 <b>Userbot menyusu</b>", "━━━━━━━━━━━━━━━━━━",
             f"Hesab: <b>{conn}</b>", f"Pluginlər: <b>{on}/{len(snap)}</b> · komanda: <b>{cmds}</b>"
             + (f" (🔴 {off} söndürülüb)" if off else ""),
             "<i>Pluginləri idarə et, panelləri aç, əmrləri düymə ilə işlət, bələdçini oxu, görünüşü (kod bloku) seç.</i>"]
    if note:
        lines += ["", note]
    rows = [[ui.btn("🧩 Pluginlər", _cb(tok, "pl")), ui.btn("⚡ Funksiyalar", _cb(tok, "fn"))],
            [ui.btn("▶️ Əmrlər", _cb(tok, "qk")), ui.btn("📖 Bələdçi", _cb(tok, "g"))],
            [ui.btn("🎨 Görünüş", _cb(tok, "ui"))],
            _nav(tok, refresh="main", home=False)]
    return "\n".join(lines), ui.kb(*rows)


def list_view(snap: list, tok: str, page: int = 0, note: str = ""):
    on = sum(1 for p in snap if p["on"])
    cmds = sum(len(p["cmds"]) for p in snap)
    lines = ["<i>🏠 › 🧩 Userbot › Pluginlər</i>", "🧩 <b>Userbot pluginləri</b>", "━━━━━━━━━━━━━━━━━━",
             f"Yüklü: <b>{on}/{len(snap)}</b> · komanda: <b>{cmds}</b>",
             "<i>Plugini seç — komandaları, söndür / yenilə / sil.</i>"]
    since = max((p["since"] for p in snap), default=0)
    if since:
        idle = [p["name"] for p in snap if p["on"] and p["cmds"] and not p["uses"]["n"]]
        top = sorted((p for p in snap if p["uses"]["n"]), key=lambda p: -p["uses"]["n"])[:3]
        lines.append(f"📊 Sayğac {_when(since)}-dən: " + (", ".join(f"{p['name']} {p['uses']['n']}" for p in top) or "hələ çağırış yoxdur"))
        if idle:
            lines.append(f"💤 Heç işlənməyib: {len(idle)} plugin")
    if note:
        lines += ["", note]
    items, page, pages = ui.paginate(snap, page, PER_PAGE)
    rows = ui.grid([ui.toggle(p["name"], p["on"], _cb(tok, f"p:{p['name']}"), compact=True) for p in items], 2)
    rows.append(ui.pager(f"ubp:{tok}:pl:", page, pages, _cb(tok, "nop")))
    rows.append([ui.btn("🔄 Hamısını yenilə", _cb(tok, "ra")), ui.btn("➕ Yeni plugin", _cb(tok, "new"))])
    if any(not p["on"] for p in snap):
        rows.append([ui.btn("🟢 Hamısını yandır", _cb(tok, "ea"))])
    rows.append(_nav(tok, "main", "Menyu", refresh=f"pl:{page}"))
    return "\n".join(lines), ui.kb(*rows)


def detail_view(snap: list, tok: str, name: str, note: str = "", n_off: int = 0):
    p = next((x for x in snap if x["name"] == name), None)
    if p is None:
        return list_view(snap, tok, note="❌ Plugin tapılmadı")
    lines = [f"<i>🏠 › 🧩 Userbot › Pluginlər › {escape(name)}</i>", f"🧩 <b>{escape(name)}.py</b>", "━━━━━━━━━━━━━━━━━━",
             f"Vəziyyət: <b>{ui.state(p['on'])}</b> · {p['lines']} sətir"]
    if p["on"]:
        lines.append("Komandalar: " + (", ".join(f"<code>.{escape(c)}</code>" for c in p["cmds"][:30]) or "—")
                     + (f" <i>(🔴 {n_off} söndürülüb)</i>" if n_off else ""))
        if p["secs"]:
            lines.append("Panel bölmələri: " + ", ".join(escape(s) for s in p["secs"]))
        if p["raw"]:
            lines.append(f"Fon handlerləri: {p['raw']}")
    else:
        lines.append("<i>Yüklənməyib — komandaları işləmir. Restartdan sonra da yüklənməz.</i>")
    u = p["uses"]
    if p["since"]:
        lines.append(f"📊 Çağırış: <b>{u['n']}</b> · son: {_when(u['last'])} <i>(sayğac {_when(p['since'])}-dən)</i>")
        top = sorted(u["cmds"].items(), key=lambda kv: -kv[1])[:5]
        if top:
            lines.append("Ən çox: " + ", ".join(f"<code>.{escape(c)}</code> {n}" for c, n in top))
    else:
        lines.append("<i>📊 Sayğac hələ işləmir — ilk komandadan sonra başlayır.</i>")
    if p["protected"]:
        lines.append("<i>🔒 Əsas plugin — söndürmək və silmək olmaz (.install / .reload / .menu burada).</i>")
    if note:
        lines += ["", note]
    top = [ui.toggle("Plugin", p["on"], _cb(tok, f"t:{name}"))] if not p["protected"] else []
    mid = ([ui.btn("🧰 Komandalar", _cb(tok, f"cs:{name}:0"))] if p["on"] and p["cmds"] else []) + \
          ([ui.btn("🔄 Yenilə", _cb(tok, f"r:{name}"))] if p["on"] else [])
    low = ([ui.btn("🗑 Sil", _cb(tok, f"d:{name}"))] if not p["protected"] else []) + \
          ([ui.btn("♻️ Sayğacı sıfırla", _cb(tok, f"z:{name}"))] if u["n"] else [])
    return "\n".join(lines), ui.kb(top, mid, low, _nav(tok, "pl", "Pluginlər", refresh=f"p:{name}"))


def cmds_view(ub, tok: str, name: str, page: int = 0, note: str = ""):
    """🧰 Plugin daxilində hər komandanı ayrıca söndür / yandır."""
    cmds = sorted(ub.plugin_summary(name)["commands"]) if is_on(ub, name) else []
    if not cmds:
        return detail_view(snapshot(ub), tok, name, note)
    off = cmd_off(ub)
    lines = [f"<i>🏠 › 🧩 Userbot › Pluginlər › {escape(name)} › Komandalar</i>", f"🧰 <b>{escape(name)} — komandalar</b>",
             "━━━━━━━━━━━━━━━━━━",
             f"İşləyir: <b>{sum(1 for c in cmds if c not in off)}/{len(cmds)}</b>",
             "<i>🔴 söndürülmüş komanda yazılanda heç nə etmir. Plugin yüklü qalır, digər komandalar işləyir.</i>"]
    if note:
        lines += ["", note]
    items, page, pages = ui.paginate(cmds, page, CMD_PER_PAGE)
    rows = ui.grid([ui.toggle(f".{c}", c not in off, _cb(tok, f"ct:{name}:{page}:{c}"), compact=True)
                    if c not in PROTECTED_CMDS else ui.btn(f".{c} 🔒", _cb(tok, f"ct:{name}:{page}:{c}")) for c in items], 2)
    rows.append(ui.pager(f"ubp:{tok}:cs:{name}:", page, pages, _cb(tok, "nop")))
    rows.append(_nav(tok, f"p:{name}", name, refresh=f"cs:{name}:{page}"))
    return "\n".join(lines), ui.kb(*rows)


def functions_view(ub, tok: str, note: str = ""):
    """⚡ Userbot panelləri: plugin bölmələri (ub.sections) + host ekranları — ubm: mexanizmi ilə açılır."""
    secs = sorted(ub.sections.items(), key=lambda kv: kv[1].get("order", 50))
    lines = ["<i>🏠 › 🧩 Userbot › Funksiyalar</i>", "⚡ <b>Userbot funksiyaları</b>", "━━━━━━━━━━━━━━━━━━",
             "Panelləri düymə ilə aç — komanda yazmaq lazım deyil.", ""]
    lines += [f"• {escape(v['label'])}" for _, v in secs] + [f"• {label}" for label, _ in BUILTIN_SCREENS] + ["• 🎨 Görünüş"]
    total = sum(cmds.counts(ub, guide.build(ub)).values())
    lines.append(f"\n🧰 <b>Bütün komandalar ({total})</b> də düymədir — aşağıdakı düyməyə bas.")
    if note:
        lines += ["", note]
    btns = [ui.btn(v["label"], f"ubm:{tok}:{k}"[:64]) for k, v in secs]
    btns += [ui.btn(label, f"ubm:{tok}:{key}") for label, key in BUILTIN_SCREENS]
    btns.append(ui.btn("🎨 Görünüş", _cb(tok, "ui")))
    rows = [[ui.btn(f"🧰 Bütün əmrlər {total}", _cb(tok, "qk"))]] + ui.grid(btns, 2)
    rows.append(_nav(tok, "main", "Menyu", refresh="fn"))
    return "\n".join(lines), ui.kb(*rows)


def quick_view(ub, tok: str, note: str = ""):
    """▶️ Zərərsiz komandalar düymə ilə: userbot hesabından menyu çatında yazılır."""
    names = [n for n in guide.RUNNABLE if guide.runnable(ub, n) and n not in cmd_off(ub)]
    lines = ["<i>🏠 › 🧩 Userbot › Əmrlər › Sürətli</i>", "⚡ <b>Sürətli əmrlər</b>", "━━━━━━━━━━━━━━━━━━",
             "Düyməyə bas — funksiya <b>birbaşa</b> işləyir, nəticə bu mesajda çıxır (çata mesaj yazılmır).", ""]
    lines += [f"• <code>.{n}</code> — {escape(ub.help[n][1])}" for n in names if n in ub.help]
    lines.append("\n<i>Burada yalnız məlumat göstərənlər var (təsdiqsiz). Təsir edən və arqumentli komandalar "
                 "üçün 🧰 Əmrlər-ə qayıdın. İstifadə qaydası: 📖 İzahlar.</i>")
    if note:
        lines += ["", note]
    rows = ui.grid([ui.btn(f"▶️ .{n}", _cb(tok, f"run:{n}")) for n in names], 2)
    rows.append([ui.btn("📖 İzahlar", _cb(tok, f"gp:{guide.RUN}:0"))])
    rows.append(_nav(tok, "qk", "Əmrlər", refresh="qs"))
    return "\n".join(lines), ui.kb(*rows)


def confirm_view(tok: str, name: str):
    text = (f"<i>🏠 › 🧩 Userbot › Pluginlər › {escape(name)}</i>\n🗑 <b>{escape(name)}.py silinsin?</b>\n"
            "━━━━━━━━━━━━━━━━━━\nFayl diskdən silinəcək, komandaları dayanacaq. Geri qaytarmaq olmur.")
    return text, ui.kb(ui.confirm(_cb(tok, f"dy:{name}"), _cb(tok, f"p:{name}"), "sil"))


def new_plugin_view(tok: str):
    text = ("<i>🏠 › 🧩 Userbot › Pluginlər › Yeni plugin</i>\n➕ <b>Yeni plugin</b>\n━━━━━━━━━━━━━━━━━━\n"
            "1. <code>register(ub)</code> funksiyası olan <code>.py</code> faylı hazırla.\n"
            "2. Faylı userbot hesabından göndər, ona <b>reply</b> edib <code>.install</code> yaz.\n"
            "3. Dərhal yüklənir və bu siyahıda görünür.\n\n"
            "<i>Fayl adı <code>.install ad</code> ilə dəyişdirilə bilər. Sintaksis xətası olsa yüklənmir. "
            "Hər komandaya <code>help=(qısa, ətraflı)</code> yazın — bələdçi onu özü göstərəcək.</i>")
    return text, ui.kb(_nav(tok, "pl", "Pluginlər"))


def text_view(snap: list) -> str:
    """Inline düymə qadağan olan çat üçün sadə mətn."""
    rows = [f"{'🟢' if p['on'] else '🔴'} {p['name']} — " + (", ".join("." + c for c in p["cmds"][:6]) or "—")
            for p in snap]
    return ("🧩 Userbot pluginləri\n\n" + "\n".join(rows)
            + "\n\nİdarə: .plugins / .reload ad / .uninstall ad · bələdçi: .help")


# ───────────── qoşulma ─────────────
def setup(ub, dp):
    def runnable_names() -> tuple:
        return tuple(n for n in guide.RUNNABLE if guide.runnable(ub, n) and n not in cmd_off(ub))

    def view_for(tok: str, act: str, note: str = ""):
        head, _, arg = act.partition(":")
        snap = snapshot(ub)
        if head == "pl":
            return list_view(snap, tok, int(arg) if arg.isdigit() else 0, note)
        if head == "p":
            n_off = sum(1 for c in ub.plugin_summary(arg)["commands"] if c in cmd_off(ub)) if is_on(ub, arg) else 0
            return detail_view(snap, tok, arg, note, n_off)
        if head == "d":
            return confirm_view(tok, arg)
        if head == "new":
            return new_plugin_view(tok)
        if head == "cs":
            name, _, page = arg.partition(":")
            return cmds_view(ub, tok, name, int(page) if page.isdigit() else 0, note)
        if head == "fn":
            return functions_view(ub, tok, note)
        if head == "so":                                       # so:<q|g>:<komanda> — ⚙️ ayarlar
            where, _, cmd = arg.partition(":")
            return options_view(ub, tok, cmd, where, note)
        if head == "ui":
            return pstyle.view(tok, note)
        if head == "uis":
            return pstyle.style_picker(tok, note)
        if head == "uib":
            return pstyle.bar_picker(tok, note)
        if head in ("qk", "qs", "qg", "qc"):
            if head == "qs":
                return quick_view(ub, tok, note)
            groups = guide.build(ub)
            if head == "qk":
                return cmds.home_view(ub, groups, tok, int(arg) if arg.isdigit() else 0, note)
            if head == "qg":
                gid, _, page = arg.rpartition(":")
                return cmds.group_view(ub, groups, tok, gid, int(page) if page.isdigit() else 0)
            return cmds.command_view(ub, groups, tok, arg, off=arg in cmd_off(ub), protected=arg in PROTECTED_CMDS, note=note,
                                     quiet=arg in quiet_set(ub), options=bool(cmd_options(ub, arg)))
        if head in ("g", "gp", "gc"):
            groups = guide.build(ub)
            if head == "g":
                off = [p["name"] for p in snap if not p["on"]]
                return guide.home_view(groups, tok, int(arg) if arg.isdigit() else 0, off)
            if head == "gp":
                gid, _, page = arg.rpartition(":")
                return guide.group_view(groups, tok, gid, int(page) if page.isdigit() else 0, only=runnable_names())
            return guide.command_view(groups, tok, arg, ran_ok=guide.runnable(ub, arg) and arg not in cmd_off(ub),
                                      off=arg in cmd_off(ub), protected=arg in PROTECTED_CMDS, note=note,
                                      quiet=arg in quiet_set(ub), options=bool(cmd_options(ub, arg)))
        return hub_view(ub, tok, snap, note)

    async def fail(cb, text):
        await cb.answer(f"❌ {text}", show_alert=True)

    async def submit(tok, name, args, reply_id, iid):
        """Yazılan arqumenti icra edir: təsir edən komandada əvvəl təsdiq ekranı (dəqiq mətnlə)."""
        i, text = cmds.info(ub, name), cmds.compose(name, args)
        if i["mode"] == "none" or name in cmd_off(ub) or not cmds.matches(ub, name, text):
            groups = guide.build(ub)
            await ub.edit_panel(iid, *cmds.command_view(ub, groups, tok, name, off=name in cmd_off(ub),
                                                         note=f"❌ Arqument formatı uyğun deyil: <code>{escape(text)}</code>"))
            return
        if i["confirm"]:
            ub.state.setdefault("_pending", {})[tok] = {"name": name, "args": args, "reply_id": reply_id}
            await ub.edit_panel(iid, *cmds.confirm_view(tok, name, text, "rp", f"qc:{name}",
                                                         "↩️ <b>reply</b> ilə seçdiyiniz mesaja tətbiq olunur" if reply_id else ""))
            return
        err = await run_direct(ub, iid, tok, name, args, reply_id)
        if err:
            await ub.edit_panel(iid, f"❌ {escape(err)}", ui.kb(_nav(tok, f"qc:{name}", f".{name}")))

    async def run_flow(cb, tok, head, arg):
        """run — ▶️ (təsdiqsiz: safe; digərləri təsdiq ekranı) · ry — təsdiqdən sonra · rp — arqumentli, təsdiqdən sonra · in — arqument gözlə."""
        iid = getattr(cb, "inline_message_id", None)
        if head == "rp":
            p = ub.state.get("_pending", {}).pop(tok, None)
            if not p:
                return await fail(cb, "vaxtı keçib — komandanı yenidən seç")
            await cb.answer(f"▶️ .{p['name']}")
            err = await run_direct(ub, cb, tok, p["name"], p["args"], p["reply_id"])
            return await fail(cb, err) if err else None
        i = cmds.info(ub, arg)
        if i["mode"] == "none" or arg in cmd_off(ub):
            return await fail(cb, "bu komanda düymə ilə işlədilmir")
        if head == "in":
            if not i["takes_args"]:
                return await fail(cb, "bu komanda arqument almır")
            m = ub.menus.get(tok) or {}
            chat_id = m.get("chat_id")
            ub.state["_await"] = {"tok": tok, "name": arg, "chat_id": chat_id or ub.creator_id, "iid": iid, "ts": time.time()}
            groups = guide.build(ub)
            _, e = guide.find(groups, arg)
            await cb.answer("✍️")
            await ub.edit_panel(cb, *cmds.prompt_view(ub, tok, arg, e["long"] if e else "", same_chat=bool(chat_id)))
            return
        if not i["bare_ok"]:
            return await fail(cb, "bu komanda arqumentsiz işləmir — ✍️ Arqument yaz")
        if head == "run" and i["confirm"]:                   # təsir edən komanda — əvvəl təsdiq
            await cb.answer()
            await ub.edit_panel(cb, *cmds.confirm_view(tok, arg, cmds.compose(arg), f"ry:{arg}", f"qc:{arg}"))
            return
        await cb.answer(f"▶️ .{arg}")
        err = await run_direct(ub, cb, tok, arg)
        if err:
            await fail(cb, err)

    async def on_input(event):
        """✍️ gözləyən arqument: menyu çatında yazdığınız NÖVBƏTİ mesaj (silinir) komandanın arqumenti olur."""
        st = ub.state.get("_await")
        if not st or getattr(event, "chat_id", None) != st["chat_id"]:
            return
        if time.time() - st["ts"] > AWAIT_TTL:
            ub.state.pop("_await", None)
            return
        text = (getattr(event, "raw_text", None) or "").strip()
        if re.match(r"^\.[A-Za-z]", text):                   # başqa komanda yazdınız — gözləmə ləğv
            ub.state.pop("_await", None)
            return
        ub.state.pop("_await", None)
        args = "" if text in (".", "") else text
        reply_id = getattr(event, "reply_to_msg_id", None)
        try:
            await event.delete()
        except Exception as e:
            logger.debug(f"arqument mesajı silinmədi: {e}")
        try:
            await submit(st["tok"], st["name"], args, reply_id, st["iid"])
        except Exception as e:
            logger.warning(f"arqumentli icra xətası: {e}", exc_info=True)

    raw = getattr(ub, "raw", None)
    if raw is not None:
        try:
            from telethon import events
            raw(events.NewMessage(outgoing=True))(on_input)
        except Exception as e:
            logger.warning(f"arqument gözləyicisi qoşulmadı: {e}")
    ub.state["plugin_menu_input"] = on_input

    @dp.callback_query(F.data.startswith("ubp:"))
    async def pm_cb(cb):
        if not ub.is_creator(cb.from_user.id):
            await cb.answer("⛔ Bu panel yalnız sahibi üçündür", show_alert=True)
            return
        _, tok, act = (cb.data.split(":", 2) + ["main", "main"])[:3]
        head, _, arg = act.partition(":")
        note, ok = "", True
        if head not in ("ry", "rp"):                         # başqa səhifəyə keçid — işləyən komanda / gözləmə dayansın
            drop_panel(ub, tok)
        if head == "nop":                                    # səhifələmədə cari səhifə sayğacı
            await cb.answer()
            return
        if head == "close":
            await cb.answer("Bağlandı")
            m = ub.menus.pop(tok, None)
            if m and m.get("msg_id") and ub.client:
                try:
                    await ub.client.delete_messages(m["chat_id"], [m["msg_id"]])
                    return
                except Exception as e:
                    logger.debug(f"plugin paneli silinmədi: {e}")
            await ub.edit_panel(cb, "✖️ <i>Panel bağlandı</i>", None)
            return
        if head in ("run", "ry", "rp", "in"):
            await run_flow(cb, tok, head, arg)
            return
        if head == "t":
            ok, msg = set_enabled(ub, arg, not is_on(ub, arg))
            note, act = msg, f"p:{arg}"
        elif head == "r":
            ok, msg = reload_one(ub, arg)
            note, act = msg, f"p:{arg}"
        elif head == "dy":
            ok, msg = uninstall(ub, arg)
            note, act = msg, ("pl" if ok else f"p:{arg}")
        elif head == "ra":
            ok, msg = reload_all(ub)
            note, act = msg, "pl"
        elif head == "ea":
            ok, msg = enable_all(ub)
            note, act = msg, "pl"
        elif head == "z":
            reset_usage(ub, arg)
            note, act = "♻️ Sayğac sıfırlandı", f"p:{arg}"
        elif head == "uit" and arg in ("box", "kb"):         # 🎨 botun /ui ayarları ilə ortaq
            note, act = pstyle.set_toggle(arg), "ui"
        elif head == "uis" and arg:
            note, act = pstyle.set_style(arg), "uis"
        elif head == "uib" and arg:
            note, act = pstyle.set_bar(arg), "uib"
        elif head == "ct":                                   # ct:<plugin>:<səhifə>:<komanda>
            plugin, page, cmd = (arg.split(":", 2) + ["", ""])[:3]
            ok, msg = set_cmd(ub, cmd, cmd in cmd_off(ub))
            note, act = msg, f"cs:{plugin}:{page}"
        elif head == "sv":                                   # sv:<q|g>:<komanda>:<açar>:<dəyər>
            where, cmd, key, value = (arg.split(":", 3) + ["", "", ""])[:4]
            ok, msg = opt_set(ub, cmd, key, value)
            note, act = msg, f"so:{where}:{cmd}"
        elif head == "cq":                                   # cq:<q|g>:<komanda> — 💬 cavab mesajı göstər / gizlət
            where, _, cmd = arg.partition(":")
            ok, msg = set_quiet(ub, cmd, cmd not in quiet_set(ub))
            note, act = msg, f"{'qc' if where == 'q' else 'gc'}:{cmd}"
        elif head == "cg":                                   # bələdçidən: komandanı çevir, səhifədə qal
            ok, msg = set_cmd(ub, arg, arg in cmd_off(ub))
            note, act = msg, f"gc:{arg}"
        await cb.answer(note[:190] if note and not ok else None, show_alert=bool(note and not ok))
        try:
            await ub.edit_panel(cb, *view_for(tok, act, note))
        except Exception as e:
            logger.warning(f"plugin paneli xətası ({act}): {e}", exc_info=True)

    def start_view(tok: str, start: str = "main"):
        return view_for(tok, start)

    ub.state["plugin_menu_view"] = start_view
    ub.state["plugin_menu_text"] = lambda: text_view(snapshot(ub))
    ub.state["plugin_guide_text"] = lambda: guide.text_view(guide.build(ub))
