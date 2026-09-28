"""
COLETA (GitHub Actions): candles de 4h da ONDOUSDT (e BTCUSDT como regime).
Janela: 2025-06-01 ate agora (6 meses de analise + aquecimento para medias longas).
Fontes em ordem: Binance spot (data-api.binance.vision), Binance spot (api), OKX.
"""
import os
import time

import pandas as pd
import requests

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ondo")
os.makedirs(OUT, exist_ok=True)
INI = int(pd.Timestamp("2025-06-01", tz="UTC").timestamp() * 1000)
COLS = ["t", "open", "high", "low", "close", "volume", "ct", "qvol", "trades", "tbb", "tbq", "x"]


def binance(base, par, tf):
    out, ini = [], INI
    while True:
        r = requests.get(f"{base}/api/v3/klines",
                         params={"symbol": par, "interval": tf, "startTime": ini, "limit": 1000}, timeout=30)
        r.raise_for_status()
        k = r.json()
        if not k:
            break
        out += k
        ini = k[-1][0] + 1
        if len(k) < 1000:
            break
        time.sleep(0.3)
    df = pd.DataFrame(out, columns=COLS)[["t", "open", "high", "low", "close", "volume", "qvol", "trades", "tbb"]]
    return df


def okx(par, tf):
    inst = par.replace("USDT", "-USDT")
    bar = {"4h": "4H", "1d": "1Dutc"}[tf]
    out, after = [], ""
    while True:
        p = {"instId": inst, "bar": bar, "limit": 100}
        if after:
            p["after"] = after
        k = requests.get("https://www.okx.com/api/v5/market/history-candles", params=p, timeout=30).json()["data"]
        if not k:
            break
        out += k
        after = k[-1][0]
        if int(after) < INI:
            break
        time.sleep(0.2)
    df = pd.DataFrame(out, columns=["t", "open", "high", "low", "close", "volume", "qvol", "x", "conf"])
    df = df[["t", "open", "high", "low", "close", "volume", "qvol"]].astype(float)
    return df[df.t >= INI].sort_values("t")


log = []
for par in ("ONDOUSDT", "BTCUSDT"):
    for tf in ("4h", "1d"):
        df = None
        for nome, f in (("binance.vision", lambda: binance("https://data-api.binance.vision", par, tf)),
                        ("binance", lambda: binance("https://api.binance.com", par, tf)),
                        ("okx", lambda: okx(par, tf))):
            try:
                df = f()
                if len(df):
                    log.append(f"{par} {tf}: {len(df)} candles via {nome}")
                    break
            except Exception as e:
                log.append(f"{par} {tf}: {nome} falhou: {e}")
        if df is not None and len(df):
            df["data"] = pd.to_datetime(df.t.astype("int64"), unit="ms", utc=True)
            df.to_csv(f"{OUT}/{par}_{tf}.csv", index=False)
open(f"{OUT}/log.txt", "w").write("\n".join(log) + "\n")
print("\n".join(log))
