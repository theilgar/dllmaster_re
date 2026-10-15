"""
🔁 Köhnə importlar üçün uyğunluq qatı — common/compat.py

Əvvəl bütün nüvə bir `core/` qovluğunda idi. İndi üç yerə bölünüb (common/, bot/core/, userbot/core/).
Köhnə yazılmış və ya .install ilə qoyulmuş plugin-lər `from core.userbot_api import ...` yazsa da işləsin
deyə `core.X` adları YENİ modulların ÖZÜNƏ yönəldilir (kopya deyil — eyni obyekt). Bu vacibdir:
audio_cache, cpu_pool kimi modulların qlobal vəziyyəti var; iki nüsxə olsa keş / hovuz ikiyə bölünərdi.

    from core.database import get_db          → common.database
    from core import audio_cache              → bot.core.audio_cache
    import plugins.start_plugin               → bot.plugins.start_plugin

Yeni kodda birbaşa yeni yolları yaz. init.py install()-ı ən əvvəl çağırır.
"""
import importlib
import importlib.abc
import importlib.util
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _discover(rel_dir: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    p = os.path.join(_ROOT, rel_dir)
    if os.path.isdir(p):
        try:
            return tuple(sorted(
                f[:-3] for f in os.listdir(p)
                if f.endswith(".py") and not f.startswith("__")
            ))
        except OSError:
            pass
    return fallback


_COMMON = _discover("common", (
    "activity", "archive", "audit", "backup", "bootstrap", "code_update",
    "compat", "context", "database", "deps", "destinations", "diagnostics",
    "doctor", "i18n", "logger", "media", "menu", "permissions",
    "plugins", "proxy", "tiers", "updates", "utilities",
))

_BOT = _discover("bot/core", (
    "audio_cache", "cpu_pool", "depo_helpers", "group_panel", "inline_panels",
    "inline_settings", "input_wait", "links", "panel", "song_names",
    "spotify_handler", "stars", "va_record", "webprofile", "welcome",
    "youtube_handler", "yt_accounts", "ytdl_worker",
))

_USERBOT = _discover("userbot/core", (
    "accounts", "host", "tg_login", "tg_session", "userbot_api", "userbot_db",
))

ALIASES = {f"core.{n}": f"common.{n}" for n in _COMMON}
ALIASES.update({f"core.{n}": f"bot.core.{n}" for n in _BOT})
ALIASES.update({f"core.{n}": f"userbot.core.{n}" for n in _USERBOT})

# bot/core/<common> çağırışları (məs. proxy, archive, destinations, media, permissions...) common/-a gedir
ALIASES.update({f"bot.core.{n}": f"common.{n}" for n in _COMMON})

_PREFIXES = {
    "plugins.": "bot.plugins.",
    "userbot_plugins.": "userbot.plugins.",
}


def target_of(name: str):
    if name in ALIASES:
        return ALIASES[name]
    for old, new in _PREFIXES.items():
        if name.startswith(old) and name.count(".") == 1:
            return new + name[len(old):]
    return None


class _AliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if target_of(fullname) is None:
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        # yeni modulu (lazım olsa) import edib ONU qaytarırıq — köhnə ad eyni obyektə bağlanır
        return importlib.import_module(target_of(spec.name))

    def exec_module(self, module):
        pass                                          # artıq yüklənib


def _package(name: str, doc: str):
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name, doc)
        mod.__path__ = []                             # paket kimi görünsün: "from core import x" işləsin
        sys.modules[name] = mod
    return mod


def install():
    if any(isinstance(f, _AliasFinder) for f in sys.meta_path):
        return
    _package("core", "Uyğunluq: köhnə core/ → common/, bot/core/, userbot/core/")
    _package("plugins", "Uyğunluq: köhnə plugins/ → bot/plugins/")
    _package("userbot_plugins", "Uyğunluq: köhnə userbot_plugins/ → userbot/plugins/")
    sys.meta_path.insert(0, _AliasFinder())
