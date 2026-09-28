"""
COLETA 3 (GitHub Actions): precos diarios e setores para o backtest final.
  - Yahoo: OHLCV ajustado desde 2013-06-01 dos candidatos + SPY, QQQ, BTC-USD
  - Yahoo: setor/industria/tipo de cada candidato
  - Binance spot (data-api.binance.vision): candles diarios ASTERUSDT e BTCUSDT
"""
import json
import os
import time

import pandas as pd
import requests
import yfinance as yf

OUT = os.environ.get("SAIDA", "saida")
os.makedirs(OUT, exist_ok=True)
AQUI = os.path.dirname(os.path.abspath(__file__))
cands = [c["ticker"] for c in json.load(open(f"{AQUI}/candidatos.json"))["candidatos"]]
tickers = sorted(set(cands + ["SPY", "QQQ", "BTC-USD"]))
log = [f"{len(tickers)} tickers"]

campos = {k: {} for k in ("Open", "High", "Low", "Close", "Volume")}
for i in range(0, len(tickers), 60):
    lote = tickers[i:i + 60]
    for tent in range(3):
        try:
            b = yf.download(lote, start="2013-06-01", auto_adjust=True, progress=False,
                            group_by="ticker", threads=True)
            break
        except Exception as e:
            log.append(f"lote {i}: {e}")
            time.sleep(20)
    for t in lote:
        try:
            df = b[t]
        except KeyError:
            continue
        for k in campos:
            campos[k][t] = df[k]
for k, d in campos.items():
    pd.DataFrame(d).to_csv(f"{OUT}/yahoo_{k.lower()}.csv.gz", float_format="%.6g")
fechou = pd.DataFrame(campos["Close"])
log.append(f"precos: {fechou.shape}, ultima data {fechou.index.max()}")
log.append("sem dados: " + " ".join(t for t in tickers if t not in fechou or fechou[t].notna().sum() == 0))

info = {}
for t in cands:
    try:
        x = yf.Ticker(t).info
        info[t] = {k: x.get(k) for k in ("sector", "industry", "quoteType", "longName",
                                          "averageVolume", "marketCap", "firstTradeDateEpochUtc")}
    except Exception as e:
        info[t] = {"erro": str(e)[:200]}
json.dump(info, open(f"{OUT}/yahoo_info.json", "w"), indent=0)
log.append(f"info: {sum(1 for v in info.values() if v.get('sector'))} com setor")


def klines(par):
    out, ini = [], int(pd.Timestamp("2017-01-01").timestamp() * 1000)
    while True:
        r = requests.get("https://data-api.binance.vision/api/v3/klines",
                         params={"symbol": par, "interval": "1d", "startTime": ini, "limit": 1000}, timeout=30)
        k = r.json()
        if not k:
            break
        out += k
        ini = k[-1][0] + 86_400_000
        if len(k) < 1000:
            break
    df = pd.DataFrame(out).iloc[:, :6].astype(float)
    df.columns = ["t", "Open", "High", "Low", "Close", "Volume"]
    df.index = pd.to_datetime(df["t"], unit="ms")
    df.drop(columns="t").to_csv(f"{OUT}/binance_{par}.csv")
    log.append(f"{par}: {len(df)} dias, {df.index[0]:%Y-%m-%d} a {df.index[-1]:%Y-%m-%d}")


for par in ("ASTERUSDT", "BTCUSDT"):
    try:
        klines(par)
    except Exception as e:
        log.append(f"{par}: ERRO {e}")

open(f"{OUT}/_log_precos.txt", "w").write("\n".join(log) + "\n")
print("\n".join(log))
