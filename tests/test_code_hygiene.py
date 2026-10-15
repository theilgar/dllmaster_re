"""Kod gigiyenası: səssiz xəta udmaq qalmasın, bütün modullar tərtib olunsun."""
import ast
import glob
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGGING_HANDLER_FUNCS = {"emit", "handleError", "flush", "__del__"}
EXCLUDED = {"common/logger.py", "bot/core/ytdl_worker.py", "common/i18n.py", "bot/core/input_wait.py"}


def _py_files():
    for d in ("bot", "common", "userbot"):
        for f in glob.glob(os.path.join(ROOT, d, "**", "*.py"), recursive=True):
            if "__pycache__" not in f:
                yield f


def test_every_module_parses():
    for f in list(_py_files()) + [os.path.join(ROOT, "init.py")]:
        ast.parse(open(f, encoding="utf-8").read(), filename=f)


def test_no_silent_except_exception_pass():
    offenders = []
    for f in _py_files():
        rel = os.path.relpath(f, ROOT)
        if rel in EXCLUDED:
            continue
        tree = ast.parse(open(f, encoding="utf-8").read())
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}

        def enclosing_func(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node.name
            return ""

        for n in ast.walk(tree):
            if (isinstance(n, ast.ExceptHandler) and n.name is None and isinstance(n.type, ast.Name)
                    and n.type.id == "Exception" and len(n.body) == 1 and isinstance(n.body[0], ast.Pass)):
                if enclosing_func(n) in LOGGING_HANDLER_FUNCS:      # logging.Handler daxilində loga yazmaq sonsuz dövrə yaradır
                    continue
                offenders.append(f"{rel}:{n.lineno}")
    assert not offenders, offenders


def test_no_unlogged_shell_with_user_input():
    """create_subprocess_shell yalnız məlum yerlərdə olmalıdır (yeni yer əlavə olunsa təhlükəsizlik baxışı lazımdır)."""
    allowed = {"common/doctor.py", "common/utilities.py"}
    found = set()
    for f in _py_files():
        if "create_subprocess_shell" in open(f, encoding="utf-8").read():
            found.add(os.path.relpath(f, ROOT))
    assert found <= allowed, found - allowed


@pytest.mark.parametrize("path", ["userbot/plugins/system.py"])
def test_shell_reply_only_runs_own_messages(path):
    src = open(os.path.join(ROOT, path), encoding="utf-8").read()
    assert "not reply.out" in src
