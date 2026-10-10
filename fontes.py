"""Fontes de dados com reserva (FASE 2). Falha de uma fonte: usa a outra e registra no log,
sem aviso no Telegram.

- Acoes (EUA e B3, sufixo .SA): yfinance; reserva = API de grafico do Yahoo chamada direto
  (cobre a quebra da biblioteca yfinance, o problema mais comum; se o proprio Yahoo cair,
  nao ha reserva gratuita: a Stooq saiu do ar em 2026).
- Cripto (ASTER, BTC): ccxt na Binance spot pelo espelho publico data-api.binance.vision
  (api.binance.com bloqueia os servidores do GitHub, nos EUA); reserva = mesma API sem ccxt.
"""
import time

import pandas as pd
import requests

VISION = "https://data-api.binance.vision"
COLUNAS = ["Open", "High", "Low", "Close", "Volume"]
_ccxt = None


def _log(msg):
    print(f"[fontes] {msg}")


# ================================================================ ACOES
def yahoo_direto(ticker, periodo="1y", intervalo="1d"):
    """OHLCV pela API de grafico do Yahoo, sem a biblioteca yfinance. Precos ajustados."""
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                     params={"range": periodo, "interval": intervalo, "events": "div,splits"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({"Open": q["open"], "High": q["high"], "Low": q["low"],
                       "Close": q["close"], "Volume": q["volume"]},
                      index=pd.to_datetime(res["timestamp"], unit="s"))
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    if adj:  # ajusta OHLC por dividendos/desdobramentos, como o auto_adjust do yfinance
        fator = pd.Series(adj, index=df.index) / df["Close"]
        for c in ("Open", "High", "Low", "Close"):
            df[c] = df[c] * fator
    if intervalo == "1d":
        df.index = df.index.normalize()
    return df.dropna()


def completar_acoes(saida, pedidos, periodo="1y", minimo=60):
    """Tickers que o yfinance nao trouxe: tenta a reserva (Yahoo direto), ate 250 por vez."""
    faltam = [t for t in sorted(set(pedidos)) if t not in saida]
    if not faltam:
        return saida
    recuperados = []
    for t in faltam[:250]:
        try:
            df = yahoo_direto(t, periodo)
            if len(df) >= minimo:
                saida[t] = df
                recuperados.append(t)
        except Exception:
            pass
    _log(f"yfinance sem {len(faltam)} ticker(s); reserva Yahoo direto recuperou {len(recuperados)}"
         + (f": {', '.join(recuperados[:15])}" if recuperados else ""))
    return saida


# ================================================================ CRIPTO
def _binance_ccxt():
    global _ccxt
    if _ccxt is None:
        import ccxt
        ex = ccxt.binance({"options": {"fetchMarkets": {"types": ["spot"]}}})
        for k, v in list(ex.urls["api"].items()):
            if isinstance(v, str) and v.startswith("https://api.binance.com/api/v3"):
                ex.urls["api"][k] = v.replace("https://api.binance.com", VISION)
        _ccxt = ex
    return _ccxt


def _para_df(linhas):
    df = pd.DataFrame(linhas).iloc[:, :6].astype(float)
    df.columns = ["t"] + COLUNAS
    df.index = pd.to_datetime(df["t"], unit="ms")
    return df.drop(columns="t")


def _rest(par, intervalo, n, inicio=None):
    params = {"symbol": par, "interval": intervalo, "limit": min(n, 1000)}
    if inicio is not None:
        params["startTime"] = inicio
    r = requests.get(f"{VISION}/api/v3/klines", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def cripto(par, intervalo="1d", n=400, inicio=None):
    """Candles da Binance spot (par no formato ASTERUSDT). ccxt; reserva: REST direto."""
    try:
        ex = _binance_ccxt()
        simbolo = par[:-4] + "/" + par[-4:] if par.endswith("USDT") else par
        linhas = ex.fetch_ohlcv(simbolo, intervalo, since=inicio, limit=min(n, 1000))
        if not linhas:
            raise ValueError("ccxt devolveu vazio")
        return _para_df(linhas)
    except Exception as e:
        _log(f"ccxt falhou em {par} {intervalo} ({type(e).__name__}: {str(e)[:120]}); usando REST direto")
    for tentativa in range(3):
        try:
            return _para_df(_rest(par, intervalo, n, inicio))
        except Exception as e:
            if tentativa == 2:
                raise
            time.sleep(5)


def cripto_desde(par, intervalo, inicio_ms):
    """Historico completo desde inicio_ms, em blocos de 1000 candles."""
    partes, ini = [], inicio_ms
    while True:
        df = cripto(par, intervalo, 1000, ini)
        if df.empty:
            break
        partes.append(df)
        ini = int(df.index[-1].timestamp() * 1000) + 1
        if len(df) < 1000:
            break
    if not partes:
        return pd.DataFrame(columns=COLUNAS)
    df = pd.concat(partes)
    return df[~df.index.duplicated(keep="last")]
