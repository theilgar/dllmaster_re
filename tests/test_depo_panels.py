"""bot/depo/panels.py — handlers.py-dən ayrılmış paneller: ad xətası yoxdur, köməkçi panelinin düymələri düzgündür."""
import asyncio
import inspect
import types
from unittest.mock import MagicMock

import pytest

import bot.depo.panels as P
from bot.core import depo_helpers

V1 = ["helpers_guide_view", "df_top_interval_picker_view", "dd_result_view", "sync_result_view", "hp_add_view",
      "mode_rows", "workers_label", "clean_prompt_view", "_ago", "kb_sig"]
V2 = ["ytm_line", "sources_view", "df_delay_picker_view", "df_worker_picker_view", "df_rate_picker_view", "fail_view",
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
        helpers=helpers_list, scope=scope, rate=18, conv_mode="lazy", active=lambda: helpers_list, flooded=lambda: 0,
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
    assert "df:pick:hpr" not in _datas(kb) and "df:hpconv" not in _datas(kb)
    assert "Temp" not in text and "file_id çevirmə" not in text


def test_on_scope_shows_rate_and_conversion():
    text, kb = _views(helpers=_hp("all")).helpers_view()
    assert "df:pick:hpr" in _datas(kb) and "df:hpconv" in _datas(kb)
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
