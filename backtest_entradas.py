"""Backtest de 2 anos das 2 entradas do estudo (19/10/2026), em cripto e ações. Só leitura: não opera nada.

Entrada 1 — continuação em tendência (vela de 1h):
  tendência = fechamento acima da EMA 50 e EMA 50 subindo nas últimas 24 velas (short: espelho);
  recuo = a vela anterior tocou a EMA 20 sem fechar abaixo da EMA 50; gatilho = fechamento acima da máxima anterior.
  Saída como o robô: stop 3%, alvos 1,5/2/2,5/3% (25/25/20/10%), stop na entrada após o 1º alvo, resto sai em 12 velas.
Entrada 2 — força relativa (vela diária): ordena pelo retorno de L dias, long nas K mais fortes e short nas K mais
  fracas, segura H dias; stop no fechamento diário a 10% contra (perfil diário do robô).
Custos: 0,1% por lado (taxa + deslize) e funding de 0,01% a cada 8h contra a posição.
Dados: cripto pela Binance spot (data-api.binance.vision), ações pelo Yahoo (1h = ~730 dias).
Saída: relatorio_entradas.txt."""
import datetime
import math
import time

import numpy as np
import pandas as pd
import requests

BINANCE = "https://data-api.binance.vision/api/v3"
CUSTO_LADO = 0.001
FUNDING_8H = 0.0001
SL = 0.03
ALVOS = [(0.015, 0.25), (0.02, 0.25), (0.025, 0.20), (0.03, 0.10)]
SAIDA_VELAS = 12
ESTAVEIS = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "EUR", "AEUR", "USDE", "BFUSD", "XUSD", "USD1", "PAXG", "WBTC",
            "WBETH", "BUSD", "EURI", "RLUSD", "USDS"}
ACOES = {
    "neoclouds": ["CRWV", "NBIS", "IREN", "APLD", "CIFR", "WULF", "HUT", "CORZ", "GLXY", "BTDR"],
    "petróleo": ["XOM", "CVX", "OXY", "COP", "SLB", "HAL", "DVN", "EOG", "MPC", "VLO", "USO", "XLE"],
    "top 20 EUA": ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRK-B", "JPM", "LLY", "V", "WMT",
                   "ORCL", "MA", "NFLX", "COST", "AMD", "PLTR", "UNH"],
    "defesa": ["LMT", "NOC", "RTX", "GD", "LHX", "HII", "KTOS", "AVAV"],
    "energia elétrica": ["CEG", "VST", "NRG", "TLN", "NEE", "SO", "DUK", "OKLO"],
    "índices": ["QQQ", "SPY", "IWM", "DIA", "EWJ", "EWG", "EWY"],
    "commodities": ["GLD", "SLV", "PALL", "PPLT", "CPER", "UNG"],
}
OUT = []


def p(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    OUT.append(s)


# ---------------------------------------------------------------- dados
def binance_top(n=60):
    r = requests.get(f"{BINANCE}/ticker/24hr", timeout=30).json()
    pares = []
    for x in r:
        s = x["symbol"]
        if not s.endswith("USDT"):
            continue
        base = s[:-4]
        if base in ESTAVEIS or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue
        pares.append((float(x["quoteVolume"]), s))
    return [s for _, s in sorted(pares, reverse=True)[:n]]


def binance_velas(simbolo, intervalo, desde_ms):
    linhas, t = [], desde_ms
    while True:
        for tentativa in range(3):
            try:
                r = requests.get(f"{BINANCE}/klines", params={"symbol": simbolo, "interval": intervalo,
                                                               "startTime": t, "limit": 1000}, timeout=30)
                r.raise_for_status()
                lote = r.json()
                break
            except Exception:
                time.sleep(2 + 3 * tentativa)
        else:
            break
        if not lote:
            break
        linhas += lote
        t = lote[-1][0] + 1
        if len(lote) < 1000:
            break
    if not linhas:
        return None
    df = pd.DataFrame(linhas, columns=list(range(12)))
    df = pd.DataFrame({"open": df[1].astype(float), "high": df[2].astype(float), "low": df[3].astype(float),
                       "close": df[4].astype(float), "volume": df[7].astype(float)},
                      index=pd.to_datetime(df[0], unit="ms", utc=True))
    return df[~df.index.duplicated()]


def yahoo(tickers, intervalo, periodo):
    import yfinance as yf
    d = yf.download(tickers, interval=intervalo, period=periodo, group_by="ticker", auto_adjust=True,
                    progress=False, threads=True)
    out = {}
    for t in tickers:
        try:
            x = d[t][["Open", "High", "Low", "Close", "Volume"]].dropna()
        except Exception:
            continue
        x.columns = ["open", "high", "low", "close", "volume"]
        if len(x) > 50:
            out[t] = x
    return out


# ---------------------------------------------------------------- entrada 1
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def sinais_continuacao(df, atr_max):
    c, h, lo = df["close"], df["high"], df["low"]
    e20, e50 = ema(c, 20), ema(c, 50)
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean() / c
    ok_vol = atr <= atr_max
    long_ = ((c > e50) & (e50 > e50.shift(24)) & (lo.shift() <= e20.shift()) & (c.shift() > e50.shift())
             & (c > h.shift()) & ok_vol)
    short = ((c < e50) & (e50 < e50.shift(24)) & (h.shift() >= e20.shift()) & (c.shift() < e50.shift())
             & (c < lo.shift()) & ok_vol)
    return long_.fillna(False).values, short.fillna(False).values


def simular(df, i, lado, horas_por_vela):
    """Trade aberto no fechamento da vela i. Devolve (R líquido, índice da saída)."""
    o, h, lo, c = (df[k].values for k in ("open", "high", "low", "close"))
    s = 1 if lado == "LONG" else -1
    ent = c[i] * (1 + s * CUSTO_LADO)               # entra pagando o custo
    stop = ent * (1 - s * SL)
    resto, ganho, alvo_i, j = 1.0, 0.0, 0, i
    fim = min(i + SAIDA_VELAS, len(c) - 1)
    for j in range(i + 1, fim + 1):
        # stop primeiro (conservador); gap além do stop sai na abertura
        if (s == 1 and lo[j] <= stop) or (s == -1 and h[j] >= stop):
            px = o[j] if (s == 1 and o[j] < stop) or (s == -1 and o[j] > stop) else stop
            ganho += resto * s * (px * (1 - s * CUSTO_LADO) / ent - 1)
            resto = 0.0
            break
        while alvo_i < len(ALVOS):
            dist, fatia = ALVOS[alvo_i]
            alvo = ent * (1 + s * dist)
            if (s == 1 and h[j] >= alvo) or (s == -1 and lo[j] <= alvo):
                ganho += fatia * s * (alvo * (1 - s * CUSTO_LADO) / ent - 1)
                resto -= fatia
                alvo_i += 1
                if alvo_i == 1:
                    stop = ent                        # stop na entrada após o 1º alvo
            else:
                break
        if resto <= 1e-9:
            break
    if resto > 1e-9:
        ganho += resto * s * (c[j] * (1 - s * CUSTO_LADO) / ent - 1)
    horas = (j - i) * horas_por_vela
    ganho -= FUNDING_8H * horas / 8
    return ganho / SL, j


def backtest_continuacao(dados, classe, atr_max, horas_por_vela, lados=("LONG", "SHORT")):
    trades = []
    for nome, df in dados.items():
        if len(df) < 200:
            continue
        lg, sh = sinais_continuacao(df, atr_max)
        livre = 0
        for i in range(60, len(df) - 1):
            if i < livre:
                continue
            for lado, m in (("LONG", lg), ("SHORT", sh)):
                if lado in lados and m[i]:
                    r, j = simular(df, i, lado, horas_por_vela)
                    trades.append({"ativo": nome, "classe": classe, "lado": lado, "t": df.index[i],
                                   "saida": df.index[j], "r": r})
                    livre = j + 1
                    break
    return pd.DataFrame(trades)


def resumo_trades(t, titulo, semanas):
    if t is None or t.empty:
        p(f"{titulo}: nenhum trade")
        return
    t = t.sort_values("saida")
    g, l = t.r[t.r > 0].sum(), -t.r[t.r < 0].sum()
    curva = t.r.cumsum()
    dd = (curva.cummax() - curva).max()
    p(f"{titulo}: {len(t)} trades ({len(t) / semanas:.1f}/semana) · acerto {100 * (t.r > 0).mean():.0f}% · "
      f"média {t.r.mean():+.3f}R · total {t.r.sum():+.1f}R · fator de lucro {g / l if l else float('inf'):.2f} · "
      f"maior queda {dd:.1f}R")


def por_semestre(t):
    if t is None or t.empty:
        return
    t = t.copy()
    t["sem"] = t["t"].dt.year.astype(str) + "-S" + ((t["t"].dt.month > 6) + 1).astype(str)
    linhas = [f"{k}: {len(x)} tr, {x.r.mean():+.3f}R, total {x.r.sum():+.1f}R" for k, x in t.groupby("sem")]
    p("   por semestre → " + " | ".join(linhas))


# ---------------------------------------------------------------- entrada 2
def backtest_forca(diarios, L, H, K, stop=0.10, funding=True, so_long=False):
    fech = pd.DataFrame({k: v["close"] for k, v in diarios.items()}).sort_index()
    datas = fech.index
    rets, legs_l, legs_s = [], [], []
    i = L
    while i + 1 < len(datas):
        ret = fech.iloc[i] / fech.iloc[i - L] - 1
        ret = ret.dropna()
        if len(ret) < 2 * K + 2:
            i += H
            continue
        ordem = ret.sort_values()
        fortes, fracos = list(ordem.index[-K:]), ([] if so_long else list(ordem.index[:K]))
        fim = min(i + H, len(datas) - 1)
        pernas = []
        for ativo, s in [(a, 1) for a in fortes] + [(a, -1) for a in fracos]:
            ent = fech[ativo].iloc[i]
            saida, dias = fech[ativo].iloc[fim], fim - i
            for d in range(i + 1, fim + 1):
                px = fech[ativo].iloc[d]
                if np.isnan(px):
                    continue
                if s * (px / ent - 1) <= -stop:
                    saida, dias = px, d - i
                    break
            if np.isnan(saida) or np.isnan(ent):
                continue
            r = s * (saida / ent - 1) - 2 * CUSTO_LADO - (FUNDING_8H * 3 * dias if funding else 0)
            pernas.append((s, r))
        if pernas:
            rets.append((datas[i], np.mean([r for _, r in pernas])))
            legs_l += [r for s, r in pernas if s == 1]
            legs_s += [r for s, r in pernas if s == -1]
        i += H
    if not rets:
        return None
    r = pd.Series({d: x for d, x in rets})
    curva = (1 + r).cumprod()
    anos = (r.index[-1] - r.index[0]).days / 365 or 1
    cagr = curva.iloc[-1] ** (1 / anos) - 1
    sharpe = r.mean() / r.std() * math.sqrt(365 / H) if r.std() > 0 else 0
    dd = (1 - curva / curva.cummax()).max()
    return {"periodos": len(r), "cagr": cagr, "sharpe": sharpe, "dd": dd, "pos": (r > 0).mean(),
            "long": np.mean(legs_l) if legs_l else 0, "short": np.mean(legs_s) if legs_s else 0}


def linha_forca(nome, x):
    if not x:
        p(f"{nome}: sem dados")
        return
    p(f"{nome}: {x['periodos']} períodos · {100 * x['cagr']:+.0f}% ao ano · Sharpe {x['sharpe']:.2f} · "
      f"maior queda {100 * x['dd']:.0f}% · {100 * x['pos']:.0f}% períodos positivos · "
      f"média por perna: long {100 * x['long']:+.2f}% / short {100 * x['short']:+.2f}%")


# ---------------------------------------------------------------- main
def main():
    global CUSTO_LADO, FUNDING_8H
    agora = datetime.datetime.now(datetime.timezone.utc)
    desde = int((agora - datetime.timedelta(days=740)).timestamp() * 1000)
    p(f"# Backtest das 2 entradas do estudo — {agora:%d/%m/%Y %H:%M} UTC")
    p(f"Custos: {100 * CUSTO_LADO:.1f}% por lado + funding {100 * FUNDING_8H:.2f}%/8h contra a posição. "
      "R = resultado ÷ risco de 3%.")

    pares = binance_top(60)
    cripto_1h, cripto_1d = {}, {}
    for s in pares:
        d1 = binance_velas(s, "1h", desde)
        if d1 is not None and len(d1) > 24 * 120:
            cripto_1h[s[:-4]] = d1
        dd = binance_velas(s, "1d", desde)
        if dd is not None and len(dd) > 120:
            cripto_1d[s[:-4]] = dd
    tick = sorted({t for v in ACOES.values() for t in v})
    acoes_1h = yahoo(tick, "1h", "730d")
    acoes_1d = yahoo(tick, "1d", "2y")
    p(f"Dados: {len(cripto_1h)} criptos (1h), {len(cripto_1d)} (diário) · {len(acoes_1h)} ações/ETFs (1h), "
      f"{len(acoes_1d)} (diário)")
    p("Atenção: universo de hoje (as criptos que sobreviveram) puxa o resultado para cima; ações no Yahoo só têm "
      "o pregão normal (6,5 velas de 1h por dia), na BingX o contrato roda 24h.")

    def semanas(dados):
        ini = min(v.index[0] for v in dados.values())
        fim = max(v.index[-1] for v in dados.values())
        return max((fim - ini).days / 7, 1)

    p("\n## Entrada 1 — continuação em tendência (1h)")
    for classe, dados, hv, atrs in (("cripto", cripto_1h, 1, (0.012, 0.02)), ("ações", acoes_1h, 1, (0.01, 0.02))):
        sem = semanas(dados)
        for atr_max in atrs:
            t = backtest_continuacao(dados, classe, atr_max, hv)
            resumo_trades(t, f"{classe} · ATR ≤ {100 * atr_max:.1f}% · long+short", sem)
            guardado = (CUSTO_LADO, FUNDING_8H)
            CUSTO_LADO, FUNDING_8H = 0.0, 0.0
            resumo_trades(backtest_continuacao(dados, classe, atr_max, hv), "   sem custos (borda bruta)", sem)
            CUSTO_LADO, FUNDING_8H = guardado
            if not t.empty:
                for lado in ("LONG", "SHORT"):
                    resumo_trades(t[t.lado == lado], f"   só {lado.lower()}", sem)
                por_semestre(t)
                t.to_csv(f"entradas_continuacao_{classe}_{int(atr_max * 1000)}.csv", index=False)

    p("\n## Entrada 2 — força relativa (diário), stop 10% no fechamento")
    for classe, dados, variantes in (
            ("cripto", cripto_1d, [(14, 14, 3), (14, 14, 5), (7, 7, 3), (28, 14, 3)]),
            ("ações", acoes_1d, [(14, 14, 3), (14, 14, 5), (63, 14, 3), (126, 21, 5)])):
        for L, H, K in variantes:
            linha_forca(f"{classe} · ordena {L}d, segura {H}d, {K}+{K}", backtest_forca(dados, L, H, K))
        L, H, K = variantes[0]
        linha_forca(f"{classe} · só long ({L}d/{H}d, top {K})", backtest_forca(dados, L, H, K, so_long=True))

    open("relatorio_entradas.txt", "w", encoding="utf-8").write("\n".join(OUT) + "\n")


if __name__ == "__main__":
    main()
