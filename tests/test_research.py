import numpy as np
import pandas as pd
import pytest
from core.affordability import affordability
from research.candidates import higher_timeframe_trend
from research.robust_simulator import simulate


def bars(n=5):
    return pd.DataFrame(dict(time=pd.date_range('2025-01-01',periods=n,freq='15min',tz='UTC'),
        open=np.full(n,2500.),high=np.full(n,2501.),low=np.full(n,2490.),close=np.full(n,2500.),
        atr14=np.full(n,5.),spread=np.full(n,160)))


def test_account_currency_scaling_preserves_affordability():
    usd=affordability(50,.003,.01,750)
    cents=affordability(5000,.003,.01,75000)
    assert usd['affordable'] == cents['affordable'] == False
    assert cents['minimum_equity'] == usd['minimum_equity']*100
    assert usd['minimum_loss'] == 7.5


def test_smaller_contract_not_higher_risk():
    assert affordability(50,.003,.0001,750)['affordable']
    assert not affordability(50,.003,.01,750)['affordable']


def test_htf_never_uses_future_or_partial_candles():
    df=bars(1200)
    df['close']=np.linspace(2500,2800,len(df))
    original=higher_timeframe_trend(df,4)
    assert (original[:799] == 0).all()
    changed=df.copy()
    changed.loc[1000:,'close']=100
    assert np.array_equal(original[:1000],higher_timeframe_trend(changed,4)[:1000])
    assert np.array_equal(original[:1000],higher_timeframe_trend(df.iloc[:1000],4))


def test_standard_contract_does_not_force_minimum():
    df=bars()
    result=simulate(df,np.ones(len(df)),1,len(df),minimum=.01,step=.01)
    assert result['final']==50 and result['trades']==0
    assert result['skipped']>0


def test_small_exposure_loss_stays_within_budget_without_gap():
    df=bars()
    result=simulate(df,np.ones(len(df)),1,len(df))
    assert result['trades']>0
    assert abs(result['trade_log'][0]['pnl']) <= .15


def test_trailing_not_applied_retroactively_inside_same_bar():
    df=bars(3)
    df.loc[1,['high','low','close']]=[2506,2498,2505]
    df.loc[2,['high','low','close']]=[2502,2499,2501]
    result=simulate(df,np.array([1,0,0]),1,3)
    assert result['trades']==1
    assert result['trade_log'][0]['exit_time']==df.time.iloc[2].isoformat()


def test_future_signals_do_not_change_earlier_simulation():
    df=bars(10)
    a=np.ones(10);b=a.copy();b[5:]=-1
    assert simulate(df,a,1,5)==simulate(df,b,1,5)
