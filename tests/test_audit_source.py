"""Auditi kim izləyir: 🎙 asistan / 📱 userbot."""


def test_group_source_defaults_to_assistant(logger_mod, ub):
    assert logger_mod.group_source(ub, -1) == "assistant"
    logger_mod.set_group_source(ub, -1, "userbot")
    assert logger_mod.group_source(ub, -1) == "userbot"
    assert logger_mod.group_source(ub, -2) == "assistant"


def test_assistant_listener_works_for_assistant_groups(logger_mod, ub, assistant):
    logger_mod.add_group(ub, -1, "a")
    assert not logger_mod.audit_here(ub, -1)                     # userbot dinləyicisi susur
    token = logger_mod._CUR_CLIENT.set(object())
    try:
        assert logger_mod.audit_here(ub, -1)                     # asistan dinləyicisi işləyir
    finally:
        logger_mod._CUR_CLIENT.reset(token)


def test_userbot_takes_over_when_assistant_is_down(logger_mod, ub):
    logger_mod.add_group(ub, -1, "a")                            # ub.context.voice_assistant = None
    assert logger_mod.audit_here(ub, -1)
    token = logger_mod._CUR_CLIENT.set(object())
    try:
        assert not logger_mod.audit_here(ub, -1)
    finally:
        logger_mod._CUR_CLIENT.reset(token)


def test_group_can_choose_userbot(logger_mod, ub, assistant):
    logger_mod.add_group(ub, -1, "a")
    logger_mod.set_group_source(ub, -1, "userbot")
    assert logger_mod.audit_here(ub, -1)
    token = logger_mod._CUR_CLIENT.set(object())
    try:
        assert not logger_mod.audit_here(ub, -1)
    finally:
        logger_mod._CUR_CLIENT.reset(token)


def test_unaudited_group_is_only_journaled_by_userbot(logger_mod, ub, assistant):
    assert logger_mod.audit_here(ub, -99)
    token = logger_mod._CUR_CLIENT.set(object())
    try:
        assert not logger_mod.audit_here(ub, -99)
    finally:
        logger_mod._CUR_CLIENT.reset(token)


class FakeClient:
    def __init__(self):
        self.handlers = []

    def add_event_handler(self, fn, builder):
        self.handlers.append((fn, builder))

    def remove_event_handler(self, fn, builder):
        self.handlers.remove((fn, builder))


async def test_attach_and_detach_audit_handlers(logger_mod, ub):
    async def handler(event):
        return event

    logger_mod._AUDIT_UB = ub
    logger_mod.AUDIT_HANDLERS[:] = [(handler, 1), (handler, 2)]
    client = FakeClient()
    assert logger_mod.attach_audit(client) is True
    assert len(client.handlers) == 2
    assert logger_mod.attach_audit(client) is False              # ikinci dəfə qoşulmur
    await client.handlers[0][0]("evt")                           # wrapper işləyir
    logger_mod.detach_audit(client)
    assert not client.handlers


def test_attach_audit_without_host_does_nothing(logger_mod):
    assert logger_mod.attach_audit(FakeClient()) is False
