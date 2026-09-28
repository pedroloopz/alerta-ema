"""COLETA 4: preco atual dos tokens (Ondo, Binance Wallet, bStocks) x acao no Yahoo."""
import json, os, re
import requests, yfinance as yf
OUT = os.environ.get("SAIDA", "saida"); os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124", "Accept": "application/json, text/html"}
log = []
r = requests.get("https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list", headers=UA, timeout=40)
open(f"{OUT}/alpha_agora.json", "w").write(r.text)
h = requests.get("https://ondo.finance/ondo-stocks", headers=UA, timeout=60).text
open(f"{OUT}/ondo_site_agora.html", "w").write(h)
r = requests.get("https://api.coingecko.com/api/v3/coins/markets", params={"vs_currency": "usd", "category": "bstocks-ecosystem", "per_page": 250}, headers=UA, timeout=40)
open(f"{OUT}/bstocks_agora.json", "w").write(r.text)
for t in ["UNH", "NFLX", "ABNB", "BE", "UBER", "ENPH", "MVLL"]:
    try:
        df = yf.Ticker(t).history(period="2d", interval="5m", prepost=True)
        log.append(f"{t}: ultimo {df.index[-1]} {df['Close'].iloc[-1]:.2f} | fech {yf.Ticker(t).history(period='5d')['Close'].iloc[-1]:.2f}")
    except Exception as e:
        log.append(f"{t}: {e}")
open(f"{OUT}/_log_tokens.txt", "w").write("\n".join(log) + "\n"); print("\n".join(log))
