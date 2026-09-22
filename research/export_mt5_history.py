"""Read-only MT5 history export. Never submits or modifies orders."""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import MetaTrader5 as mt5
import pandas as pd
from config import settings as cfg

out = Path(__file__).resolve().parents[1] / 'data' / 'historical_test'
out.mkdir(parents=True, exist_ok=True)
if not mt5.initialize(path=r'C:\Program Files\MetaTrader 5\terminal64.exe', login=cfg.BROKER_LOGIN,
                      password=cfg.BROKER_PASSWORD, server=cfg.BROKER_SERVER, timeout=60000):
    print(json.dumps({'error': 'MT5 initialization failed', 'detail': mt5.last_error()}))
    raise SystemExit(1)
try:
    account = mt5.account_info()
    if account is None or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise RuntimeError('Expected the configured demo account; no history exported')
    info = mt5.symbol_info(cfg.SYMBOL)
    if info is None:
        raise RuntimeError('Configured symbol unavailable')
    if not info.visible:
        if not mt5.symbol_select(cfg.SYMBOL, True):
            raise RuntimeError('Symbol selection failed')
    rates = None
    for count in (30000, 10000, 5000, 1000):
        candidate = mt5.copy_rates_from_pos(cfg.SYMBOL, mt5.TIMEFRAME_M15, 1, count)
        if candidate is not None and len(candidate) > 201:
            rates = candidate
            break
    if rates is None:
        raise RuntimeError(f'Historical rates unavailable: {mt5.last_error()}')
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df.to_csv(out / 'gold_m15.csv', index=False)
    tick = mt5.symbol_info_tick(cfg.SYMBOL)
    ref = tick.ask if tick and tick.ask > 0 else float(df.close.iloc[-1])
    profit = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, cfg.SYMBOL, info.volume_min, ref, ref - 1.0)
    metadata = dict(symbol=cfg.SYMBOL, timeframe='M15', source='Configured MT5 demo broker',
        exported_utc=datetime.now(timezone.utc).isoformat(), bars=len(df),
        first=str(df.time.iloc[0]), last=str(df.time.iloc[-1]), account_currency=account.currency,
        leverage=account.leverage, volume_min=info.volume_min, volume_step=info.volume_step,
        volume_max=info.volume_max, contract_size=info.trade_contract_size, point=info.point,
        value_per_price_per_lot=(-profit / info.volume_min if profit is not None else None),
        spread_price_median=float(df.spread.median()*info.point),
        spread_price_p90=float(df.spread.quantile(.9)*info.point))
    (out / 'metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(json.dumps(metadata, indent=2))
finally:
    mt5.shutdown()
