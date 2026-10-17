"""🧩 Userbot .menu — plugin idarəsi, funksiyalar, əmrlər, sayğac və bələdçi (userbot/core/plugin_menu.py, plugin_guide.py)."""
import ast
import asyncio
import glob
import json
import time
import re
import types

import pytest

from userbot.core import plugin_guide as guide
from userbot.core import plugin_menu as pm

TOK = "abc123"


class FakeDB:
    def __init__(self):
        self.d = {}

    def kv_get(self, ns, key, default=None):
        return self.d.get((ns, key), default)

    def kv_set(self, ns, key, value):
        self.d[(ns, key)] = value


class FakeUB:
    """Host reyestrinin kiçik surəti: pluginlər, komandalar, help, bölmələr."""

    def __init__(self, tmp_path, names=("system", "alpha", "beta"), extra=None):
        self.db = FakeDB()
        self.menus, self.client, self.edits, self.removed = {}, None, [], []
        self.plugin_files, self.commands, self.help, self.help_owner, self.sections = {}, [], {}, {}, {}
        self.extra = extra if extra is not None else {"alpha": ["alive", "ban"]}
        for n in names:
            (tmp_path / f"{n}.py").write_text("def register(ub):\n    pass\n", encoding="utf-8")
        (tmp_path / "_example.py").write_text("x = 1\n", encoding="utf-8")
        self.state = {"plugins_dir": str(tmp_path), "load_plugin_file": self._load}
        for host_cmd in ("menu", "help"):                          # host komandaları — sahibsiz
            self.commands.append({"name": host_cmd, "pattern": rf"^\.{host_cmd}$", "owner": None, "fn": None})
            self.help[host_cmd] = (f".{host_cmd}", f"{host_cmd} qısa", "uzun", "acc")
        self.help["depo_add"] = ("/depo add", "mənbə", "bot izahı", "bot")
        self.help["p_status"] = ("📊 Status", "panel", "panel izahı", "panel")
        for n in names:
            self._load(str(tmp_path / f"{n}.py"))
        self.ran = []

    def _load(self, path, attach=True):
        name = path.rsplit("/", 1)[-1][:-3]
        if name in self.plugin_files:
            self.remove_plugin(name)
        self.plugin_files[name] = path
        for c in [f"{name}cmd"] + self.extra.get(name, []):
            self.commands.append({"name": c, "pattern": rf"^\.{c}$", "outgoing": True, "owner": name,
                                  "fn": self._fn(c)})
            self.help[c] = (f".{c}", f"{c} qısa", f"<b>İstifadə:</b> <code>.{c}</code>", "acc")
            self.help_owner[c] = name
        return name, self.plugin_summary(name)

    def _fn(self, c):
        async def fn(event):
            self.ran.append(c)
        return fn

    def plugin_summary(self, owner):
        return {"commands": [c["name"] for c in self.commands if c.get("owner") == owner], "sections": [], "raw": 0}

    def remove_plugin(self, owner):
        self.removed.append(owner)
        for c in [c for c in self.commands if c.get("owner") == owner]:
            self.commands.remove(c)
            self.help.pop(c["name"], None)

    def attach_pending(self):
        return 0

    def is_creator(self, uid):
        return uid == 1

    async def edit_panel(self, cb, text, kb):
        self.edits.append((text, kb))


@pytest.fixture
def ub(tmp_path):
    return FakeUB(tmp_path)


def flat(kb):
    return [b for r in kb.inline_keyboard for b in r]


def datas(kb):
    return [b.callback_data for b in flat(kb)]


# ───────────── plugin söndür / yandır ─────────────
def test_snapshot_lists_disk_files_and_marks_protected(ub):
    snap = {p["name"]: p for p in pm.snapshot(ub)}
    assert sorted(snap) == ["alpha", "beta", "system"]          # _example.py sayılmır
    assert snap["alpha"]["on"] and snap["alpha"]["cmds"] == ["alphacmd", "alive", "ban"]
    assert snap["system"]["protected"] and not snap["alpha"]["protected"]


def test_disable_unloads_and_remembers_then_enable_loads_again(ub):
    ok, msg = pm.set_enabled(ub, "alpha", False)
    assert ok and "alpha" not in ub.plugin_files and ub.removed == ["alpha"]
    assert pm.disabled(ub) == ["alpha"]
    assert [p for p in pm.snapshot(ub) if p["name"] == "alpha"][0]["on"] is False   # fayl qalır, siyahıda görünür
    ok, msg = pm.set_enabled(ub, "alpha", True)
    assert ok and "alpha" in ub.plugin_files and pm.disabled(ub) == []


def test_failed_enable_stays_disabled(ub):
    pm.set_enabled(ub, "beta", False)
    ub.state["load_plugin_file"] = lambda path, attach=True: (_ for _ in ()).throw(RuntimeError("sintaksis"))
    ok, msg = pm.set_enabled(ub, "beta", True)
    assert not ok and "sintaksis" in msg and pm.disabled(ub) == ["beta"]


def test_enable_all_brings_back_every_disabled_plugin(ub):
    pm.set_enabled(ub, "alpha", False)
    pm.set_enabled(ub, "beta", False)
    ok, msg = pm.enable_all(ub)
    assert ok and "2 plugin" in msg and pm.disabled(ub) == [] and {"alpha", "beta"} <= set(ub.plugin_files)


def test_system_plugin_cannot_be_disabled_or_deleted(ub, tmp_path):
    assert not pm.set_enabled(ub, "system", False)[0]
    assert not pm.uninstall(ub, "system")[0]
    assert "system" in ub.plugin_files and (tmp_path / "system.py").exists()


def test_uninstall_removes_file_and_forgets_it(ub, tmp_path):
    pm.set_enabled(ub, "beta", False)
    ok, _ = pm.uninstall(ub, "beta")
    assert ok and not (tmp_path / "beta.py").exists() and pm.disabled(ub) == []
    assert "beta" not in [p["name"] for p in pm.snapshot(ub)]
    assert not pm.set_enabled(ub, "ghost", True)[0]


def test_reload_one_and_all(ub):
    assert pm.reload_one(ub, "alpha")[0]
    pm.set_enabled(ub, "beta", False)
    assert not pm.reload_one(ub, "beta")[0]                      # söndürülüb
    ok, msg = pm.reload_all(ub)
    assert ok and "2 plugin" in msg                              # yalnız yüklülər


def test_startup_skips_disabled_plugins(tmp_path, monkeypatch):
    from userbot.core import host
    ub = FakeUB(tmp_path)
    pm.set_enabled(ub, "alpha", False)
    ub.plugin_files.clear()
    monkeypatch.setattr(host, "PLUGINS_DIR", str(tmp_path))
    monkeypatch.setattr(host, "load_plugin_file", lambda u, path, attach=False: ub._load(path))
    loaded = host.load_command_plugins(ub)
    assert "alpha" not in loaded and {"system", "beta"} <= set(loaded)
    ub.db.kv_set(pm.NS, pm.KEY, ["system"])                      # əsas plugin heç vaxt keçilmir
    assert "system" in host.load_command_plugins(ub)


# ───────────── ayrı-ayrı komandaları söndür ─────────────
def test_single_command_can_be_switched_off_and_on(ub):
    assert pm.set_cmd(ub, "ban", False)[0] and pm.cmd_off(ub) == {"ban"}
    assert pm.set_cmd(ub, "ban", True)[0] and pm.cmd_off(ub) == set()
    assert not pm.set_cmd(ub, "ghost", False)[0]


@pytest.mark.parametrize("name", sorted(pm.PROTECTED_CMDS))
def test_recovery_commands_cannot_be_switched_off(ub, name):
    ok, msg = pm.set_cmd(ub, name, False)
    assert not ok and "söndürülə bilməz" in msg and pm.cmd_off(ub) == set()
    ub.db.kv_set(pm.NS, pm.CMD_OFF, [name])                      # bazaya əl ilə yazılsa belə qapı bağlanmır
    assert name not in pm.cmd_off(ub)


def test_gate_blocks_disabled_commands_dedupes_and_counts(ub):
    ev = types.SimpleNamespace(chat_id=5, id=10)
    assert pm.gate(ub, "alpha", "alphacmd", ev) is True
    assert pm.gate(ub, "alpha", "alphacmd", ev) is False         # eyni mesaj ikinci dəfə gəlsə — keçmir
    assert pm.gate(ub, "alpha", "alphacmd", types.SimpleNamespace(chat_id=5, id=11)) is True
    assert pm.usage(ub)["alpha"]["n"] == 2
    pm.set_cmd(ub, "ban", False)
    assert pm.gate(ub, "alpha", "ban", types.SimpleNamespace(chat_id=5, id=12)) is False
    assert pm.usage(ub)["alpha"]["cmds"].get("ban") is None      # söndürülmüş komanda sayılmır
    assert pm.gate(ub, "alpha", "alphacmd", types.SimpleNamespace()) is True   # id-siz hadisə dedupe olunmur
    for i in range(400):
        pm.gate(ub, "alpha", "alphacmd", types.SimpleNamespace(chat_id=5, id=1000 + i))
    assert len(ub.state["_ran"]) <= 300                          # yaddaş böyümür


def test_command_decorator_wraps_with_gate(tmp_path):
    from userbot.core.userbot_api import UB
    ub = UB.__new__(UB)
    ub.commands, ub.help, ub.help_owner, ub._loading, ub.db, ub.state = [], {}, {}, "alpha", FakeDB(), {}

    @UB.command(ub, "ping")
    async def on_ping(event):
        return "pong"

    fn = ub.commands[0]["fn"]
    assert asyncio.run(fn(types.SimpleNamespace(chat_id=1, id=1))) == "pong"
    assert asyncio.run(fn(types.SimpleNamespace(chat_id=1, id=1))) is None      # dublikat hadisə
    assert asyncio.run(fn(types.SimpleNamespace(chat_id=1, id=2))) == "pong"
    assert pm.usage(ub)["alpha"]["cmds"] == {"ping": 2}
    pm.set_cmd(ub, "ping", False)
    assert asyncio.run(fn(types.SimpleNamespace(chat_id=1, id=3))) is None      # söndürülüb
    assert fn.__name__ == "on_ping"


# ───────────── görünüşlər: qaydalar ─────────────
def check_rules(text, kb, hub=False):
    ds = datas(kb)
    assert len(ds) == len(set(ds)), "təkrar callback"
    assert all(d and len(d.encode()) <= 64 for d in ds)
    assert all(d.startswith(("ubp:", "ubm:")) for d in ds)
    body = kb.inline_keyboard[:-1]                                                  # naviqasiyadan başqa sətirlər
    assert all(len(r) <= 2 or (len(r) == 3 and r[0].text == "◀️") for r in body)    # mətnli düymə ≤ 2, səhifələmə 3
    assert len(kb.inline_keyboard[-1]) <= 4
    last = [b.callback_data for b in kb.inline_keyboard[-1]]
    assert last[-1] == f"ubp:{TOK}:close"                                          # bağla həmişə sonuncu
    assert hub or f"ubp:{TOK}:main" in last                                        # naviqasiya sonuncu sətir
    assert text.startswith("<i>🏠")


def test_every_view_follows_panel_rules(ub):
    pm.set_enabled(ub, "beta", False)
    pm.note_use(ub, "alpha", "alphacmd")
    ub.sections = {"status": {"label": "📊 Status", "order": 10}, "depo": {"label": "📦 Depo", "order": 40}}
    snap = pm.snapshot(ub)
    groups = guide.build(ub)
    views = [
        (pm.hub_view(ub, TOK, snap), True), (pm.hub_view(ub, TOK, snap, note="ok"), True),
        (pm.list_view(snap, TOK), False), (pm.detail_view(snap, TOK, "alpha"), False),
        (pm.detail_view(snap, TOK, "beta"), False), (pm.detail_view(snap, TOK, "system"), False),
        (pm.cmds_view(ub, TOK, "alpha"), False), (pm.functions_view(ub, TOK), False), (pm.quick_view(ub, TOK), False),
        (pm.new_plugin_view(TOK), False),
        (guide.home_view(groups, TOK), False), (guide.group_view(groups, TOK, "alpha"), False),
        (guide.group_view(groups, TOK, guide.ALL), False), (guide.command_view(groups, TOK, "alive", ran_ok=True), False),
        (guide.command_view(groups, TOK, "menu"), False),
    ]
    for (text, kb), is_hub in views:
        check_rules(text, kb, hub=is_hub)
    text, kb = pm.confirm_view(TOK, "alpha")
    assert datas(kb) == [f"ubp:{TOK}:dy:alpha", f"ubp:{TOK}:p:alpha"]


def test_hub_has_four_sections_and_reflects_state(ub):
    pm.set_cmd(ub, "ban", False)
    text, kb = pm.hub_view(ub, TOK, pm.snapshot(ub))
    assert datas(kb)[:4] == [f"ubp:{TOK}:{a}" for a in ("pl", "fn", "qk", "g")]
    assert "3/3" in text and "🔴 1 söndürülüb" in text and "qoşulmayıb" in text
    ub.client = types.SimpleNamespace(is_connected=lambda: True)
    assert "🟢 qoşulub" in pm.hub_view(ub, TOK, pm.snapshot(ub))[0]


def test_list_shows_state_and_pages(tmp_path):
    ub = FakeUB(tmp_path, names=tuple(f"p{i:02d}" for i in range(11)) + ("system",), extra={})
    pm.set_enabled(ub, "p01", False)
    snap = pm.snapshot(ub)
    t0, k0 = pm.list_view(snap, TOK, 0)
    t1, k1 = pm.list_view(snap, TOK, 1)
    assert "Yüklü: <b>11/12</b>" in t0
    assert [b.text for b in flat(k0)][:2] == ["p00 🟢", "p01 🔴"]
    assert "2/2" in [b.text for b in flat(k1)] and "p10 🟢" in [b.text for b in flat(k1)]
    assert pm.list_view(snap, TOK, 99)[1].inline_keyboard[0][0].text == "p08 🟢"    # sərhəd sıxılır
    assert f"ubp:{TOK}:ea" in datas(k0)                                               # söndürülü var → hamısını yandır


def test_detail_hides_dangerous_buttons_for_protected_and_disabled(ub):
    pm.set_enabled(ub, "beta", False)
    snap = pm.snapshot(ub)
    assert not any(":t:" in d or ":d:" in d for d in datas(pm.detail_view(snap, TOK, "system")[1]))
    beta = datas(pm.detail_view(snap, TOK, "beta")[1])
    assert f"ubp:{TOK}:t:beta" in beta and f"ubp:{TOK}:r:beta" not in beta and f"ubp:{TOK}:cs:beta:0" not in beta
    assert f"ubp:{TOK}:cs:alpha:0" in datas(pm.detail_view(snap, TOK, "alpha")[1])


def test_text_fallback_lists_everything(ub):
    pm.set_enabled(ub, "beta", False)
    t = pm.text_view(pm.snapshot(ub))
    assert "🟢 alpha" in t and "🔴 beta" in t and ".alphacmd" in t and ".help" in t


# ───────────── ⚡ funksiyalar və ▶️ əmrlər ─────────────
def test_functions_page_lists_registered_sections_and_builtin_screens(ub):
    ub.sections = {"depo": {"label": "📦 Depo", "order": 40}, "status": {"label": "📊 Status", "order": 10}}
    text, kb = pm.functions_view(ub, TOK)
    labels = [b.text for b in flat(kb)]
    assert labels[0].startswith("🧰 Bütün əmrlər") and labels[1:3] == ["📊 Status", "📦 Depo"]   # order-ə görə
    assert f"ubp:{TOK}:qk" in datas(kb)
    assert {"⚙️ Performans", "🗂 Yaddaş & Keş", "🎨 Görünüş"} <= set(labels)
    assert f"ubm:{TOK}:status" in datas(kb) and f"ubm:{TOK}:perf" in datas(kb)
    assert "📦 Depo" in text


def test_quick_page_has_only_safe_loaded_enabled_commands(ub):
    ds = datas(pm.quick_view(ub, TOK)[1])
    assert f"ubp:{TOK}:run:alive" in ds and not any(d.endswith(":run:ban") for d in ds)
    pm.set_cmd(ub, "alive", False)
    assert f"ubp:{TOK}:run:alive" not in datas(pm.quick_view(ub, TOK)[1])
    pm.set_enabled(ub, "alpha", False)
    assert not any(":run:" in d for d in datas(pm.quick_view(ub, TOK)[1]))


# ───────────── ▶️ Əmrlər: funksiya birbaşa işləyir, çata mesaj yazılmır ─────────────
class NoSendClient:
    """Userbot client: mesaj göndərmək cəhdi testi sındırır."""

    async def send_message(self, *a, **kw):
        raise AssertionError("çata mesaj göndərilməməli")

    async def get_input_entity(self, chat_id):
        return ("input", chat_id)


def arm(ub, name, fn, pattern=None):
    """Hazır komandanın funksiyasını dəyişir: ub.commands-dakı qeyd həqiqi sarğıdır."""
    for c in ub.commands:
        if c["name"] == name:
            c["fn"] = fn
            if pattern:
                c["pattern"] = pattern
    return ub


def inline_cb(iid="iid"):
    return FakeCB("x") if iid else types.SimpleNamespace(inline_message_id=None, from_user=types.SimpleNamespace(id=1))


@pytest.fixture
def live(ub, monkeypatch):
    """Userbot qoşulub, menyu çatı məlum, bot redaktələri ub.edits-ə yığılır."""
    from userbot.core import userbot_api as api
    ub.client, ub.creator_id, ub.cards = NoSendClient(), 1, {}
    ub.menus[TOK] = {"chat_id": 42, "msg_id": 5}
    ub.tr = lambda text, **kw: text
    ub.track = lambda chat_id, kind, task: None

    class Bot:
        async def edit_message_text(self, **kw):
            ub.edits.append((kw["text"], kw.get("reply_markup")))
            ub.bot_calls.append(kw)
    ub.bot, ub.bot_calls = Bot(), []

    async def out(event, text, rows=None, inline=True):
        return await api.Out(ub, event, inline=inline).open(text, rows)
    ub.out = out
    monkeypatch.setattr(api, "CARD_MIN_GAP", 0)
    return ub


def test_plain_edit_result_goes_into_the_menu_message(live):
    from userbot.core.userbot_api import safe_edit

    async def fn(event):
        await safe_edit(event, "<b>ALIVE</b> 42")
    arm(live, "alive", fn)
    cb = FakeCB("x")
    assert asyncio.run(pm.run_direct(live, cb, TOK, "alive")) == ""
    texts = [t for t, _ in live.edits]
    assert texts[0].startswith("⏳") and texts[-1] == "<b>ALIVE</b> 42"                   # əvvəl "işləyir", sonra nəticə
    ds = datas(live.edits[-1][1])
    assert ds == [f"ubp:{TOK}:gc:alive", f"ubp:{TOK}:run:alive", f"ubp:{TOK}:qc:alive", f"ubp:{TOK}:main", f"ubp:{TOK}:close"]
    assert live.removed == [] and live.ran == []


def test_command_sees_menu_chat_arguments_and_a_pattern_match(live):
    seen = {}

    async def fn(event):
        seen.update(chat=event.chat_id, text=event.text, arg=event.pattern_match.group(1), reply=event.is_reply,
                    sender=event.sender_id, rm=await event.get_reply_message())
        await event.edit("ok")
    arm(live, "alive", fn, pattern=r"^\.alive(?:\s+(.+))?$")
    asyncio.run(pm.run_direct(live, FakeCB("x"), TOK, "alive"))
    assert seen == {"chat": 42, "text": ".alive", "arg": None, "reply": False, "sender": 1, "rm": None}


def test_card_commands_render_inside_the_menu_message(live):
    async def fn(event):
        out = await live.out(event, "CARD ONE", [])

        async def refresh(o):
            await o.update("CARD TWO")
        out.refresh = refresh
    arm(live, "alive", fn)
    asyncio.run(pm.run_direct(live, FakeCB("x"), TOK, "alive"))
    text, kb = live.edits[-1]
    assert text == "CARD ONE" and live.bot_calls[-1]["inline_message_id"] == "iid"
    ds = datas(kb)
    assert any(d.startswith("ubc:") and d.endswith(":r") for d in ds)                       # kartın öz 🔄 düyməsi işləyir
    assert not any(d.endswith(":x") for d in ds) and f"ubp:{TOK}:qc:alive" in ds and f"ubp:{TOK}:gc:alive" in ds
    (tok, out), = live.cards.items()
    asyncio.run(out.update("CARD TWO"))
    assert live.edits[-1][0] == "CARD TWO"


def test_running_command_stops_when_the_user_leaves_the_page(live):
    state = {}

    async def fn(event):
        out = await live.out(event, "WORKING")

        async def work():
            try:
                await asyncio.sleep(30)
            finally:
                state["cancelled"] = True
        state["out"] = out
        out.task = asyncio.get_event_loop().create_task(work())
    arm(live, "alive", fn)

    async def scenario():
        await pm.run_direct(live, FakeCB("x"), TOK, "alive")
        await asyncio.sleep(0)
        out = state["out"]
        assert out.running() and live.cards
        n = len(live.edits)
        pm.drop_panel(live, TOK)                                  # ⬅️ Əmrlər / başqa səhifə
        await asyncio.sleep(0.05)
        await out.update("LATE")                                  # köhnə iş yeni səhifənin üstündən yazmır
        return n, out
    n, out = asyncio.run(scenario())
    assert state["cancelled"] and out.closed and not live.cards and len(live.edits) == n


def test_leaving_via_callbacks_drops_the_running_command(live):
    async def fn(event):
        await event.edit("RESULT")
    arm(live, "alive", fn)
    click(live, f"ubp:{TOK}:run:alive")
    assert live.edits[-1][0] == "RESULT" and TOK in live.state["_panels"]
    ev = live.state["_panels"][TOK]
    click(live, f"ubp:{TOK}:qk")
    assert ev.dead and TOK not in live.state["_panels"] and "bütün komandalar" in live.edits[-1][0]
    asyncio.run(ev.edit("GEC GƏLƏN"))
    assert live.edits[-1][0] != "GEC GƏLƏN"


def test_failing_and_silent_commands_still_report(live):
    async def boom(event):
        raise RuntimeError("yox <b>")

    async def silent(event):
        return None
    arm(live, "alive", boom)
    assert asyncio.run(pm.run_direct(live, FakeCB("x"), TOK, "alive")) == ""
    assert live.edits[-1][0].startswith("❌ <b>.alive</b>") and "yox &lt;b&gt;" in live.edits[-1][0]
    arm(live, "alive", silent)
    asyncio.run(pm.run_direct(live, FakeCB("x"), TOK, "alive"))
    assert "icra olundu" in live.edits[-1][0]


def test_run_refuses_unknown_menu_only_or_unavailable(live):
    cb = FakeCB("x")
    assert "işlədilmir" in asyncio.run(pm.run_direct(live, cb, TOK, "ghost"))
    assert "işlədilmir" in asyncio.run(pm.run_direct(live, cb, TOK, "menu"))                  # menyunun özü
    pm.set_cmd(live, "alive", False)
    assert "işlədilmir" in asyncio.run(pm.run_direct(live, cb, TOK, "alive"))
    pm.set_cmd(live, "alive", True)
    assert "inline" in asyncio.run(pm.run_direct(live, inline_cb(None), TOK, "alive"))
    live.client = None
    assert "qoşulmayıb" in asyncio.run(pm.run_direct(live, cb, TOK, "alive"))
    assert live.edits == [] and live.ran == []


def test_quick_page_describes_every_command_and_links_to_the_guide(live):
    text, kb = pm.quick_view(live, TOK)
    assert "birbaşa" in text and "çata mesaj yazılmır" in text
    assert "<code>.alive</code> — alive qısa" in text and ".ban" not in text
    assert f"ubp:{TOK}:gp:{guide.RUN}:0" in datas(kb)
    groups = guide.build(live)
    page, kb2 = guide.group_view(groups, TOK, guide.RUN, 0, only=("alive",))
    assert "▶️ Düymə ilə işlədilənlər" in page and ".alive" in page and ".ban" not in page
    assert f"ubp:{TOK}:gc:alive" in datas(kb2) and f"ubp:{TOK}:qs" in datas(kb2)             # geri → Sürətli
    cmd, _ = guide.command_view(groups, TOK, "alive", ran_ok=True)
    assert "birbaşa çağırılır" in cmd and "<b>İstifadə:</b>" in cmd
    click(live, f"ubp:{TOK}:gp:{guide.RUN}:0")
    shown = live.edits[-1][0]
    assert ".alive" in shown and ".ban" not in shown


def test_run_callback_answers_first_and_reports_problems_as_alert(live):
    async def fn(event):
        await event.edit("OK")
    arm(live, "alive", fn)
    cb = click(live, f"ubp:{TOK}:run:alive")
    assert cb.answers == [("▶️ .alive", False)] and live.edits[-1][0] == "OK"
    cb = click(live, f"ubp:{TOK}:run:menu")
    assert cb.answers[-1][1] is True and "işlədilmir" in cb.answers[-1][0]


def test_real_plugins_command_runs_directly_without_sending_anything(monkeypatch):
    """Həqiqi `.plugins` funksiyası (safe_edit ilə cavab verir) panelin içində işləyir."""
    import sys
    from unittest.mock import MagicMock
    import userbot.core.userbot_api as api
    from userbot.core import host
    monkeypatch.setattr(api, "get_udb", lambda: FakeDB())
    saved = {k: v for k, v in sys.modules.items() if k.startswith("userbot.plugins.")}
    try:
        real = api.UB(MagicMock(creator_id=1))
        for key in ("acc", "bot", "panel"):
            real.help_category(key, key, key)
        host.load_command_plugins(real)
        real.client = NoSendClient()
        sent = []

        class Bot:
            async def edit_message_text(self, **kw):
                sent.append(kw)
        real.bot = Bot()
        real.menus[TOK] = {"chat_id": 42}
        asyncio.run(pm.run_direct(real, FakeCB("x"), TOK, "plugins"))
        assert "Userbot pluginləri" in sent[-1]["text"] and "chat_tools" in sent[-1]["text"]
        assert sent[-1]["inline_message_id"] == "iid"
        assert pm.usage(real)["system"]["cmds"]["plugins"] == 1                              # düymə ilə işləsə də sayılır
    finally:
        for k in [k for k in sys.modules if k.startswith("userbot.plugins.")]:
            sys.modules.pop(k, None)
        sys.modules.update(saved)



# ───────────── 📖 bələdçi ─────────────
def test_guide_groups_cover_host_plugins_bot_and_panel(ub):
    g = guide.build(ub)
    assert list(g) == [guide.HOST, "alpha", "beta", "system", guide.BOT, guide.PANEL]
    assert [e["name"] for e in g[guide.HOST]] == ["help", "menu"]
    assert [e["name"] for e in g["alpha"]] == ["alive", "alphacmd", "ban"]
    assert g[guide.BOT][0]["name"] == "depo_add" and g[guide.PANEL][0]["name"] == "p_status"
    assert sum(len(v) for v in g.values()) == len(ub.commands) + 2


def test_guide_follows_plugin_toggle_live(ub):
    pm.set_enabled(ub, "beta", False)
    assert "beta" not in guide.build(ub)
    text, _ = guide.home_view(guide.build(ub), TOK, 0, ["beta"])
    assert "Söndürülüb" in text and "beta" in text


def test_aliases_are_read_from_the_pattern():
    assert guide.aliases(r"^\.(?:storage|disk|size)(?:\s+(.+))?$", "storage") == ["storage", "disk", "size"]
    assert guide.aliases(r"^\.ping$", "ping") == ["ping"]
    assert guide.aliases("", "x") == ["x"]


def test_command_page_shows_help_aliases_run_and_toggle(ub):
    ub.commands.append({"name": "disk", "pattern": r"^\.(?:disk|size)$", "owner": "alpha", "fn": None})
    ub.help["disk"] = (".disk", "yer", "<b>İstifadə:</b> <code>.disk</code>", "acc")
    g = guide.build(ub)
    text, kb = guide.command_view(g, TOK, "disk")
    assert "Eyni komanda" in text and ".size" in text and "<b>İstifadə:</b>" in text and "🧩 alpha" in text
    ds = datas(kb)
    assert f"ubp:{TOK}:cg:disk" in ds and not any(":run:" in d for d in ds) and f"ubp:{TOK}:gp:alpha:0" in ds
    _, kb2 = guide.command_view(g, TOK, "alive", ran_ok=True)
    assert f"ubp:{TOK}:run:alive" in datas(kb2)
    _, kb3 = guide.command_view(g, TOK, "menu", protected=True)
    assert not any(":cg:" in d for d in datas(kb3))               # menu / help söndürülmür
    _, kb4 = guide.command_view(g, TOK, "alphacmd", off=True)
    assert "Komanda 🔴" in [b.text for b in flat(kb4)]


def test_group_and_home_pagination(tmp_path):
    names = tuple(f"p{i:02d}" for i in range(12)) + ("system",)
    ub = FakeUB(tmp_path, names=names, extra={})
    g = guide.build(ub)
    t0, k0 = guide.home_view(g, TOK, 0)
    t1, k1 = guide.home_view(g, TOK, 1)
    assert "Necə işləyir" in t0 and "Necə işləyir" not in t1        # giriş yalnız 1-ci səhifədə
    assert "1/" in [b.text for b in flat(k0)][-5:][0] or any("/" in b.text for b in flat(k0))
    assert f"ubp:{TOK}:gp:{guide.ALL}:0" in datas(k0)
    ta, ka = guide.group_view(g, TOK, guide.ALL, 1)                 # əlifba sırası: bütün komandalar səhifələnir
    assert "2/" in " ".join(b.text for b in flat(ka))
    assert guide.group_view(g, TOK, "yoxdur")[0].startswith("<i>🏠 › 📖")   # bilinməyən qrup → ana səhifə


def test_long_help_is_trimmed_on_a_paragraph_boundary():
    long = "\n\n".join(f"<b>Bölmə {i}</b>\n" + "x" * 400 for i in range(20))
    out = guide._fit(long)
    assert len(out) <= guide.LIMIT + 120 and out.count("<b>") == out.count("</b>") and "davamı" in out
    assert guide._fit("qısa") == "qısa"


def test_text_guide_lists_every_command(ub):
    t = guide.text_view(guide.build(ub))
    assert ".alphacmd" in t and ".alive" in t and "/depo add" in t and "🏠 Əsas" in t


# ───────────── bütün həqiqi komandalar üçün bələdçi ─────────────
def _real_commands():
    out = []
    for f in sorted(glob.glob("userbot/plugins/[a-z]*.py")):
        for n in ast.walk(ast.parse(open(f, encoding="utf-8").read())):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "command"
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "ub" and n.args
                    and isinstance(n.args[0], ast.Constant)):
                kw = {k.arg: k.value for k in n.keywords}
                pat = kw["pattern"].value if isinstance(kw.get("pattern"), ast.Constant) else None
                out.append((f.split("/")[-1], n.args[0].value, "help" in kw, pat))
    return out


def test_every_real_userbot_command_has_help_for_the_guide():
    cmds = _real_commands()
    assert len(cmds) > 80
    assert [(f, n) for f, n, has, _ in cmds if not has] == [], "help=(qısa, ətraflı) olmayan komanda — bələdçidə boş qalar"


def test_runnable_commands_exist_and_match_when_typed_bare():
    real = {n: p for _, n, _, p in _real_commands()}
    for name in guide.RUNNABLE:
        assert name in real, f"{name} komandası yoxdur"
        assert re.match(real[name] if real[name] is not None else rf"^\.{name}$", f".{name}"), name
    risky = {"ban", "kick", "purge", "purgeme", "clear", "sh", "bash", "restore", "uninstall", "install", "reload",
             "clearfilters", "stopfilter", "delusername", "delpfp", "block", "tagall", "unmute", "mute", "invite"}
    assert not risky & set(guide.RUNNABLE)


# ───────────── callback-lər ─────────────
class FakeDP:
    def __init__(self):
        self.handlers = []

    def callback_query(self, _f):
        def deco(fn):
            self.handlers.append(fn)
            return fn
        return deco


class FakeCB:
    def __init__(self, data, uid=1):
        self.data, self.from_user, self.inline_message_id = data, types.SimpleNamespace(id=uid), "iid"
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def click(ub, data, uid=1):
    dp = FakeDP()
    pm.setup(ub, dp)
    cb = FakeCB(data, uid)
    asyncio.run(dp.handlers[0](cb))
    return cb


def test_callbacks_toggle_reload_delete_and_close(ub, tmp_path):
    cb = click(ub, f"ubp:{TOK}:t:alpha")
    assert "alpha" not in ub.plugin_files and "🔴 alpha söndürüldü" in ub.edits[-1][0]
    assert cb.answers[-1] == (None, False)
    click(ub, f"ubp:{TOK}:t:alpha")
    assert "alpha" in ub.plugin_files
    click(ub, f"ubp:{TOK}:d:alpha")
    assert "silinsin" in ub.edits[-1][0] and (tmp_path / "alpha.py").exists()      # təsdiqdən əvvəl silinmir
    click(ub, f"ubp:{TOK}:dy:alpha")
    assert not (tmp_path / "alpha.py").exists() and "🗑 alpha.py silindi" in ub.edits[-1][0]
    assert "Yüklü" in ub.edits[-1][0]                                              # silindikdən sonra siyahıya qayıdır
    cb = click(ub, f"ubp:{TOK}:t:system")
    assert cb.answers[-1][1] is True and "system" in ub.plugin_files               # xəta alert kimi göstərilir
    click(ub, f"ubp:{TOK}:close")
    assert "bağlandı" in ub.edits[-1][0]


def test_navigation_callbacks_open_every_page(ub):
    for act, marker in (("main", "Userbot menyusu"), ("pl", "Userbot pluginləri"), ("pl:0", "Userbot pluginləri"),
                        ("p:alpha", "alpha.py"), ("cs:alpha:0", "komandalar"), ("fn", "Userbot funksiyaları"),
                        ("qk", "bütün komandalar"), ("qk:0", "bütün komandalar"), ("qs", "Sürətli əmrlər"),
                        ("qg:alpha:0", "alpha"), ("qc:alive", "alive"), ("qc:ban", "ban"), ("g", "Userbot bələdçisi"), ("gp:alpha:0", "alpha"),
                        (f"gp:{guide.ALL}:0", "Əlifba"), ("gc:alive", "alive"), ("new", "Yeni plugin")):
        click(ub, f"ubp:{TOK}:{act}")
        assert marker in ub.edits[-1][0], act


def test_command_switch_from_commands_page_and_from_guide(ub):
    click(ub, f"ubp:{TOK}:ct:alpha:0:ban")
    assert pm.cmd_off(ub) == {"ban"} and "🔴 .ban söndürüldü" in ub.edits[-1][0]
    assert "ban 🔴" in [b.text for b in flat(ub.edits[-1][1])] or ".ban 🔴" in [b.text for b in flat(ub.edits[-1][1])]
    click(ub, f"ubp:{TOK}:cg:ban")
    assert pm.cmd_off(ub) == set() and "🟢 .ban yandırıldı" in ub.edits[-1][0]
    cb = click(ub, f"ubp:{TOK}:ct:alpha:0:menu")
    assert cb.answers[-1][1] is True and pm.cmd_off(ub) == set()                   # qorunan komanda


def test_reset_callback(ub):
    pm.note_use(ub, "alpha", "ping")
    click(ub, f"ubp:{TOK}:z:alpha")
    assert pm.usage(ub).get("alpha") is None and "Sayğac sıfırlandı" in ub.edits[-1][0]


def test_only_creator_can_use_callbacks(ub):
    cb = click(ub, f"ubp:{TOK}:t:alpha", uid=99)
    assert "alpha" in ub.plugin_files and cb.answers[-1][1] is True and not ub.edits


def test_start_views_for_menu_and_help(ub):
    dp = FakeDP()
    pm.setup(ub, dp)
    assert "Userbot menyusu" in ub.state["plugin_menu_view"](TOK)[0]
    assert "Userbot bələdçisi" in ub.state["plugin_menu_view"](TOK, "g")[0]
    assert "🟢 alpha" in ub.state["plugin_menu_text"]() and "🏠 Əsas" in ub.state["plugin_guide_text"]()


# ───────────── çağırış sayğacı ─────────────
def test_counter_counts_per_plugin_and_command_and_resets(ub):
    for _ in range(3):
        pm.note_use(ub, "alpha", "ping")
    pm.note_use(ub, "alpha", "ff")
    pm.note_use(ub, "beta", "x")
    pm.note_use(ub, None, "menu")                                # sahibsiz (host) komandalar sayılmır
    u = pm.usage(ub)
    assert u["alpha"]["n"] == 4 and u["alpha"]["cmds"] == {"ping": 3, "ff": 1} and u["beta"]["n"] == 1 and "_since" in u
    snap = {p["name"]: p for p in pm.snapshot(ub)}
    assert snap["alpha"]["uses"]["n"] == 4 and snap["system"]["uses"]["n"] == 0
    pm.reset_usage(ub, "alpha")
    assert "alpha" not in pm.usage(ub) and pm.usage(ub)["beta"]["n"] == 1
    pm.reset_usage(ub)
    assert pm.usage(ub) == {}


def test_views_show_counts_top_commands_and_idle(ub):
    pm.note_use(ub, "alpha", "ping")
    pm.note_use(ub, "alpha", "ping")
    pm.note_use(ub, "alpha", "ff")
    snap = pm.snapshot(ub)
    lst = pm.list_view(snap, TOK)[0]
    assert "alpha 3" in lst and "Heç işlənməyib: 2 plugin" in lst         # system, beta
    det, kb = pm.detail_view(snap, TOK, "alpha")
    assert "Çağırış: <b>3</b>" in det and ".ping</code> 2" in det
    assert f"ubp:{TOK}:z:alpha" in datas(kb)
    check_rules(det, kb)
    assert f"ubp:{TOK}:z:beta" not in datas(pm.detail_view(snap, TOK, "beta")[1])


def test_no_counter_text_before_first_command(ub):
    assert "📊 Sayğac" not in pm.list_view(pm.snapshot(ub), TOK)[0]
    assert "Sayğac hələ işləmir" in pm.detail_view(pm.snapshot(ub), TOK, "alpha")[0]


def test_real_registry_every_loaded_command_is_in_the_guide(monkeypatch):
    """Həqiqi pluginləri yükləyir (db saxta): hər komandanın izahı var, səhifələr Telegram limitinə sığır."""
    import sys
    from unittest.mock import MagicMock
    import userbot.core.userbot_api as api
    from userbot.core import host
    monkeypatch.setattr(api, "get_udb", lambda: FakeDB())
    saved = {k: v for k, v in sys.modules.items() if k.startswith("userbot.plugins.")}
    try:
        real = api.UB(MagicMock(creator_id=1))
        for key in ("acc", "bot", "panel"):
            real.help_category(key, key, key)
        loaded = host.load_command_plugins(real)
        assert len(loaded) >= 14 and len(real.commands) >= 88
        groups = guide.build(real)
        listed = {e["name"] for v in groups.values() for e in v}
        assert {c["name"] for c in real.commands} <= listed
        blank = [e["name"] for v in groups.values() for e in v if not e["long"] or e["short"] == "təsvir yoxdur"]
        assert blank == [], f"izahı boş komandalar: {blank}"
        for e in (e for v in groups.values() for e in v):
            text, kb = guide.command_view(groups, TOK, e["name"], ran_ok=guide.runnable(real, e["name"]))
            assert len(text) < 4000 and all(len(d.encode()) <= 64 for d in datas(kb)), e["name"]
        for gid in groups:
            assert len(guide.group_view(groups, TOK, gid)[0]) < 4000
        assert len(guide.home_view(groups, TOK)[0]) < 4000
        assert [n for n in guide.RUNNABLE if not guide.runnable(real, n)] == []
        # 🧰 katalog: hər həqiqi komanda düymədir, rejimlər məntiqlidir
        modes = {c["name"]: cmds.info(real, c["name"])["mode"] for c in real.commands}
        assert len(modes) >= 88 and set(modes.values()) <= {"safe", "input", "bare", "none"}
        assert sorted(n for n, m in modes.items() if m == "safe") == sorted(guide.RUNNABLE)
        assert [n for n, m in modes.items() if m == "none"] == ["bmenu"]               # yalnız menyu açan komanda
        assert {"ban", "purge", "sh", "kickme", "restore", "install", "clearfilters"} <= {
            n for n in modes if cmds.info(real, n)["confirm"]}                          # təsir edənlər təsdiq tələb edir
        assert not {n for n in modes if cmds.info(real, n)["confirm"]} & set(guide.RUNNABLE)
        seen = []
        for gid in cmds.command_groups(groups):
            for page in range(0, 6):
                text, kb = cmds.group_view(real, groups, TOK, gid, page)
                assert len(text) < 4000
                check_rules(text, kb)
                seen += [d.rsplit(":", 1)[1] for d in datas(kb) if ":qc:" in d]
        assert set(seen) == set(modes)
        for name in modes:
            text, kb = cmds.command_view(real, groups, TOK, name)
            assert len(text) < 4000 and all(len(d.encode()) <= 64 for d in datas(kb)), name
    finally:
        for k in [k for k in sys.modules if k.startswith("userbot.plugins.")]:
            sys.modules.pop(k, None)
        sys.modules.update(saved)


# ───────────── 🧰 Bütün komandalar düymə kimi: rejimlər, katalog, ✍️ arqument ─────────────
from userbot.core import plugin_cmds as cmds  # noqa: E402


def with_args(ub, name, pattern=None):
    """Komandanı arqument qəbul edən pattern ilə əvəzləyir (həqiqi pluginlərdəki kimi)."""
    arm(ub, name, ub._fn(name), pattern or rf"^\.{name}(?:\s+(.+))?$")
    return ub


def test_command_modes_are_derived_from_the_pattern(ub):
    with_args(ub, "ban")
    arm(ub, "ban", ub._fn("ban"))                                 # sonra yenə də bare-only
    with_args(ub, "ban")
    ub.commands.append({"name": "info", "pattern": r"^\.info(?:\s+(.+))?$", "owner": "alpha", "fn": None})
    ub.commands.append({"name": "uninstall", "pattern": r"^\.uninstall\s+(\S+)$", "owner": "system", "fn": None})
    got = {n: cmds.info(ub, n) for n in ("alive", "ban", "alphacmd", "info", "uninstall", "menu", "help", "ghost")}
    assert got["alive"]["mode"] == "safe" and not got["alive"]["confirm"]
    assert got["ban"] == {"name": "ban", "mode": "input", "bare_ok": True, "takes_args": True, "confirm": True}
    assert got["alphacmd"]["mode"] == "bare" and got["alphacmd"]["confirm"]              # arqumentsiz, təsir edə bilər
    assert got["info"]["mode"] == "input" and not got["info"]["confirm"]               # oxuyur → təsdiqsiz
    assert got["uninstall"]["mode"] == "input" and not got["uninstall"]["bare_ok"]
    assert got["menu"]["mode"] == got["help"]["mode"] == got["ghost"]["mode"] == "none"
    assert cmds.compose("ban") == ".ban" and cmds.compose("ban", "@x spam") == ".ban @x spam"
    assert cmds.matches(ub, "uninstall", ".uninstall beta") and not cmds.matches(ub, "uninstall", ".uninstall")


def test_catalogue_lists_every_command_exactly_once_and_skips_bot_and_panel_groups(ub):
    groups = guide.build(ub)
    text, kb = cmds.home_view(ub, groups, TOK)
    assert "Cəmi: <b>7</b>" in text and cmds.LEGEND in text                               # 2 host + 3 plugin cmd + alive + ban
    assert datas(kb)[0] == f"ubp:{TOK}:qs"                                                # ⚡ Sürətli ilk
    assert f"ubp:{TOK}:qg:{guide.BOT}:0" not in datas(kb) and f"ubp:{TOK}:qg:{guide.PANEL}:0" not in datas(kb)
    seen = []
    for gid in cmds.command_groups(groups):
        t, k = cmds.group_view(ub, groups, TOK, gid)
        seen += [d.rsplit(":", 1)[1] for d in datas(k) if ":qc:" in d]
    assert sorted(seen) == sorted(c["name"] for c in ub.commands)                         # hamısı düymədir, təkrarsız


def test_catalogue_pages_paginate_and_follow_panel_rules(tmp_path):
    ub = FakeUB(tmp_path, names=("system", "big"), extra={"big": [f"c{i:02d}" for i in range(25)]})
    groups = guide.build(ub)
    t0, k0 = cmds.group_view(ub, groups, TOK, "big", 0)
    t2, k2 = cmds.group_view(ub, groups, TOK, "big", 2)
    assert "3/3" in [b.text for b in flat(k2)] and len([d for d in datas(k0) if ":qc:" in d]) == 10
    assert "▶️ birbaşa" in t0 and "⚙️" in t0                                               # rejim izahı və işarələr
    for text, kb in (cmds.home_view(ub, groups, TOK), (t0, k0), (t2, k2), cmds.group_view(ub, groups, TOK, "nope")):
        check_rules(text, kb)
    assert cmds.group_view(ub, groups, TOK, "nope")[0].startswith("<i>🏠 › 🧩 Userbot › Əmrlər</i>")   # bilinməyən qrup → ana


def test_command_page_offers_buttons_by_mode(ub):
    with_args(ub, "ban")
    groups = guide.build(ub)
    alive, ban, plain = (cmds.command_view(ub, groups, TOK, n) for n in ("alive", "ban", "alphacmd"))
    assert f"ubp:{TOK}:run:alive" in datas(alive[1]) and "Birbaşa işləyir" in alive[0]
    assert f"ubp:{TOK}:in:ban" in datas(ban[1]) and f"ubp:{TOK}:run:ban" in datas(ban[1]) and "Arqument alır" in ban[0]
    assert "təsdiq soruşulur" in ban[0]
    assert f"ubp:{TOK}:run:alphacmd" in datas(plain[1]) and f"ubp:{TOK}:in:alphacmd" not in datas(plain[1])
    menu = cmds.command_view(ub, groups, TOK, "menu")
    assert not any(":run:" in d or ":in:" in d for d in datas(menu[1])) and "menyudan işləmir" in menu[0]
    off = cmds.command_view(ub, groups, TOK, "ban", off=True)
    assert not any(":run:" in d or ":in:" in d for d in datas(off[1])) and "Söndürülüb" in off[0]
    for text, kb in (alive, ban, plain, menu, off):
        check_rules(text, kb)
    assert f"ubp:{TOK}:qg:alpha:0" in datas(ban[1])                                       # ⬅️ komandanın qrupu


def test_effectful_bare_command_asks_for_confirmation_first(live):
    async def fn(event):
        await event.edit("DAYANDI")
    arm(live, "alphacmd", fn)
    click(live, f"ubp:{TOK}:run:alphacmd")
    text, kb = live.edits[-1]
    assert "İşlədilsin?" in text and "<code>.alphacmd</code>" in text and "DAYANDI" not in text
    assert datas(kb) == [f"ubp:{TOK}:ry:alphacmd", f"ubp:{TOK}:qc:alphacmd"]            # hələ işləməyib
    click(live, f"ubp:{TOK}:ry:alphacmd")
    assert live.edits[-1][0] == "DAYANDI"


class Msg:
    def __init__(self, chat_id=42, text="@ali spam", reply=None):
        self.chat_id, self.raw_text, self.reply_to_msg_id, self.deleted = chat_id, text, reply, False

    async def delete(self):
        self.deleted = True


class MsgClient(NoSendClient):
    async def get_messages(self, chat_id, ids=None, limit=None):
        return SimpleReply(ids) if ids else [types.SimpleNamespace(id=900)]


def SimpleReply(mid):
    return types.SimpleNamespace(id=mid, sender_id=555)


def test_arguments_flow_prompt_then_confirm_then_run_with_reply(live):
    live.client = MsgClient()
    seen = {}

    async def ban(event):
        seen.update(arg=event.pattern_match.group(1), reply=event.is_reply, rid=event.reply_to_msg_id,
                    rm=(await event.get_reply_message()).sender_id, cid=event.id, chat=event.chat_id, text=event.text)
        await event.edit("BANLANDI")
    with_args(live, "ban")
    arm(live, "ban", ban, pattern=r"^\.ban(?:\s+(.+))?$")
    click(live, f"ubp:{TOK}:in:ban")
    prompt = live.edits[-1][0]
    assert "✍️ <b>.ban</b> üçün arqument" in prompt and "<b>bu çata</b>" in prompt and "reply" in prompt
    assert datas(live.edits[-1][1]) == [f"ubp:{TOK}:qc:ban"] and live.state["_await"]["chat_id"] == 42
    msg = Msg(text="@ali spam", reply=77)
    asyncio.run(live.state["plugin_menu_input"](msg))                       # növbəti mesajınız
    assert msg.deleted and "_await" not in live.state                        # mesaj silinir, gözləmə bitir
    text, kb = live.edits[-1]
    assert "İşlədilsin?" in text and "<code>.ban @ali spam</code>" in text and "<b>reply</b> ilə" in text
    assert datas(kb) == [f"ubp:{TOK}:rp", f"ubp:{TOK}:qc:ban"] and seen == {}              # təsdiqdən əvvəl işləmir
    cb = click(live, f"ubp:{TOK}:rp")
    assert live.edits[-1][0] == "BANLANDI" and cb.answers[0] == ("▶️ .ban", False)
    assert seen == {"arg": "@ali spam", "reply": True, "rid": 77, "rm": 555, "cid": 900, "chat": 42, "text": ".ban @ali spam"}
    click(live, f"ubp:{TOK}:rp")
    assert "vaxtı keçib" in live.edits[-1][0] or live.edits[-1][0] == "BANLANDI"
    assert "_pending" in live.state and TOK not in live.state["_pending"]


def test_light_commands_run_right_after_the_argument(live):
    live.client = MsgClient()

    async def info(event):
        await event.edit(f"INFO {event.pattern_match.group(1)}")
    live.commands.append({"name": "info", "pattern": r"^\.info(?:\s+(.+))?$", "owner": "alpha", "fn": info})
    live.help["info"] = (".info", "məlumat", "uzun", "acc")
    click(live, f"ubp:{TOK}:in:info")
    asyncio.run(live.state["plugin_menu_input"](Msg(text="@ali")))
    assert live.edits[-1][0] == "INFO @ali"                                  # LIGHT — təsdiq yoxdur
    click(live, f"ubp:{TOK}:in:info")
    asyncio.run(live.state["plugin_menu_input"](Msg(text=".")))              # nöqtə = arqumentsiz
    assert live.edits[-1][0] == "INFO None"


def test_argument_wait_is_scoped_to_chat_time_and_other_commands(live):
    with_args(live, "ban")
    on_input = live.state["plugin_menu_input"] if "plugin_menu_input" in live.state else None
    click(live, f"ubp:{TOK}:in:ban")
    on_input = live.state["plugin_menu_input"]
    other = Msg(chat_id=999)
    asyncio.run(on_input(other))
    assert not other.deleted and "_await" in live.state                       # başqa çat — toxunulmur
    typed = Msg(text=".id")
    asyncio.run(on_input(typed))
    assert not typed.deleted and "_await" not in live.state                   # başqa komanda yazdınız — ləğv
    click(live, f"ubp:{TOK}:in:ban")
    live.state["_await"]["ts"] -= pm.AWAIT_TTL + 1
    late = Msg()
    asyncio.run(on_input(late))
    assert not late.deleted and "_await" not in live.state                    # vaxtı keçib
    n = len(live.edits)
    asyncio.run(on_input(Msg()))
    assert len(live.edits) == n                                               # gözləmə yoxdur — hadisə nəzərə alınmır


def test_leaving_the_prompt_page_cancels_the_wait_and_bad_format_is_reported(live):
    live.commands.append({"name": "uninstall", "pattern": r"^\.uninstall\s+(\S+)$", "owner": "system", "fn": None})
    live.help["uninstall"] = (".uninstall", "sil", "uzun", "acc")
    click(live, f"ubp:{TOK}:in:uninstall")
    assert "_await" in live.state
    click(live, f"ubp:{TOK}:qc:uninstall")
    assert "_await" not in live.state                                          # ✖️ Ləğv et / başqa səhifə
    click(live, f"ubp:{TOK}:in:uninstall")
    asyncio.run(live.state["plugin_menu_input"](Msg(text="a b")))               # \S+$ — boşluqlu uyğun gəlmir
    assert "format uyğun deyil" in live.edits[-1][0].lower() and "_pending" not in live.state or not live.state.get("_pending")
    cb = click(live, f"ubp:{TOK}:run:uninstall")                               # arqumentsiz işləmir
    assert cb.answers[-1][1] is True and "arqumentsiz" in cb.answers[-1][0]
    cb = click(live, f"ubp:{TOK}:in:alphacmd")                                 # arqument almayan komanda
    assert cb.answers[-1][1] is True and "arqument almır" in cb.answers[-1][0]


def test_panel_runs_are_never_deduplicated_by_the_gate(ub):
    ev = types.SimpleNamespace(chat_id=5, id=900, panel=object())
    assert pm.gate(ub, "alpha", "alive", ev) and pm.gate(ub, "alpha", "alive", ev)     # eyni id, iki basış — ikisi də işləyir
    real = types.SimpleNamespace(chat_id=5, id=900)
    assert pm.gate(ub, "alpha", "alive", real) and not pm.gate(ub, "alpha", "alive", real)


def test_functions_page_links_to_the_catalogue(ub):
    text, kb = pm.functions_view(ub, TOK)
    assert "Bütün komandalar" in text and f"ubp:{TOK}:qk" in datas(kb)


# ───────────── 🎨 botun kod bloku görünüşü ilə əlaqə ─────────────
from userbot.core import plugin_style as pstyle  # noqa: E402


class FakeSettings:
    def __init__(self, **kw):
        self.d = dict(kw)

    def get_setting(self, key, default=None):
        return self.d.get(key, default)

    def set_setting(self, key, value):
        self.d[key] = value


@pytest.fixture
def botui(monkeypatch):
    """Botun /ui ayarları: compact_ui_plugin və common.menu eyni saxta bazanı oxuyur."""
    import common.database as cdb
    import common.menu as cmenu
    from bot.plugins import compact_ui_plugin as cui
    db = FakeSettings()
    monkeypatch.setattr(cui, "get_db", lambda: db)
    monkeypatch.setattr(cdb, "get_db", lambda: db)
    monkeypatch.setattr(cmenu, "_UI_STYLE_OVERRIDE", None)
    monkeypatch.setattr(cmenu, "_UI_BAR_OVERRIDE", None)
    return db


def test_apply_puts_the_first_message_into_the_same_code_block_as_the_bots_panels(botui, ub):
    text, kb = pm.hub_view(ub, TOK, pm.snapshot(ub))
    out, kb2 = pstyle.apply(text, kb)
    assert out.startswith("<b>🏠 › 🧩 Userbot</b>\n<pre>🧩 Userbot menyusu") and out.endswith("</pre>")
    assert [b.text for b in flat(kb2)][-2:] == ["🔄", "✖️"]                                  # yığcam naviqasiya
    assert datas(kb2) == datas(kb)                                                          # callback-lər dəyişmir
    botui.set_setting("ui_style", "quote")
    assert "<blockquote><code>" in pstyle.apply(text, kb)[0]
    botui.set_setting("ui_style", "script")
    assert "<blockquote expandable><code>" in pstyle.apply(text, kb)[0]


def test_apply_leaves_everything_alone_when_the_bot_has_it_switched_off(botui, ub):
    botui.set_setting("ui_box", "0")
    botui.set_setting("ui_kb", "0")
    text, kb = pm.hub_view(ub, TOK, pm.snapshot(ub))
    out, kb2 = pstyle.apply(text, kb)
    assert out == text and [b.text for b in flat(kb2)] == [b.text for b in flat(kb)]
    assert pstyle.apply("yalnız mətn")[0] == "yalnız mətn" and pstyle.apply("x", None)[1] is None


def test_apply_text_is_boxed_by_the_same_setting(botui):
    out = pstyle.apply_text("🧩 Userbot pluginləri\n\n🟢 alpha — .alphacmd")
    assert out.startswith("<b>🧩 Userbot pluginləri</b>\n<pre>") and "alpha" in out
    botui.set_setting("ui_box", "0")
    assert pstyle.apply_text("🧩 Userbot pluginləri\n\nx") == "🧩 Userbot pluginləri\n\nx"


def test_style_page_mirrors_the_bots_settings_and_follows_panel_rules(botui):
    text, kb = pstyle.view(TOK)
    check_rules(text, kb)
    assert "Kod bloku: 🟢 aktiv" in text and "Üslub: 📋 Kod bloku" in text and "Yığcam naviqasiya: 🟢 aktiv" in text
    assert "botun /ui paneli" in text and "STATUS  ● alive" in text
    assert datas(kb)[:4] == [f"ubp:{TOK}:uit:box", f"ubp:{TOK}:uit:kb", f"ubp:{TOK}:uis", f"ubp:{TOK}:uib"]
    botui.set_setting("ui_box", "0")
    assert "Kod bloku: 🔴 deaktiv" in pstyle.view(TOK)[0] and "📋 Kod bloku 🔴" in [b.text for b in flat(pstyle.view(TOK)[1])]
    for text, kb in (pstyle.style_picker(TOK), pstyle.bar_picker(TOK)):
        check_rules(text, kb)
        assert sum(b.text.startswith("✅") for b in flat(kb)) == 1                           # cari seçim işarəli


def test_style_callbacks_change_the_bots_settings(botui, ub):
    from common.menu import get_ui_bar, get_ui_style
    click(ub, f"ubp:{TOK}:ui")
    assert "Görünüş" in ub.edits[-1][0]
    click(ub, f"ubp:{TOK}:uit:box")
    assert botui.d["ui_box"] == "0" and "📋 Kod bloku: 🔴 deaktiv" in ub.edits[-1][0]          # botun /ui ayarı dəyişdi
    click(ub, f"ubp:{TOK}:uit:box")
    assert botui.d["ui_box"] == "1"
    click(ub, f"ubp:{TOK}:uit:kb")
    assert botui.d["ui_kb"] == "0"
    click(ub, f"ubp:{TOK}:uis")
    assert "Kod bloku üslubu" in ub.edits[-1][0]
    click(ub, f"ubp:{TOK}:uis:quote")
    assert get_ui_style() == "quote" and botui.d["ui_style"] == "quote" and "Üslub: 💬 Sitat" in ub.edits[-1][0]
    assert "✅ 💬 Sitat" in [b.text for b in flat(ub.edits[-1][1])]
    click(ub, f"ubp:{TOK}:uib:block")
    assert get_ui_bar() == "block" and "Bar üslubu" in ub.edits[-1][0]
    click(ub, f"ubp:{TOK}:uis:yoxdur")
    assert get_ui_style() == "quote" and "Naməlum" in ub.edits[-1][0]
    click(ub, f"ubp:{TOK}:uit:nope")                                                          # naməlum açar — ayara toxunmur
    assert botui.d["ui_kb"] == "0" and "Userbot menyusu" in ub.edits[-1][0]


def test_hub_and_functions_page_lead_to_the_style_page(ub):
    assert f"ubp:{TOK}:ui" in datas(pm.hub_view(ub, TOK, pm.snapshot(ub))[1])
    text, kb = pm.functions_view(ub, TOK)
    assert f"ubp:{TOK}:ui" in datas(kb) and "🎨 Görünüş" in text
    assert not any(d.startswith("ubm:") and d.endswith(":ui") for d in datas(kb))             # köhnə host ekranı yox


def test_host_inline_results_use_the_bots_code_block_view(botui, monkeypatch):
    """host.setup-un həqiqi inline handler-i: .menu / .help / kart nəticəsi ilk mesajdan kod blokunda gəlir."""
    import sys
    from unittest.mock import MagicMock
    import userbot.core.userbot_api as api
    from userbot.core import host
    monkeypatch.setattr(api, "get_udb", lambda: FakeDB())

    def no_loop(coro):                                  # host qoşulma tapşırığını yaratmağa çalışır; loop yoxdur
        coro.close()
        raise RuntimeError("test")
    monkeypatch.setattr(host.asyncio, "create_task", no_loop)
    saved = {k: v for k, v in sys.modules.items() if k.startswith("userbot.plugins.")}

    class DP:
        def __init__(self):
            self.handlers = {}

        def __getattr__(self, kind):
            def make(*flt):
                def deco(fn):
                    self.handlers.setdefault(kind, []).append(fn)
                    return fn
                return deco
            return make
    try:
        dp = DP()
        ctx = MagicMock(creator_id=1, dp=dp)
        host.setup(ctx)
        inline = dp.handlers["inline_query"][0]
        answers = []

        class Q:
            from_user = types.SimpleNamespace(id=1)

            def __init__(self, query):
                self.query = query

            async def answer(self, results, **kw):
                answers.append(results)
        for query, marker in (("ub:pm:aaa111", "Userbot menyusu"), ("ub:pg:bbb222", "Userbot bələdçisi")):
            asyncio.run(inline(Q(query)))
            res = answers[-1][0]
            msg, markup = res.input_message_content.message_text, res.reply_markup
            assert msg.startswith("<b>🏠 › ") and "<pre>" in msg and marker in msg
            assert [b.text for r in markup.inline_keyboard for b in r][-1] == "✖️"            # yığcam naviqasiya
        botui.set_setting("ui_box", "0")
        asyncio.run(inline(Q("ub:pm:ccc333")))
        assert answers[-1][0].input_message_content.message_text.startswith("<i>🏠 › 🧩 Userbot</i>")
        card = types.SimpleNamespace(text="Kart başlığı\nsətir", kb=lambda: pm.ui.kb([pm.ui.btn("🔄 Yenilə", "ubc:t:r")]))
        ctx.userbot.cards["card99"] = card
        botui.set_setting("ui_box", "1")
        asyncio.run(inline(Q("ub:card:card99")))
        res = answers[-1][0]
        assert res.input_message_content.message_text.startswith("<b>Kart başlığı</b>\n<pre>")
        assert res.reply_markup.inline_keyboard[0][0].text == "🔄"
    finally:
        for k in [k for k in sys.modules if k.startswith("userbot.plugins.")]:
            sys.modules.pop(k, None)
        sys.modules.update(saved)


# ───────────── 💬 komanda cavab mesajını göstər / gizlət ─────────────
@pytest.fixture(autouse=True)
def _clean_quiet():
    from userbot.core import userbot_api as api
    api.QUIET.clear()
    yield
    api.QUIET.clear()


class QEvent:
    """Telethon hadisəsinin kiçik surəti: edit / delete çağırışları yazılır."""

    def __init__(self, chat_id=5, mid=10):
        self.chat_id, self.id, self.edits, self.deleted = chat_id, mid, [], 0

    async def edit(self, text, **kw):
        self.edits.append(text)

    async def delete(self):
        self.deleted += 1


def test_quiet_setting_is_stored_per_command_and_host_commands_are_refused(ub):
    assert pm.set_quiet(ub, "ban", True) == (True, ".ban: cavab mesajı 🔴 gizlədilib")
    assert pm.quiet_set(ub) == {"ban"}
    assert pm.set_quiet(ub, "ban", False)[1].endswith("🟢 göstərilir") and pm.quiet_set(ub) == set()
    assert not pm.set_quiet(ub, "menu", True)[0] and not pm.set_quiet(ub, "ghost", True)[0]


def test_quiet_start_marks_only_listed_real_messages(ub):
    from userbot.core import userbot_api as api
    ev = QEvent()
    assert pm.quiet_start(ub, "ban", ev) == "" and not api.QUIET            # siyahıda yoxdur
    pm.set_quiet(ub, "ban", True)
    assert pm.quiet_start(ub, "ban", ev) == "early" and (5, 10) in api.QUIET
    assert pm.quiet_start(ub, "alive", QEvent(mid=11)) == ""
    assert pm.quiet_start(ub, "ban", types.SimpleNamespace(chat_id=5, id=12, panel=object())) == ""   # menyudan işləyəndə yox
    assert pm.quiet_start(ub, "ban", types.SimpleNamespace()) == ""
    pm.set_quiet(ub, "info", True) if any(c["name"] == "info" for c in ub.commands) else ub.db.kv_set(pm.NS, pm.QUIET_KEY, ["ban", "info"])
    assert pm.quiet_start(ub, "info", QEvent(mid=13)) == "late"                                      # reply atan komanda
    for i in range(600):
        pm.quiet_start(ub, "ban", QEvent(mid=100 + i))
    assert len(api.QUIET) <= 500


def test_safe_edit_stays_silent_for_a_hidden_commands_message():
    from userbot.core import userbot_api as api
    hidden, shown = QEvent(mid=1), QEvent(mid=2)
    api.QUIET[(5, 1)] = 1
    asyncio.run(api.safe_edit(hidden, "mesajlar toplanır…"))
    asyncio.run(api.safe_edit(shown, "mesajlar toplanır…"))
    assert hidden.edits == [] and shown.edits == ["mesajlar toplanır…"]


def test_cards_are_not_opened_for_a_hidden_command(ub):
    from userbot.core import userbot_api as api
    ub.cards, ub.bot = {}, types.SimpleNamespace()
    ub.tr = lambda t, **kw: t
    ev = QEvent(mid=3)
    ev.client = None
    api.QUIET[(5, 3)] = 1
    out = asyncio.run(api.Out(ub, ev).open("kart", []))
    assert out.closed and not ub.cards and ev.edits == []


def real_wrapped(ub_cls_state, name="purgeme"):
    from userbot.core.userbot_api import UB, safe_edit
    ub = UB.__new__(UB)
    ub.commands, ub.help, ub.help_owner, ub._loading, ub.db, ub.state = [], {}, {}, "chat_tools", FakeDB(), {}
    calls = ub_cls_state.setdefault("calls", [])

    @UB.command(ub, name)
    async def handler(event):
        calls.append(f"start(deleted={event.deleted})")
        await safe_edit(event, "mesajlar toplanır…")
        calls.append("ran")
        if ub_cls_state.get("boom"):
            raise RuntimeError("xəta")
    return ub, ub.commands[0]["fn"], calls


def test_wrapped_command_hides_status_text_and_deletes_its_message_when_done():
    ub, fn, calls = real_wrapped({})
    shown = QEvent(mid=20)
    asyncio.run(fn(shown))
    assert shown.edits == ["mesajlar toplanır…"] and shown.deleted == 0 and calls == ["start(deleted=0)", "ran"]   # standart: göstərilir
    pm.set_quiet(types.SimpleNamespace(commands=[{"name": "purgeme", "owner": "chat_tools"}], db=ub.db), "purgeme", True)
    hidden = QEvent(mid=21)
    asyncio.run(fn(hidden))
    assert hidden.edits == [] and hidden.deleted == 1
    assert calls[2:] == ["start(deleted=1)", "ran"]            # komanda mesajı handler başlamazdan ƏVVƏL silinib — gec yox


def test_hidden_command_message_is_deleted_even_if_the_command_fails():
    ub, fn, calls = real_wrapped({"boom": True})
    pm.set_quiet(types.SimpleNamespace(commands=[{"name": "purgeme", "owner": "chat_tools"}], db=ub.db), "purgeme", True)
    ev = QEvent(mid=30)
    with pytest.raises(RuntimeError):
        asyncio.run(fn(ev))
    assert ev.edits == [] and ev.deleted == 1


def test_commands_that_reply_to_their_own_message_delete_it_only_at_the_end():
    ub, fn, calls = real_wrapped({}, name="info")
    pm.set_quiet(types.SimpleNamespace(commands=[{"name": "info", "owner": "chat_tools"}], db=ub.db), "info", True)
    ev = QEvent(mid=40)
    asyncio.run(fn(ev))
    assert calls == ["start(deleted=0)", "ran"] and ev.deleted == 1 and ev.edits == []


def test_command_pages_have_the_reply_toggle_but_host_commands_do_not(ub):
    groups = guide.build(ub)
    for text, kb, where in ((*cmds.command_view(ub, groups, TOK, "alive", quiet=False), "q"),
                            (*guide.command_view(groups, TOK, "alive", quiet=False), "g")):
        assert f"ubp:{TOK}:cq:{where}:alive" in datas(kb) and "<b>Cavab mesajı:</b> 🟢 göstərilir" in text
        assert "💬 Cavab 🟢" in [b.text for b in flat(kb)]
        check_rules(text, kb)
    text, kb = cmds.command_view(ub, groups, TOK, "alive", quiet=True)
    assert "🔴 gizlədilib" in text and "💬 Cavab 🔴" in [b.text for b in flat(kb)]
    for host_cmd in ("menu", "help"):
        t, k = cmds.command_view(ub, groups, TOK, host_cmd, quiet=False)
        assert not any(":cq:" in d for d in datas(k)) and "Cavab mesajı" not in t
    assert not any(":cq:" in d for d in datas(cmds.command_view(ub, groups, TOK, "alive")[1]))   # quiet=None → düymə yoxdur


def test_reply_toggle_callbacks_flip_the_setting_and_stay_on_the_same_page(ub):
    click(ub, f"ubp:{TOK}:qc:ban")
    assert "💬 Cavab 🟢" in [b.text for b in flat(ub.edits[-1][1])]
    click(ub, f"ubp:{TOK}:cq:q:ban")
    assert pm.quiet_set(ub) == {"ban"} and "ban: cavab mesajı 🔴 gizlədilib" in ub.edits[-1][0]
    assert f"ubp:{TOK}:qg:alpha:0" in datas(ub.edits[-1][1]) and f"ubp:{TOK}:in:ban" not in datas(ub.edits[-1][1])   # Əmrlər (qc) səhifəsində qalır
    assert "💬 Cavab 🔴" in [b.text for b in flat(ub.edits[-1][1])]
    click(ub, f"ubp:{TOK}:cq:g:ban")
    assert pm.quiet_set(ub) == set() and f"ubp:{TOK}:gp:alpha:0" in datas(ub.edits[-1][1])             # bələdçi (gc) səhifəsində qalır
    cb = click(ub, f"ubp:{TOK}:cq:q:menu")
    assert cb.answers[-1][1] is True and pm.quiet_set(ub) == set()


def test_menu_runs_ignore_the_hide_setting(live):
    pm.set_quiet(live, "alive", True)

    async def fn(event):
        await event.edit("NƏTİCƏ")
    arm(live, "alive", fn)
    asyncio.run(pm.run_direct(live, FakeCB("x"), TOK, "alive"))
    assert live.edits[-1][0] == "NƏTİCƏ"                                                              # menyuda həmişə görünür


# ───────────── ♻️ .restart ─────────────
from userbot.core import userbot_restart as ur  # noqa: E402


@pytest.fixture
def restarter(ub, tmp_path, monkeypatch):
    """ub-ya .restart quraşdırılır; botun restart_plugin-i saxtalaşdırılır (proses əvəzlənməsin)."""
    from bot.plugins import restart_plugin as rp
    calls = {"restart": 0}

    async def fake_restart(ctx):
        calls["restart"] += 1
    monkeypatch.setattr(rp, "restart", fake_restart)
    monkeypatch.setattr(rp, "_STARTED", 0)                                          # uzun müddətdir işləyir
    monkeypatch.setattr(rp, "get_active_operations", lambda ctx: calls.get("active") or {
        "downloads": [], "download_count": 0, "streams": [], "stream_count": 0, "is_busy": False})
    monkeypatch.setattr(ur, "STATE", str(tmp_path / "ur.json"))
    ub.context = types.SimpleNamespace()
    ub.add_help = lambda key, button, short, long, cat="acc": ub.help.__setitem__(key, (button, short, long, cat))
    ur.setup(ub)
    cmd = next(c for c in ub.commands if c["name"] == "restart")
    return ub, cmd, calls


class REvent(QEvent):
    def __init__(self, arg=None, **kw):
        super().__init__(**kw)
        self.pattern_match = re.match(ur.PATTERN, ".restart" + (f" {arg}" if arg else ""))


def run_restart(cmd, arg=None, **kw):
    ev = REvent(arg, **kw)

    async def scenario():
        await cmd["fn"](ev)
        await asyncio.sleep(0.01)                       # ayrı tapşırığa növbə ver
    asyncio.run(scenario())
    return ev


def test_restart_command_is_a_protected_host_command_with_help(restarter):
    ub, cmd, _ = restarter
    assert cmd["owner"] is None and "restart" in pm.PROTECTED_CMDS and not pm.set_cmd(ub, "restart", False)[0]
    assert ub.help["restart"][0] == ".restart" and ".restart now" in ub.help["restart"][2]
    assert re.match(ur.PATTERN, ".reboot") and re.match(ur.PATTERN, ".restart now") and not re.match(ur.PATTERN, ".restartx")
    groups = guide.build(ub)
    assert "restart" in [e["name"] for e in groups[guide.HOST]]                      # bələdçidə 🏠 Əsas qrupunda
    assert cmds.info(ub, "restart")["mode"] == "input" and cmds.info(ub, "restart")["confirm"]   # menyudan: təsdiqlə


def test_restart_runs_immediately_when_nothing_is_busy(restarter, tmp_path):
    ub, cmd, calls = restarter
    ev = run_restart(cmd, chat_id=7, mid=99)
    assert calls["restart"] == 1 and "yenidən başladılır" in ev.edits[-1]
    st = json.loads((tmp_path / "ur.json").read_text())
    assert st["chat"] == 7 and st["msg"] == 99 and "iid" not in st


def test_restart_warns_when_busy_and_force_overrides(restarter):
    ub, cmd, calls = restarter
    calls["active"] = {"downloads": ["Mahnı yükləməsi (2)"], "download_count": 2, "streams": ["Qrup: Mahnı"],
                       "stream_count": 1, "is_busy": True}
    ev = run_restart(cmd)
    assert calls["restart"] == 0 and "Aktiv işlər var" in ev.edits[-1] and "<b>2</b>" in ev.edits[-1]
    assert "Qrup: Mahnı" in ev.edits[-1] and ".restart now" in ev.edits[-1]
    run_restart(cmd, "now")
    assert calls["restart"] == 1


def test_restart_rejects_bad_argument_and_a_just_started_process(restarter, monkeypatch):
    from bot.plugins import restart_plugin as rp
    ub, cmd, calls = restarter
    assert "İstifadə" in run_restart(cmd, "bəlkə").edits[-1] and calls["restart"] == 0
    monkeypatch.setattr(rp, "_STARTED", time.time() - 3)
    assert "Yenicə başlayıb" in run_restart(cmd, "now").edits[-1] and calls["restart"] == 0


def test_restart_from_the_menu_remembers_the_menu_message(restarter, tmp_path, live):
    ub, cmd, calls = restarter
    ev = pm.PanelEvent(ub, "inline-77", TOK, "restart", cmd["pattern"], ".restart now")

    async def scenario():
        await cmd["fn"](ev)
        await asyncio.sleep(0.01)
    asyncio.run(scenario())
    assert calls["restart"] == 1 and json.loads((tmp_path / "ur.json").read_text()) == {
        "at": json.loads((tmp_path / "ur.json").read_text())["at"], "iid": "inline-77"}


def test_announce_edits_the_command_message_or_the_menu_message_after_restart(restarter, tmp_path, monkeypatch):
    ub, cmd, _ = restarter
    sent = []

    async def edit_message(chat, msg, text, parse_mode=None):
        sent.append((chat, msg, text))
    ub.client = types.SimpleNamespace(edit_message=edit_message)

    async def quick(_):
        return None
    monkeypatch.setattr(ur.asyncio, "sleep", quick)
    assert asyncio.run(ur.announce(ub)) is False and not sent                    # restart olmayıb — heç nə
    (tmp_path / "ur.json").write_text(json.dumps({"at": time.time() - 5, "chat": 7, "msg": 99}))
    assert asyncio.run(ur.announce(ub)) is True
    assert sent[0][:2] == (7, 99) and "Userbot yenidən başladı" in sent[0][2] and "Müddət: 5." in sent[0][2]
    assert not (tmp_path / "ur.json").exists()                                    # bir dəfəlikdir
    (tmp_path / "ur.json").write_text(json.dumps({"at": time.time() - 70, "iid": "inline-9"}))
    asyncio.run(ur.announce(ub))
    assert ub.edits[-1][0].startswith("✅ <b>Userbot yenidən başladı</b>\n⏱ Müddət: 1 dəq.")
    assert datas(ub.edits[-1][1]) == ["ubp:0:main", "ubp:0:close"]


def test_restart_runs_in_a_detached_task_so_disconnecting_the_client_cannot_cancel_it(restarter, monkeypatch):
    """Köhnə xəta: restart handler-in içində gedirdi, client.disconnect() handler-i ləğv edirdi → execv olmurdu,
    userbot-lar bağlı qalırdı. İndi handler dərhal qayıdır, restart ayrı tapşırıqdır."""
    from bot.plugins import restart_plugin as rp
    ub, cmd, calls = restarter
    state = {}

    async def slow_restart(ctx):
        state["started"] = True
        await state["gate"].wait()
        state["finished"] = True
    monkeypatch.setattr(rp, "restart", slow_restart)

    async def scenario():
        state["gate"] = asyncio.Event()
        handler = asyncio.ensure_future(cmd["fn"](REvent(None)))
        await handler                                    # handler restart bitməyi GÖZLƏMİR
        assert handler.done() and "finished" not in state
        await asyncio.sleep(0.01)
        assert state.get("started")
        handler.cancel()                                 # handler-in tapşırığını ləğv etmək restartı dayandırmır
        state["gate"].set()
        await asyncio.sleep(0.01)
        assert state.get("finished")
    asyncio.run(scenario())


def test_restart_falls_back_to_exec_if_the_clean_restart_gets_cancelled_or_fails(monkeypatch):
    from bot.plugins import restart_plugin as rp
    execs = []
    monkeypatch.setattr(ur.os, "execv", lambda path, argv: execs.append((path, argv)))
    monkeypatch.setattr(ur.logging, "shutdown", lambda: None)
    for exc in (asyncio.CancelledError(), RuntimeError("cleanup xətası")):
        async def broken(ctx, exc=exc):
            raise exc
        monkeypatch.setattr(rp, "restart", broken)
        asyncio.run(ur.do_restart(types.SimpleNamespace()))
    assert len(execs) == 2 and execs[0][0] == execs[0][1][0]                        # eyni argv ilə birbaşa əvəzlənir


# ───────────── ⚙️ komanda ayarları (məs. .purgeme silmə sırası) ─────────────
ORDER = [{"key": "order", "label": "🔀 Silmə sırası", "default": "new", "note": "Standart: sonuncudan.",
          "choices": [("new", "🔽 Yenidən köhnəyə (Z → A)"), ("old", "🔼 Köhnədən yeniyə (A → Z)")]}]


@pytest.fixture
def opts(ub):
    ub.state["cmd_options"] = {"ban": ORDER}
    return ub


def test_options_are_read_with_defaults_and_validated(opts):
    assert pm.opt_get(opts, "ban", "order") == "new" and pm.opt_get(opts, "alive", "order") is None
    assert pm.opt_set(opts, "ban", "order", "old") == (True, "⚙️ .ban: 🔀 Silmə sırası → 🔼 Köhnədən yeniyə (A → Z)")
    assert pm.opt_get(opts, "ban", "order") == "old" and opts.db.kv_get("cmdopt", "ban:order") == "old"
    assert not pm.opt_set(opts, "ban", "order", "xaos")[0] and not pm.opt_set(opts, "ban", "yox", "old")[0]
    assert not pm.opt_set(opts, "alive", "order", "old")[0]
    opts.db.kv_set("cmdopt", "ban:order", "pozulmuş")
    assert pm.opt_get(opts, "ban", "order") == "new"                                       # yanlış dəyər → standart


def test_options_page_marks_the_current_choice_and_follows_panel_rules(opts):
    text, kb = pm.options_view(opts, TOK, "ban", "q")
    check_rules(text, kb)
    assert "🔀 Silmə sırası: <b>🔽 Yenidən köhnəyə (Z → A)</b>" in text and "Standart: sonuncudan." in text
    labels = [b.text for b in flat(kb)]
    assert labels[:2] == ["✅ 🔽 Yenidən köhnəyə (Z → A)", "🔼 Köhnədən yeniyə (A → Z)"]
    assert f"ubp:{TOK}:sv:q:ban:order:old" in datas(kb) and f"ubp:{TOK}:qc:ban" in datas(kb)     # ⬅️ komanda səhifəsi
    assert f"ubp:{TOK}:gc:ban" in datas(pm.options_view(opts, TOK, "ban", "g")[1])


def test_command_pages_show_the_settings_button_only_for_commands_with_options(opts):
    groups = guide.build(opts)
    for text, kb in (cmds.command_view(opts, groups, TOK, "ban", options=True),
                     guide.command_view(groups, TOK, "ban", options=True, ctx="g")):
        assert any(d.startswith(f"ubp:{TOK}:so:") and d.endswith(":ban") for d in datas(kb)) and "ayarları var" in text
        check_rules(text, kb)
    assert not any(":so:" in d for d in datas(cmds.command_view(opts, groups, TOK, "alive")[1]))
    click(opts, f"ubp:{TOK}:qc:ban")
    assert f"ubp:{TOK}:so:q:ban" in datas(opts.edits[-1][1])
    click(opts, f"ubp:{TOK}:qc:alive")
    assert not any(":so:" in d for d in datas(opts.edits[-1][1]))


def test_option_callbacks_save_and_stay_on_the_settings_page(opts):
    click(opts, f"ubp:{TOK}:so:q:ban")
    assert "ayarlar" in opts.edits[-1][0]
    click(opts, f"ubp:{TOK}:sv:q:ban:order:old")
    assert opts.db.kv_get("cmdopt", "ban:order") == "old"
    assert "→ 🔼 Köhnədən yeniyə (A → Z)" in opts.edits[-1][0]
    assert "✅ 🔼 Köhnədən yeniyə (A → Z)" in [b.text for b in flat(opts.edits[-1][1])]
    cb = click(opts, f"ubp:{TOK}:sv:q:ban:order:xaos")
    assert cb.answers[-1][1] is True and opts.db.kv_get("cmdopt", "ban:order") == "old"            # yanlış dəyər qəbul olunmur
    click(opts, f"ubp:{TOK}:so:q:alive")                                                          # ayarı olmayan komanda → ana menyu
    assert "Userbot menyusu" in opts.edits[-1][0]


def test_real_purge_plugin_options_show_up_in_the_menu(ub):
    import userbot.plugins.chat_tools as chat_tools
    from tests import userbot_fakes as fakes
    fub = fakes.FakeUB()
    fub.state, fub.context, fub.db = {}, types.SimpleNamespace(), ub.db
    chat_tools.register(fub)
    ub.state["cmd_options"] = fub.state["cmd_options"]
    text, kb = pm.options_view(ub, TOK, "purgeme")
    assert "Silmə sırası" in text and "A → Z" in [b.text for b in flat(kb)][1] and f"ubp:{TOK}:sv:q:purgeme:order:old" in datas(kb)
    pm.opt_set(ub, "purgeme", "order", "old")
    assert ub.db.kv_get("cmdopt", "purgeme:order") == "old"                                         # plugin məhz bu açarı oxuyur
