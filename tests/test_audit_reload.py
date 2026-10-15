"""Plugin reload: asistandakı köhnə audit dinləyiciləri qalmasın, təkrar qoşulma olmasın."""
import types

from tests.test_audit_source import FakeClient


async def _handler(event):
    return event


def _setup(lg, ub, n=2):
    lg._AUDIT_UB = ub
    lg.AUDIT_HANDLERS[:] = [(_handler, i) for i in range(n)]


def test_reload_replaces_old_handlers_instead_of_duplicating(logger_mod, ub):
    _setup(logger_mod, ub)
    client = FakeClient()
    assert logger_mod.attach_audit(client) is True
    old_handlers = list(client.handlers)
    assert len(old_handlers) == 2

    logger_mod._MODULE_TOKEN = object()                  # plugin yenidən yükləndi (yeni modul nüsxəsi)
    assert logger_mod.attach_audit(client) is True
    assert len(client.handlers) == 2                      # 4 yox, 2
    assert all(h not in client.handlers for h in old_handlers)
    assert logger_mod.attach_audit(client) is False       # yeni modul üçün də təkrar qoşulmur


def test_detach_removes_state_and_handlers(logger_mod, ub):
    _setup(logger_mod, ub)
    client = FakeClient()
    logger_mod.attach_audit(client)
    logger_mod.detach_audit(client)
    assert not client.handlers and not hasattr(client, "_dlm_audit")
    logger_mod.detach_audit(client)                       # ikinci dəfə zərərsizdir


def test_refresh_attaches_to_running_assistants(logger_mod, ub, assistant):
    _setup(logger_mod, ub)
    client = FakeClient()
    assistant.slots[0].client = client
    logger_mod.refresh_assistants(ub)
    assert len(client.handlers) == 2


def test_detach_all_assistants(logger_mod, ub, assistant):
    _setup(logger_mod, ub)
    client = FakeClient()
    assistant.slots[0].client = client
    logger_mod.refresh_assistants(ub)
    logger_mod.detach_all_assistants(ub)
    assert not client.handlers


def test_remove_plugin_runs_hooks():
    from userbot.core.userbot_api import UB

    called = []
    host = object.__new__(UB)
    host.remove_hooks = {}
    host._loading = "logger"
    host.on_remove(lambda: called.append("a"))
    host.on_remove(lambda: called.append("b"))
    host.plugin_summary = lambda owner: {}
    host.commands, host.raw_handlers, host._attached_raw = [], [], set()
    host.sections, host.help, host.help_owner, host.client = {}, {}, {}, None
    UB.remove_plugin(host, "logger")
    assert called == ["a", "b"] and "logger" not in host.remove_hooks


def test_failing_hook_does_not_break_removal():
    from userbot.core.userbot_api import UB

    host = object.__new__(UB)
    host.remove_hooks = {"x": [lambda: 1 / 0]}
    host.plugin_summary = lambda owner: {"ok": True}
    host.commands, host.raw_handlers, host._attached_raw = [], [], set()
    host.sections, host.help, host.help_owner, host.client = {}, {}, {}, None
    assert UB.remove_plugin(host, "x") == {"ok": True}


def test_register_hooks_cleanup_and_attaches(logger_mod, ub, assistant):
    """register() sonunda: remove-hook qeydə alınır + işləyən asistana dərhal qoşulur."""
    from userbot.core.userbot_api import UB

    client = FakeClient()
    assistant.slots[0].client = client
    host = object.__new__(UB)
    host.remove_hooks, host._loading = {}, "logger"
    host.db, host.context, host.client = ub.db, ub.context, None
    host.state = {}
    host.command = host.section = host.on_section = host.raw = lambda *a, **k: (lambda fn: fn)
    host.title = lambda name: name
    host.__dict__["add_help"] = host.__dict__["help_category"] = lambda *a, **k: None
    logger_mod.register(host)
    assert len(client.handlers) == 5
    assert host.remove_hooks.get("logger")
    host.remove_hooks["logger"][0]()
    assert not client.handlers
