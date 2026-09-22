from types import SimpleNamespace as NS
from unittest.mock import Mock
import sys
import time
import math
import pandas as pd
import pytest

from core.risk_manager import position_size, floor_volume
from core.signals import Signal


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_sizing_blocks(value):
    assert position_size(50, .003, value, 100) == 0
    assert position_size(value, .003, 5, 100) == 0
    assert position_size(50, .003, 5, value) == 0


def test_fifty_dollar_account_cannot_afford_gold_minimum():
    assert position_size(50, .003, 5, 100) == 0


def test_volume_steps_never_round_up():
    assert floor_volume(.0199, .01, 100, .01) == .01
    assert floor_volume(.0019, .001, 100, .001) == .001
    assert floor_volume(.005, .01, 100, .01) == 0


def test_loss_scaling_cannot_force_minimum(monkeypatch):
    import utils.trade_journal as journal
    import config.settings as cfg
    monkeypatch.setattr(cfg, "ANTI_MARTINGALE_ENABLED", True)
    monkeypatch.setattr(journal, "get_recent_trade_results", lambda **kw: [-1, -1])
    assert position_size(1000, .003, 5, 100) == 0


@pytest.fixture
def broker(monkeypatch):
    import config.settings as cfg
    import core.execution as execution
    api = Mock()
    api.ORDER_TYPE_BUY = 0
    api.ORDER_TYPE_SELL = 1
    api.ACCOUNT_TRADE_MODE_DEMO = 0
    api.TRADE_RETCODE_DONE = 10009
    api.symbol_info_tick.return_value = NS(bid=2500., ask=2500.2, time=time.time())
    api.symbol_info.return_value = NS(volume_min=.01, volume_max=100., volume_step=.01,
        trade_tick_size=.01, digits=2, trade_stops_level=10, point=.01, filling_mode=1)
    api.account_info.return_value = NS(equity=10000., margin_free=9000., trade_mode=0)
    api.order_calc_profit.side_effect = lambda kind, sym, vol, entry, stop: -abs(entry-stop)*100*vol
    api.order_calc_margin.return_value = 10.
    api.order_check.return_value = NS(retcode=0)
    api.order_send.return_value = NS(retcode=10009, order=123, price=2500.2)
    monkeypatch.setitem(sys.modules, "MetaTrader5", api)
    monkeypatch.setattr(cfg, "TRADING_MODE", "demo")
    monkeypatch.setattr(execution, "check_mtf_alignment", lambda *a: (True, ""))
    monkeypatch.setattr(execution, "check_all_risk_gates", lambda *a: (False, ""))
    return api


def place():
    from core.execution import place_order
    return place_order(Signal("LONG", .9, "test"), pd.DataFrame({"atr14": [5.]}), 10000.)


def test_small_account_never_submits(broker):
    broker.account_info.return_value.equity = 50
    assert place() is None
    broker.order_send.assert_not_called()


def test_demo_cannot_trade_real_account(broker):
    broker.account_info.return_value.trade_mode = 2
    assert place() is None
    broker.order_send.assert_not_called()


@pytest.mark.parametrize("failure", ["profit", "margin", "tick", "preflight"])
def test_unknown_or_rejected_broker_data_blocks(broker, failure):
    if failure == "profit":
        broker.order_calc_profit.side_effect = None
        broker.order_calc_profit.return_value = None
    elif failure == "margin":
        broker.order_calc_margin.return_value = None
    elif failure == "tick":
        broker.symbol_info_tick.return_value.time -= 1000
    else:
        broker.order_check.return_value.retcode = 10019
    assert place() is None
    broker.order_send.assert_not_called()


def test_affordable_demo_order_respects_budget(broker):
    assert place() is not None
    request = broker.order_send.call_args.args[0]
    loss = abs(request["price"] - request["sl"]) * request["volume"] * 100
    assert loss <= broker.account_info.return_value.equity * .003
    assert request["sl"] > 0


def test_broker_closes_are_reconciled_idempotently(monkeypatch):
    from utils import trade_journal as j
    api = Mock()
    api.positions_get.return_value = []
    api.DEAL_ENTRY_OUT = 1
    api.DEAL_ENTRY_OUT_BY = 3
    now = int(time.time()*1000)
    api.history_deals_get.return_value = [
        NS(entry=0, profit=0, commission=-.02, swap=0, fee=0, time_msc=now-1000, price=2500),
        NS(entry=1, profit=-1, commission=-.02, swap=-.01, fee=0, time_msc=now, price=2499)]
    monkeypatch.setitem(sys.modules, "MetaTrader5", api)
    j.record_trade_open(77, "XAUUSDm", "LONG", 2500, .01, 2499, 2502)
    assert j.sync_closed_trades()
    assert j.sync_closed_trades()
    assert j.get_recent_trade_results() == pytest.approx([-1.05])
    assert j.original_risk(77) == 1


def test_missing_history_blocks_new_entries(monkeypatch):
    from utils import trade_journal as j
    api = Mock()
    api.positions_get.return_value = []
    api.history_deals_get.return_value = None
    monkeypatch.setitem(sys.modules, "MetaTrader5", api)
    j.record_trade_open(77, "XAUUSDm", "LONG", 2500, .01, 2499, 2502)
    assert not j.sync_closed_trades()


def test_trailing_continues_after_breakeven(monkeypatch):
    from utils import trade_journal as j
    from core.position_manager import _smart_trail
    j.record_trade_open(77, "XAUUSDm", "LONG", 2500, .01, 2495, 2515)
    api = Mock()
    api.ORDER_TYPE_BUY = 0
    api.TRADE_RETCODE_DONE = 10009
    api.order_send.return_value = NS(retcode=10009)
    monkeypatch.setitem(sys.modules, "MetaTrader5", api)
    _smart_trail(NS(ticket=77, type=0, price_open=2500, sl=2500, tp=2515, symbol="XAUUSDm"),
                 5, NS(bid=2508, ask=2508.2))
    assert api.order_send.call_args.args[0]["sl"] > 2500


def test_closed_candle_fetch_and_timeframe_age(monkeypatch):
    from core.data_feed import fetch_ohlcv
    from config import settings as cfg
    api = Mock()
    now = int(time.time())
    api.copy_rates_from_pos.return_value = [dict(time=now-20*60, open=2500, high=2501, low=2499, close=2500)]
    monkeypatch.setitem(sys.modules, "MetaTrader5", api)
    monkeypatch.setattr(cfg, "get_mt5_timeframe", lambda x: 15)
    monkeypatch.setattr(cfg, "BYPASS_STALENESS_CHECK", False)
    assert len(fetch_ohlcv("XAUUSDm", "M15")) == 1
    assert api.copy_rates_from_pos.call_args.args[2] == 1


def test_backtest_uses_previous_signal_bar_and_contract(monkeypatch):
    import backtest.backtester as bt
    n = 205
    df = pd.DataFrame({"time": pd.date_range("2025-01-01", periods=n, freq="15min"),
        "open": 2500., "high": 2501., "low": 2490., "close": 2500., "atr14": 5., "ema200": 2500.})
    monkeypatch.setattr(bt, "compute_indicators", lambda d: d)
    observed = []
    def signal(d):
        observed.append(d["time"].iloc[-1])
        return Signal("LONG", .9, "test")
    monkeypatch.setattr(bt, "generate_signal", signal)
    small = bt.run_backtest(df, initial_equity=50)
    assert small.total_trades == 0
    observed.clear()
    result = bt.run_backtest(df, initial_equity=10000)
    assert result.total_trades > 0
    assert observed[0] < result.trades[0].entry_time
    first = result.trades[0]
    assert -first.pnl <= 10000 * .003 + 1e-8
    assert result.equity_curve[-1] == pytest.approx(10000 + result.net_pnl)
    assert result.max_drawdown > 0


def test_mtf_data_failure_blocks_entry(monkeypatch):
    from core import mtf_filter
    from config import settings
    monkeypatch.setattr(settings, "MTF_ENABLED", True)
    monkeypatch.setattr(mtf_filter, "get_htf_trend", lambda *a: {"direction": "UNAVAILABLE", "strength": 0})
    assert not mtf_filter.check_mtf_alignment("XAUUSDm", "LONG")[0]
    assert not mtf_filter.check_mtf_alignment("XAUUSDm", "SHORT")[0]


def test_streak_halt_expires_next_day(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from utils import trade_journal as j
    from core.emergency_closer import check_consecutive_losses
    monkeypatch.setattr(j, "get_recent_trade_results", lambda **kw: [-1] * 5)
    monkeypatch.setattr(j, "get_last_loss_time", lambda: datetime.now(timezone.utc) - timedelta(days=1))
    assert check_consecutive_losses() == (False, 5)


def test_three_loss_cooldown_is_enforced(monkeypatch):
    from datetime import datetime, timezone
    from utils import trade_journal as j
    from core.emergency_closer import check_consecutive_losses
    monkeypatch.setattr(j, "get_recent_trade_results", lambda **kw: [-1] * 3)
    monkeypatch.setattr(j, "get_last_loss_time", lambda: datetime.now(timezone.utc))
    assert check_consecutive_losses() == (True, 3)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_complete_breakout_and_vwap_inputs(direction):
    from core.signals import _strategy_breakout, _strategy_vwap_momentum
    buy = direction == "LONG"
    last = dict(close=2510 if buy else 2490, open=2500, vwap=2500,
        volume_ratio=1.5, macd_hist=1 if buy else -1, roll_high20=2505,
        roll_low20=2495, rsi14=60 if buy else 40, atr14=5)
    assert _strategy_breakout(last, {"atr14":5})[0] == direction
    assert _strategy_vwap_momentum(last, {})[0] == direction


def test_structure_short_is_reachable():
    from core.signals import _strategy_structure_ichimoku
    last = dict(market_structure=-1, ichi_signal=-1, rsi14=40,
                ichi_tenkan=2490, ichi_kijun=2500)
    assert _strategy_structure_ichimoku(last, {})[0] == "SHORT"
