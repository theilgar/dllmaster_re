"""Depo modullarının bölünməsi (engine / panels / handlers): heç bir ad itməyib, köhnə importlar işləyir."""
import builtins
import dis
import importlib
import inspect
import types

import pytest

MODULES = ["bot.depo.engine", "bot.depo.engine_common", "bot.depo.engine_failures", "bot.depo.engine_sync",
           "bot.depo.engine_queue", "bot.depo.engine_countries", "bot.depo.panels", "bot.depo.handlers"]


def _code_objects(code):
    yield code
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            yield from _code_objects(c)


def _functions(mod):
    """Modulda (və sinif gövdələrində) təyin olunan bütün funksiyalar."""
    for _, obj in vars(mod).items():
        if inspect.isfunction(obj) and obj.__module__ == mod.__name__:
            yield obj
        elif inspect.isclass(obj) and obj.__module__ == mod.__name__:
            for attr in vars(obj).values():
                f = attr.__func__ if isinstance(attr, (staticmethod, classmethod)) else attr
                if isinstance(attr, property):
                    for g in (attr.fget, attr.fset):
                        if g:
                            yield g
                elif inspect.isfunction(f):
                    yield f


@pytest.mark.parametrize("modname", MODULES)
def test_every_global_name_resolves(modname):
    """Funksiyaların istifadə etdiyi bütün qlobal adlar modulda və ya builtins-də mövcuddur (star-import itkisi yoxdur)."""
    mod = importlib.import_module(modname)
    missing = set()
    for fn in _functions(mod):
        for code in _code_objects(fn.__code__):
            for ins in dis.get_instructions(code):
                if ins.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
                    name = ins.argval
                    if name not in fn.__globals__ and not hasattr(builtins, name):
                        missing.add((fn.__qualname__, name))
    assert not missing, sorted(missing)


def test_filler_is_assembled_from_mixins():
    from bot.depo.engine import Filler
    from bot.depo.engine_countries import CountriesMixin
    from bot.depo.engine_failures import FailuresMixin
    from bot.depo.engine_queue import QueueMixin
    from bot.depo.engine_sync import SyncMixin

    assert [c.__name__ for c in Filler.__mro__[:5]] == ["Filler", "FailuresMixin", "SyncMixin", "QueueMixin",
                                                       "CountriesMixin"]
    for method, mixin in (("schedule_retry", FailuresMixin), ("sync_scan", SyncMixin), ("build_queue", QueueMixin),
                          ("apply_countries", CountriesMixin)):
        assert method in vars(mixin) and method not in vars(Filler)
    for core in ("__init__", "start", "stop", "loop", "worker", "process", "next_item", "save_state", "load_state"):
        assert core in vars(Filler)


def test_methods_are_not_defined_twice():
    from bot.depo.engine import Filler
    from bot.depo.engine_countries import CountriesMixin
    from bot.depo.engine_failures import FailuresMixin
    from bot.depo.engine_queue import QueueMixin
    from bot.depo.engine_sync import SyncMixin

    seen = {}
    for cls in (Filler, FailuresMixin, SyncMixin, QueueMixin, CountriesMixin):
        for name, attr in vars(cls).items():
            if name.startswith("__") and name.endswith("__"):
                continue
            assert name not in seen, f"{name}: {seen[name]} və {cls.__name__}"
            seen[name] = cls.__name__


def test_old_import_paths_still_work():
    """Digər modulların istifadə etdiyi adlar bot.depo.engine-dən əvvəlki kimi gəlir."""
    from bot.depo.engine import (DEFAULT_SOURCES, MAX_DELAY, MAX_WORKERS, NOVID_AUTO_MAX, Q_PAGE,  # noqa: F401
                                 SOURCE_KIND_LABELS, SYNC_AUTO_MAX, SYNC_EVERY, Filler, delete_depo_tracks,
                                 ensure_thread_pool, hkey, raise_nofile, search_depo_for_deletion)
    import bot.depo as depo
    assert depo.Filler is Filler


def test_private_helpers_reach_the_mixins():
    """_underscore adları da star-import ilə mixin modullarına çatır."""
    import bot.depo.engine_common as common
    import bot.depo.engine_sync as sync_mod

    for name in common.__all__:
        assert hasattr(sync_mod, name), name


def test_handlers_setup_is_smaller_and_wired_to_panels():
    import bot.depo.handlers as h

    src = inspect.getsource(h.setup)
    assert "make_views(" in src and len(src.splitlines()) < 1900
    for gone in ("def helpers_view", "def queue_view", "def panel("):
        assert gone not in src
