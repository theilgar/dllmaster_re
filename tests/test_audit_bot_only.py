"""python init.py bot: userbot yoxdur — audit yenə də asistan hesabına qoşulur və menyuda görünür."""
import types

import pytest


class FakeClient:
    def __init__(self):
        self.handlers = []

    def is_connected(self):
        return True

    def add_event_handler(self, fn, builder):
        self.handlers.append((fn, builder))

    def remove_event_handler(self, fn, builder):
        self.handlers.remove((fn, builder))


@pytest.fixture
def ctx(ub):
    sl = types.SimpleNamespace(running=True, voice_enabled=True, client=FakeClient())
    return types.SimpleNamespace(userbot_enabled=False, bot_enabled=True, creator_id=1, plugin_status={},
                                 mode="bot", voice_assistant=types.SimpleNamespace(slots=[sl]),
                                 telethon_client=None, userbot=None)


def test_register_works_with_audit_only_host(logger_mod, ctx):
    assert logger_mod.ensure_audit_host(ctx) is True
    assert logger_mod._AUDIT_UB is not None
    assert len(logger_mod.AUDIT_HANDLERS) == 5                     # cache, silinən, redaktə, çat hadisəsi, üzv/ban


def test_attach_audit_prepares_host_on_demand(logger_mod, ctx):
    client = ctx.voice_assistant.slots[0].client
    logger_mod.attach_audit(client, ctx)                           # host hazırlanır, dinləyicilər qoşulur
    assert len(client.handlers) == 5
    assert logger_mod.attach_audit(client, ctx) is False           # təkrar qoşulmur


def test_audit_only_host_is_harmless(logger_mod, ctx):
    host = logger_mod._AuditOnlyHost(ctx)
    assert host.client is None and host.title("x") == "x"
    assert host.command("sh")(len) is len                         # dekoratorlar heç nə qeyd etmir
    assert host.section("k", "l", order=1)(len) is len
    assert host.some_unknown_api(len) is len


def test_assistant_listens_even_without_userbot(logger_mod, ctx, ub):
    ub.context = ctx
    logger_mod.add_group(ub, -1, "g")
    token = logger_mod._CUR_CLIENT.set(object())
    try:
        assert logger_mod.audit_here(ub, -1)
    finally:
        logger_mod._CUR_CLIENT.reset(token)


def test_menu_shows_audit_button_in_bot_only_mode(ctx, monkeypatch):
    import bot.menus  # noqa: F401
    from bot.menus.views import system_hub as H

    monkeypatch.setattr(H, "get_context", lambda: ctx)
    _, kb = H.bots_view()
    labels = " ".join(b.text for row in kb.inline_keyboard for b in row)
    assert "Qrup auditi" in labels
    assert ".amsg" not in labels and "Userbotlar" not in labels   # userbot funksiyaları görünmür


def test_tg_client_falls_back_to_assistant(ctx, monkeypatch):
    import bot.menus  # noqa: F401
    from bot.menus.views import audit as av

    monkeypatch.setattr(av, "get_context", lambda: ctx)
    assert av.tg_client() is ctx.voice_assistant.slots[0].client
