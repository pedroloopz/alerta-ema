"""
COLETA (roda no GitHub Actions): listas de tokens e regras de ordem.
Nao decide nada: so salva as respostas brutas em saida/ para conferencia.
"""
import json
import os
import time
from datetime import datetime, timezone

import requests

OUT = os.environ.get("SAIDA", "saida")
os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept": "application/json, text/html;q=0.9"}
log = []


def pegar(nome, url, params=None, metodo="GET", corpo=None, ext=None):
    try:
        if metodo == "POST":
            r = requests.post(url, json=corpo, headers=UA, timeout=40)
        else:
            r = requests.get(url, params=params, headers=UA, timeout=40)
        tipo = r.headers.get("content-type", "")
        ext = ext or ("json" if "json" in tipo else "html")
        with open(f"{OUT}/{nome}.{ext}", "w", encoding="utf-8") as f:
            f.write(r.text)
        log.append(f"{nome}: {r.status_code} {len(r.text)} bytes {url}")
        return r
    except Exception as e:
        log.append(f"{nome}: ERRO {e} {url}")
        return None


# ---- Ondo: documentacao e site
pegar("ondo_docs_available_assets", "https://docs.ondo.finance/ondo-global-markets/available-assets")
pegar("ondo_docs_available_assets_md", "https://docs.ondo.finance/ondo-global-markets/available-assets.md", ext="md")
pegar("ondo_docs_llms", "https://docs.ondo.finance/llms.txt", ext="txt")
pegar("ondo_stocks_site", "https://ondo.finance/ondo-stocks")
pegar("ondo_readme_metadata", "https://ondo-finance.readme.io/reference/getallassetmetadata")
for i, u in enumerate(["https://api.gm.ondo.finance/v1/assets", "https://api.gm.ondo.finance/v1/assets/metadata",
                       "https://api.ondo.finance/v1/assets", "https://gm-api.ondo.finance/v1/assets"]):
    pegar(f"ondo_api_tentativa_{i}", u)

# ---- Binance Alpha / Wallet (endpoints publicos usados pelo proprio site)
pegar("binance_alpha_tokens",
      "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list")
pegar("binance_bstocks_landing", "https://www.binance.com/en/bstocks-landing")

# ---- Binance spot: regras de ordem da ASTER e do BTC
pegar("binance_exchangeinfo_aster", "https://api.binance.com/api/v3/exchangeInfo",
      params={"symbols": '["ASTERUSDT","BTCUSDT"]'})
pegar("binance_klines_aster_teste", "https://data-api.binance.vision/api/v3/klines",
      params={"symbol": "ASTERUSDT", "interval": "1d", "limit": 5})

# ---- CoinGecko: categorias de acoes tokenizadas e membros
r = pegar("cg_categorias", "https://api.coingecko.com/api/v3/coins/categories/list")
ids = []
try:
    for c in r.json():
        n = (c.get("category_id", "") + " " + c.get("name", "")).lower()
        if any(k in n for k in ("ondo", "bstock", "tokenized-stock", "tokenized stock", "xstock")):
            ids.append(c["category_id"])
except Exception as e:
    log.append(f"cg_categorias parse: {e}")
log.append(f"categorias escolhidas: {ids}")
for cid in ids:
    for pag in range(1, 5):
        time.sleep(7)
        rr = pegar(f"cg_{cid}_p{pag}", "https://api.coingecko.com/api/v3/coins/markets",
                   params={"vs_currency": "usd", "category": cid, "per_page": 250, "page": pag})
        try:
            if rr is None or len(rr.json()) < 250:
                break
        except Exception:
            break
time.sleep(7)
pegar("cg_coins_list_plataformas", "https://api.coingecko.com/api/v3/coins/list",
      params={"include_platform": "true"})

# ---- Wikipedia: S&P 500 e Nasdaq-100 (setores GICS)
pegar("wiki_sp500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
pegar("wiki_ndx", "https://en.wikipedia.org/wiki/Nasdaq-100")

with open(f"{OUT}/_log.txt", "w") as f:
    f.write(f"coleta {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n" + "\n".join(log) + "\n")
print("\n".join(log))
