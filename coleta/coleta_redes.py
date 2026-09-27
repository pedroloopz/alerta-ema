"""
COLETA 2 (GitHub Actions): em qual rede cada token existe (CoinGecko,
include_platform) e regras de ordem da ASTER na Binance spot.
"""
import os
import time

import requests

OUT = os.environ.get("SAIDA", "saida")
os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
log = []


def pegar(nome, url, params=None, tentativas=4):
    for k in range(tentativas):
        r = requests.get(url, params=params, headers=UA, timeout=60)
        log.append(f"{nome}: {r.status_code} {len(r.text)} bytes")
        if r.status_code != 429:
            break
        time.sleep(65)
    with open(f"{OUT}/{nome}.json", "w", encoding="utf-8") as f:
        f.write(r.text)


pegar("cg_coins_list_plataformas", "https://api.coingecko.com/api/v3/coins/list",
      params={"include_platform": "true"})
pegar("binance_vision_exchangeinfo", "https://data-api.binance.vision/api/v3/exchangeInfo",
      params={"symbols": '["ASTERUSDT","BTCUSDT"]'})
with open(f"{OUT}/_log_redes.txt", "w") as f:
    f.write("\n".join(log) + "\n")
print("\n".join(log))
