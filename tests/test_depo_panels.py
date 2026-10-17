"""bot/depo/panels.py — handlers.py-dən ayrılmış paneller: ad xətası yoxdur, köməkçi panelinin düymələri düzgündür."""
import asyncio
import inspect
import types
from unittest.mock import MagicMock

import pytest

import bot.depo.panels as P
from bot.core import depo_helpers
from bot.core.depo_tuning import TUNABLES

V1 = ["helpers_guide_view", "tune_guide_view", "df_top_interval_picker_view", "dd_result_view", "sync_result_view", "hp_add_view",
      "mode_rows", "workers_label", "clean_prompt_view", "_ago", "kb_sig"]
V2 = ["ytm_line", "sources_view", "df_delay_picker_view", "df_worker_picker_view", "df_rate_picker_view", "df_tune_hub_view",
      "df_tune_view", "fail_view",
      "queue_view", "country_queries_list_view", "country_queries_view", "countries_view", "prepare_clean_view",
      "helpers_view", "dd_intro", "sync_intro", "userbot_line", "panel", "panel_async"]


def _call(fn):
    """Bütün tələb olunan arqumentlərə MagicMock verib çağırır; yalnız NameError (itmiş ad) uğursuzluqdur."""
    args = [MagicMock() for p in inspect.signature(fn).parameters.values()
            if p.default is inspect.Parameter.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    try:
        res = fn(*args)
        if inspect.iscoroutine(res):
            asyncio.run(res)
    except NameError as e:                       # modulda itmiş ad / import
        pytest.fail(f"{fn.__name__}: {e}")
    except Exception:
        pass                                     # saxta obyektlərdən gələn digər xətalar maraqlı deyil


def _views(**over):
    deps = dict(filler=MagicMock(), helpers=MagicMock(), tg=MagicMock(), context=MagicMock(),
                scan_state={"running": False, "stop": False, "task": None}, clean_pending={}, clean_wait={},
                hp_page={"n": 0}, HP_PER_PAGE=8)
    deps.update(over)
    return P.make_views(**deps)


@pytest.mark.parametrize("name", V1)
def test_module_level_views_resolve_all_names(name):
    _call(getattr(P, name))


def test_factory_returns_every_view():
    v = _views()
    assert all(callable(getattr(v, n)) for n in V2)


@pytest.mark.parametrize("name", V2)
def test_factory_views_resolve_all_names(name):
    _call(getattr(_views(), name))


# ── köməkçi bot paneli (rejim düyməsi, bələdçi) ──
def _hp(scope="all", n=3):
    helpers_list = [types.SimpleNamespace(name=f"@bot{i}", status="🟢 hazır", sent=i * 10, floods=0, fails=0,
                                          cool_until=0, from_env=(i == 0)) for i in range(n)]
    return types.SimpleNamespace(
        helpers=helpers_list, scope=scope, rate=18, conv_mode="lazy", active=lambda: helpers_list, flooded=lambda: 0, channel_cool_left=lambda chat_id=None: 0, shard_report=lambda: [],
        tune=lambda k: TUNABLES[k].default, tune_base=lambda k: TUNABLES[k].default,
        tune_auto=lambda k: TUNABLES[k].default, tune_mode=lambda k: "auto", turbo=False,
        capacity=lambda: 54, pending=lambda: 0, polling=True, redirects=0, last_error="",
        stats={"helper": 5, "fallback": 1, "conv_fail": 0},
        cstats={"lazy": 1, "warm": 2, "inline_skip": 0, "fail": 0}, sinks=lambda: [1], sink_interval=lambda: 1.0)


def _texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def _datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


@pytest.mark.parametrize("scope,nxt", [("off", "filler"), ("filler", "all"), ("all", "off")])
def test_scope_button_cycles(scope, nxt):
    text, kb = _views(helpers=_hp(scope)).helpers_view()
    assert f"df:hps:{nxt}" in _datas(kb)
    assert sum(1 for d in _datas(kb) if d.startswith("df:hps:")) == 1          # tək rejim düyməsi


def test_off_scope_hides_rate_and_conversion():
    text, kb = _views(helpers=_hp("off")).helpers_view()
    assert "df:tn" not in _datas(kb) and "df:hpconv" not in _datas(kb)
    assert "Temp" not in text and "file_id çevirmə" not in text


def test_on_scope_shows_rate_and_conversion():
    text, kb = _views(helpers=_hp("all")).helpers_view()
    assert "df:tn" in _datas(kb) and "df:hpconv" in _datas(kb)
    assert "Temp" in text


def test_helpers_panel_buttons_and_navigation():
    _, kb = _views(helpers=_hp("all")).helpers_view()
    datas = _datas(kb)
    assert "hg:hub:0" in datas and "df:hpguide" in datas
    assert "df:panel" in datas                                               # geri → Doldurucu
    assert not any(t == "📦 Doldurucu" for t in _texts(kb))                  # ayrıca Doldurucu düyməsi yoxdur
    assert "df:hprm:1" in datas and "df:hprm:0" not in datas                  # config.env botu silinmir


def test_eight_helpers_per_page():
    text, kb = _views(helpers=_hp("all", 11)).helpers_view()
    assert text.count("<b>@bot") == 8
    assert "df:hp:1" in _datas(kb)


def test_guide_explains_main_features():
    text, kb = P.helpers_guide_view()
    for word in ("Rejim", "Sürət limiti", "Çevirmə", "Lazy", "Köməkçi bot əlavə et", "admin"):
        assert word in text
    assert len(text) < 4096 and "df:hp" in _datas(kb)


def test_depo_scope_order_supports_cycle():
    assert list(depo_helpers.SCOPES) == ["off", "filler", "all"]


# ── 🎛 Tənzimləmələr: hub + hər ayar üçün 🤖 Avto / ✋ Manual picker ──
def _hp_tune(modes=None, vals=None):
    modes, vals = modes or {}, vals or {}
    hp = _hp("all")
    hp.tune_mode = lambda k: modes.get(k, "auto")
    hp.tune = lambda k: vals.get(k, TUNABLES[k].default)
    return hp


def test_hub_turbo_toggle_shows_state_and_fits_two_per_row():
    hp = _hp_tune()
    _, kb = _views(helpers=hp).df_tune_hub_view()
    assert "🚀 Köməkçi turbo 🔴" in _texts(kb) and "df:tpf" in _datas(kb)
    assert "df:tng" in _datas(kb)
    assert len(kb.inline_keyboard) <= 7 and all(len(r) <= 2 for r in kb.inline_keyboard[:-1])
    hp.turbo = True
    _, kb = _views(helpers=hp).df_tune_hub_view()
    assert "🚀 Köməkçi turbo 🟢" in _texts(kb)
    assert "🎛 Tənzimləmələr 🚀" in _texts(_views(helpers=hp).helpers_view()[1])
    assert "🎛 Tənzimləmələr" in _texts(_views(helpers=_hp("all")).helpers_view()[1])


def test_tune_hub_lists_every_setting_with_auto_all_button():
    text, kb = _views(helpers=_hp_tune({"rate": "manual"}, {"rate": 12})).df_tune_hub_view()
    datas = _datas(kb)
    assert all(f"df:tn:{k}" in datas for k in TUNABLES) and "df:tna" in datas
    assert "df:hp" in datas                                                       # geri → Köməkçi botlar
    assert all(len(row) <= 2 for row in kb.inline_keyboard[:-1] if row[0].callback_data.startswith("df:tn"))
    assert len(text) < 4096 and "✋" in text and "🤖" in text


@pytest.mark.parametrize("key", list(TUNABLES))
def test_tune_picker_auto_has_mode_toggle_and_no_steppers(key):
    text, kb = _views(helpers=_hp_tune()).df_tune_view(key)
    datas = _datas(kb)
    assert f"df:tm:{key}:manual" in datas
    assert not any(d.startswith(("df:ts:", "df:tv:")) for d in datas)
    assert any(b.text == "Rejim: 🤖 Avto" for row in kb.inline_keyboard for b in row)
    assert TUNABLES[key].label in text


@pytest.mark.parametrize("key", list(TUNABLES))
def test_tune_picker_manual_has_steppers_and_input(key):
    text, kb = _views(helpers=_hp_tune({key: "manual"})).df_tune_view(key)
    datas = _datas(kb)
    assert f"df:tm:{key}:auto" in datas and f"df:tv:{key}" in datas
    assert sum(1 for d in datas if d.startswith(f"df:ts:{key}:")) == 4
    assert all(len(d.encode()) <= 64 for d in datas)
    assert datas[-3:].count("df:hp") == 0 and "df:tn" in datas                     # geri → Tənzimləmələr


def test_rate_picker_is_the_generic_picker():
    assert _views(helpers=_hp_tune()).df_rate_picker_view()[0].count("Sürət limiti") >= 1


def test_tune_guide_explains_every_setting_and_fits_one_message():
    text, kb = P.tune_guide_view()
    for t in TUNABLES.values():
        assert t.label in text and t.tip in text and t.auto_info in text
    for word in ("Avto", "Manual", "Köməkçi turbo", "Hamısı avto"):
        assert word in text
    assert len(text) < 4096 and _datas(kb) == ["df:tn", "menu:main", "df:close"]


def test_every_picker_shows_a_tip():
    for key, t in TUNABLES.items():
        assert t.tip and t.tip in _views(helpers=_hp_tune()).df_tune_view(key)[0]


def test_channel_flood_is_shown_on_the_helpers_panel():
    hp = _hp("all")
    hp.channel_cool_left = lambda: 600
    text, _ = _views(helpers=hp).helpers_view()
    assert "Depo kanalı flood-da" in text and "11 dəq." in text
    assert "Depo kanalı flood-da" not in _views(helpers=_hp("all")).helpers_view()[0]


def test_hub_guide_button_is_on_the_hub_and_rows_stay_compact():
    _, kb = _views(helpers=_hp_tune()).df_tune_hub_view()
    rows = [[b.callback_data for b in r] for r in kb.inline_keyboard]
    assert any("df:tng" in r for r in rows) and len(rows) <= 7
    settings = [d for r in rows for d in r if d.startswith("df:tn:")]
    assert len(settings) == len(TUNABLES) and "df:tn:chat_rate" in settings


def test_helpers_panel_lists_depo_channels_when_extras_exist():
    hp = _hp("all")
    hp.shard_report = lambda: [
        {"ref": "əsas", "id": -1001, "thread": None, "left": 0, "sent": 120, "helpers": 48, "primary": True},
        {"ref": "@depo2", "id": -1002, "thread": None, "left": 600, "sent": 0, "helpers": 24, "primary": False}]
    text, _ = _views(helpers=hp).helpers_view()
    assert "Depo kanalları" in text and "@depo2" in text and "11 dəq." in text and "⬆️ 120/10 dəq." in text
    assert "Depo kanalları" not in _views(helpers=_hp("all")).helpers_view()[0]
