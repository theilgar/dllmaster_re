"""
🧩 Plugin kodunun yoxlanışı — common/plugins.py

validate_plugin(code): sintaksis (ast.parse), növ (bot: setup · userbot: register · hybrid) və qısa məlumat.
Yükləmə / hot-reload: common/utilities.load_plugins, bot/plugins/plugin_manager_plugin.py, userbot/core/host.py.
"""
from __future__ import annotations

import ast
from typing import Any, Dict


def validate_plugin(code: str) -> Dict[str, Any]:
    """
    Python kodunu təhlil edir:
    - Sintaksis xətalarını tutur (SyntaxError).
    - Hansı növ olduğunu tapır: 'bot' (setup), 'userbot' (register), 'common' və s.
    - Daxili funksiyaları, komandaları və docstring-i çıxarır.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {
            "valid": False,
            "error": f"Sətir {e.lineno}: {e.msg}",
            "kind": "unknown",
            "has_setup": False,
            "has_register": False,
            "doc": "",
        }

    funcs = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    has_setup = "setup" in funcs
    has_register = "register" in funcs

    kind = "other"
    if has_setup and not has_register:
        kind = "bot"
    elif has_register and not has_setup:
        kind = "userbot"
    elif has_setup and has_register:
        kind = "hybrid"

    doc = ast.get_docstring(tree) or ""
    return {
        "valid": True,
        "error": None,
        "kind": kind,
        "has_setup": has_setup,
        "has_register": has_register,
        "has_teardown": "teardown" in funcs,
        "doc": doc,
    }


