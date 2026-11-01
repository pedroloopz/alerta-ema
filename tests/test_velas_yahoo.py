import os
import sys
import types

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kronos"))


def test_velas_yahoo_24h_e_4h(monkeypatch):
    os.environ["KRONOS_INTERVALO"] = "4h"
    import importlib
    import prever as P
    P = importlib.reload(P)
    idx = pd.date_range("2026-10-26 13:30", periods=7, freq="h", tz="America/New_York")   # 1 pregão de 7 velas
    hist = pd.DataFrame({"Open": np.arange(7) + 100.0, "High": np.arange(7) + 101.0, "Low": np.arange(7) + 99.0,
                         "Close": np.arange(7) + 100.5, "Volume": [10.0] * 7}, index=idx)
    fake = types.SimpleNamespace(Ticker=lambda t: types.SimpleNamespace(history=lambda **k: hist))
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    df = P.velas_yahoo("NCSKXOM2USD-USDT", 100)
    assert (df["t"] % (4 * 3600_000) == 0).all()                         # alinhado em 00h/04h/08h UTC
    assert df["close"].iloc[-1] == 106.5                                   # fora do pregão: parado no último preço
    assert (df["high"] >= df["low"]).all() and df["volume"].iloc[-1] == 0
    assert P.ticker_yahoo("NCSKTOKYOEL2USD-USDT") == "8035.T" and P.ticker_yahoo("NCSIGER2USD-USDT") == "^GDAXI"
    os.environ.pop("KRONOS_INTERVALO")
    importlib.reload(P)
