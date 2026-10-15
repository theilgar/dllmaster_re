"""Kodda tr()/_() ilə bükülən hər Azərbaycanca mətnin en / ru / tr tərcüməsi olmalıdır (yer tutucular eyni)."""
import ast
import pathlib
import re

from common import i18n

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _literals():
    out = {}
    for p in ROOT.rglob("*.py"):
        if any(x in p.parts for x in (".venv", "tests", "__pycache__")):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and n.args:
                f = n.func
                name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
                a = n.args[0]
                if name in ("tr", "_") and isinstance(a, ast.Constant) and isinstance(a.value, str) \
                        and len(a.value.strip()) > 2:
                    out.setdefault(a.value, str(p.relative_to(ROOT)))
    return out


def test_every_wrapped_string_is_translated():
    i18n.reload()
    keys = set(i18n.source_keys())
    missing = {s: f for s, f in _literals().items() if s not in keys}
    assert not missing, "Tərcümə yoxdur (common/i18n_extra.py-a əlavə edin):\n" + "\n".join(
        f"{f}: {s[:80]!r}" for s, f in list(missing.items())[:15])


def test_placeholders_match_in_all_languages():
    i18n.reload()
    bad = []
    for key, tr in i18n.CATALOG.items():
        ph = set(re.findall(r"\{(\w+)\}", key))
        for lang in ("en", "ru", "tr"):
            if lang in tr and set(re.findall(r"\{(\w+)\}", tr[lang])) != ph:
                bad.append((lang, key[:50]))
    assert not bad, bad[:10]


def test_all_three_languages_have_every_key():
    i18n.reload()
    for key, tr in i18n.CATALOG.items():
        assert all(tr.get(l) for l in ("en", "ru", "tr")), key[:60]
