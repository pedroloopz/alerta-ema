"""Temporario: diagnostico dos candles das acoes na BingX."""
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import dados  # noqa: E402
import swing  # noqa: E402

for s in ("NCSKNVDA2USD-USDT", "NCSKAMD2USD-USDT", "NCSKMU2USD-USDT"):
    for iv in ("15m", "30m", "1h"):
        try:
            d = dados.bx("/openApi/swap/v3/quote/klines", symbol=s, interval=iv, limit=1000)
            df = pd.DataFrame(d)
            t = pd.to_datetime(df["time"].astype("int64"), unit="ms", utc=True).sort_values()
            vol = df["volume"].astype(float)
            print(s, iv, "n", len(df), t.min(), "->", t.max(), "vol zero:", int((vol == 0).sum()))
            ny = t.dt.tz_convert("America/New_York")
            print("   horas NY com candle:", sorted(ny.dt.hour.value_counts().index.tolist()))
            print("   dias da semana:", sorted(ny.dt.weekday.value_counts().index.tolist()))
        except Exception as e:
            print(s, iv, "falhou", e)
        time.sleep(1)
    df15 = dados.klines(s, dias=21)
    print("klines dias=21:", None if df15 is None else (len(df15), df15.index.min(), df15.index.max()))
    h = swing.acao_1h(df15)
    print("acao_1h:", None if h is None else (len(h), h.index.min(), h.index.max()))
    time.sleep(1)
