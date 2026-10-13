"""Dados públicos (sem chave): BingX perpétuos, CoinGecko top 300 e long/short ratio da Gate.

Universo = cripto do top 300 da CoinGecko com perpétuo USDT na BingX
         + todas as ações em perpétuo na BingX (prefixo NCSK) que estão negociando,
         com volume de 24h acima de VOL_MIN.
"""
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

BX = "https://open-api.bingx.com"
VOL_MIN = 1_000_000          # US$ negociados em 24h
QUINZE = 15 * 60 * 1000


def _get(url, params=None, tentativas=3):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for t in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 painel-trade"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode())
        except Exception:
            if t == tentativas - 1:
                raise
            time.sleep(1.5 * (t + 1))


def bx(path, **params):
    js = _get(BX + path, params)
    if isinstance(js, dict) and js.get("code", 0) != 0:
        raise RuntimeError(f"BingX {path}: {js.get('code')} {js.get('msg')}")
    return js["data"]


def top300():
    """Símbolos (maiúsculos) e ranks do top 300 por valor de mercado na CoinGecko."""
    out = {}
    for page, per in ((1, 250), (2, 250)):
        js = _get("https://api.coingecko.com/api/v3/coins/markets",
                  {"vs_currency": "usd", "order": "market_cap_desc", "per_page": per, "page": page})
        for c in js:
            r = c.get("market_cap_rank")
            if r and r <= 300:
                out.setdefault(c["symbol"].upper(), r)
        time.sleep(2)
    return out


def universo():
    """Lista de dicts: simbolo, nome, classe (cripto/acao), rank, vol24h."""
    contratos = bx("/openApi/swap/v2/quote/contracts")
    ticker = {t["symbol"]: t for t in bx("/openApi/swap/v2/quote/ticker")}
    ativos = {c["symbol"]: c for c in contratos
              if c.get("currency") == "USDT" and c.get("status") == 1 and c.get("apiStateOpen") == "true"}
    out = []
    for s, c in ativos.items():
        if s.startswith("NCSK"):
            nome = c.get("displayName", s).replace("-USDT", "")
            out.append({"simbolo": s, "nome": nome, "classe": "acao", "rank": None})
    ranks = top300()
    for sym, rank in ranks.items():
        for pre in ("", "1000", "10000", "1000000"):
            s = f"{pre}{sym}-USDT"
            if s in ativos:
                out.append({"simbolo": s, "nome": sym, "classe": "cripto", "rank": rank})
                break
    for a in out:
        a["vol24h"] = float(ticker.get(a["simbolo"], {}).get("quoteVolume") or 0)
    return [a for a in out if a["vol24h"] >= VOL_MIN]


def klines(simbolo, dias=None, limite=300):
    """Candles de 15 min já fechados. Índice = horário de FECHAMENTO (UTC)."""
    linhas, fim = [], int(time.time() * 1000)
    alvo = fim - dias * 86_400_000 if dias else None
    while True:
        d = bx("/openApi/swap/v3/quote/klines", symbol=simbolo, interval="15m",
               limit=1000 if dias else limite, endTime=fim)
        if not d:
            break
        linhas += d
        menor = min(int(k["time"]) for k in d)
        if not dias or menor <= alvo or len(d) < 1000:
            break
        fim = menor - 1
    if not linhas:
        return None
    df = pd.DataFrame(linhas)
    df["time"] = pd.to_datetime(df["time"].astype("int64") + QUINZE, unit="ms", utc=True)
    df = df.astype({k: float for k in ("open", "high", "low", "close", "volume")})
    df = df.drop_duplicates("time").set_index("time").sort_index()[["open", "high", "low", "close", "volume"]]
    agora = pd.Timestamp.now(tz="UTC")
    df = df[df.index <= agora]                      # só candles fechados
    if alvo:
        df = df[df.index >= pd.Timestamp(alvo, unit="ms", tz="UTC")]
    return df


def em_paralelo(fn, itens, n=8):
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(fn, itens))


def funding():
    out = {}
    for p in bx("/openApi/swap/v2/quote/premiumIndex"):
        try:
            out[p["symbol"]] = float(p["lastFundingRate"])
        except Exception:
            pass
    return out


def long_short(cripto):
    """Long/short ratio de contas e variação do open interest em 4h (Gate.io, perpétuo USDT)."""
    try:
        d = _get("https://api.gateio.ws/api/v4/futures/usdt/contract_stats",
                 {"contract": f"{cripto}_USDT", "interval": "1h", "limit": 5}, tentativas=1)
        if not d:
            return None
        u, p = d[-1], d[0]
        def oiu(x):
            return float(x.get("open_interest_usd") or float(x.get("open_interest") or 0) * float(x.get("mark_price") or 0))
        oi = (oiu(u) / oiu(p) - 1) if oiu(p) else None
        return {"ls": round(float(u["lsr_account"]), 2), "oi_4h": round(oi, 4) if oi is not None else None}
    except Exception:
        return None
