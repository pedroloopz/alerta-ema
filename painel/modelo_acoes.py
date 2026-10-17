"""Pesquisa: modelo de chance para AÇÕES em perp da BingX (só preço, volume e mercado).

O modelo de fluxo das criptos usa dados da Gate (liquidações, OI, taker) que não existem para
ações. Aqui o mesmo método (gradient boosting, decisões de hora em hora, faixas medidas FORA da
amostra) é treinado só com o que existe para ações: preço e volume de 15 min da ação na bolsa
(Yahoo, 60 dias de pregão; o perp da BingX só tem ~10 dias de histórico) e o QQQ como mercado.
Inclui pré e pós-mercado (Yahoo, ~16h por dia útil). Testa 4 saídas e grava relatorio_ml_acoes.txt.

Não muda nada no painel nem no robô: é só o teste. Se alguma faixa der nota 8+ com amostra
grande, aí sim entra no scanner.

Uso (GitHub Actions, workflow painel-acoes): python painel/modelo_acoes.py [--max N]
"""
import argparse
import datetime
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view as swv

sys.path.insert(0, os.path.dirname(__file__))
import dados  # noqa: E402
import modelo  # noqa: E402
import nucleo as nc  # noqa: E402

AQUI = os.path.dirname(__file__)
SAIDA = os.path.join(AQUI, "..", "relatorio_ml_acoes.txt")
SAIDA_JSON = os.path.join(AQUI, "ml_acoes.json")
DIAS = 60
CONFIGS = {"A": (0.02, 0.03, 48), "B": (0.015, 0.02, 32), "C": (0.03, 0.03, 96),   # alvo, stop, candles de 15 min
           "D": ((0.015, 0.025, 0.035), 0.03, 48)}   # igual ao robô: 3 alvos de 1/3, stop -> entrada após o 1º

MAKER, TAKER_SLIP = modelo.MAKER, modelo.TAKER_SLIP
HORAS_PREGAO = range(8, 24)         # pré-mercado + pregão + pós-mercado (UTC), dias úteis


def yahoo_15m(ticker):
    import requests
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                     params={"range": "60d", "interval": "15m", "includePrePost": "true"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
                       "volume": q["volume"]}, index=pd.to_datetime(res["timestamp"], unit="s", utc=True))
    return df.dropna()


def baixar_acao(nome):
    for t in dict.fromkeys([nome, nome[:-2] if nome.endswith("US") else nome]):
        try:
            df = yahoo_15m(t)
            if len(df) > 1500:
                return t, df
        except Exception:
            pass
    return None, None


def features(df, mkt):
    f = pd.DataFrame(index=df.index)
    c, v = df["close"], df["volume"] * df["close"]
    lr = np.log(c)
    for h in (1, 4, 16, 96):
        f[f"ret_{h}"] = lr - lr.shift(h)
    vol = lr.diff().rolling(96).std()
    f["vol96"] = vol
    f["z1"] = f["ret_1"] / vol.shift()
    f["z4"] = f["ret_4"] / (vol.shift() * 2)
    f["volrel"] = v / v.rolling(96).median().shift()
    f["volrel_4"] = v.rolling(4).sum() / v.rolling(96 * 5, min_periods=96).median().shift() / 4
    f["rng"] = (df["high"] - df["low"]) / c / vol.shift()
    f["pos96"] = (c - df["low"].rolling(96).min()) / (df["high"].rolling(96).max() - df["low"].rolling(96).min())
    f["pos480"] = (c - df["low"].rolling(480).min()) / (df["high"].rolling(480).max() - df["low"].rolling(480).min())
    m = np.log(mkt.reindex(df.index).ffill())
    for h in (4, 16, 96):
        f[f"mkt_{h}"] = m - m.shift(h)
    f["rel_16"] = f["ret_16"] - f["mkt_16"]
    f["rel_96"] = f["ret_96"] - f["mkt_96"]
    f["hora"] = df.index.hour
    f["dia"] = df.index.dayofweek
    return f


def rotular(df, tp, sl, h):
    hi, lo, cl = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    n = len(df)
    out = {}
    if n <= h + 1:
        return None
    Hw, Lw = swv(hi[1:], h)[: n - h], swv(lo[1:], h)[: n - h]
    c0, fimc = cl[: n - h, None], cl[h:n]
    for lado in (1, -1):
        if lado == 1:
            btp, bsl = Hw >= c0 * (1 + tp), Lw <= c0 * (1 - sl)
        else:
            btp, bsl = Lw <= c0 * (1 - tp), Hw >= c0 * (1 + sl)
        itp = np.where(btp.any(1), btp.argmax(1), h + 1)
        isl = np.where(bsl.any(1), bsl.argmax(1), h + 1)
        g = itp < isl
        stop = (isl <= itp) & (isl <= h)
        rt = lado * (fimc / cl[: n - h] - 1)
        r = np.where(g, tp - 2 * MAKER, np.where(stop, -sl - MAKER - TAKER_SLIP, rt - MAKER - TAKER_SLIP)) / sl
        y = np.full(n, np.nan); y[: n - h] = g
        rr = np.full(n, np.nan); rr[: n - h] = r
        out[lado] = (y, rr)
    return out


def rotular3(df, tps, sl, h):
    """Saída do robô: 1/3 em cada alvo; stop inteiro até o 1º alvo, depois stop na entrada;
    o que sobrar fecha no fim do prazo. y = 1º alvo antes do stop; r = resultado em R (com taxas)."""
    hi, lo, cl = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    n = len(df)
    if n <= h + 1:
        return None
    out = {}
    for lado in (1, -1):
        y = np.full(n, np.nan); rr = np.full(n, np.nan)
        for i in range(n - h):
            e = cl[i]
            H_, L_ = hi[i + 1:i + 1 + h], lo[i + 1:i + 1 + h]
            fav = (H_ / e - 1) if lado == 1 else (1 - L_ / e)      # melhor movimento a favor no candle
            con = (1 - L_ / e) if lado == 1 else (H_ / e - 1)      # pior movimento contra no candle
            ist = np.argmax(con >= sl) if (con >= sl).any() else h
            ia = [np.argmax(fav >= t) if (fav >= t).any() else h for t in tps]
            if ist < ia[0] or (ia[0] == h and ist < h):            # stop cheio antes do 1º alvo
                y[i], rr[i] = 0, (-sl - MAKER - TAKER_SLIP) / sl
                continue
            if ia[0] == h:                                          # nada bateu: fecha no fim
                r = lado * (cl[i + h] / e - 1)
                y[i], rr[i] = 0, (r - MAKER - TAKER_SLIP) / sl
                continue
            pnl, resto = 0.0, 1.0
            be = np.argmax(con[ia[0] + 1:] >= 0) + ia[0] + 1 if (con[ia[0] + 1:] >= 0).any() else h
            for k, t in enumerate(tps):
                if ia[k] < h and (k == 0 or ia[k] < be):
                    pnl += t / 3 - MAKER / 3; resto -= 1 / 3
                else:
                    break
            if resto > 1e-9:
                if be < h:
                    pnl += resto * (0 - TAKER_SLIP)                 # saiu no zero
                else:
                    pnl += resto * (lado * (cl[i + h] / e - 1) - TAKER_SLIP)
            y[i], rr[i] = 1, (pnl - MAKER) / sl
        out[lado] = (y, rr)
    return out


def treinar(L, h_candles):
    cols = [c for c in L.columns if not c.startswith(("y_", "r_")) and c != "sym"]
    t = L.index
    corte = t.min() + (t.max() - t.min()) * 0.65
    tr, te = L[t < corte], L[t >= corte + pd.Timedelta(minutes=15 * h_candles + 60)]
    res = {"teste": [str(te.index.min()), str(te.index.max())], "linhas_treino": int(len(tr)),
           "linhas_teste": int(len(te)), "faixas": {}}
    for lado in (1, -1):
        y, r = f"y_{lado}", f"r_{lado}"
        a, b = tr.dropna(subset=[y]), te.dropna(subset=[y])
        if len(a) < 2000 or len(b) < 500:
            res["faixas"][lado] = []
            continue
        m = modelo._novo().fit(a[cols], a[y])
        fx = modelo.faixas(m.predict_proba(a[cols])[:, 1], m.predict_proba(b[cols])[:, 1],
                           b[y].to_numpy(), b[r].to_numpy())
        for x in fx:
            x["nota"] = nc.nota_de(x["acerto"], x["r"], x["n"])
        res["faixas"][lado] = fx
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    a = ap.parse_args()
    t0 = time.time()
    uni = [x for x in dados.universo() if x["classe"] == "acao"]
    uni.sort(key=lambda x: -x["vol24h"])
    if a.max:
        uni = uni[: a.max]
    mkt = yahoo_15m("QQQ")["close"]
    print(f"ações na BingX: {len(uni)}; mercado = QQQ ({len(mkt)} candles de pregão)", flush=True)

    hist, sem = {}, []

    def baixar(at):
        t, df = baixar_acao(at["nome"])
        if df is None:
            sem.append(at["nome"])
        else:
            hist[at["simbolo"]] = df

    dados.em_paralelo(baixar, uni, n=4)
    print(f"sem dados no Yahoo ({len(sem)}): {', '.join(sorted(sem))}", flush=True)
    print(f"históricos: {len(hist)} ações em {time.time() - t0:.0f}s", flush=True)
    if not hist:
        raise SystemExit("nenhuma ação com histórico no Yahoo")

    relatorio, saida = [], {"gerado_em": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                            "ativos": len(hist), "configs": {}}
    for nome, (tp, sl, h) in CONFIGS.items():
        linhas = []
        for sym, df in hist.items():
            rot = rotular3(df, tp, sl, h) if isinstance(tp, tuple) else rotular(df, tp, sl, h)
            if rot is None:
                continue
            f = features(df, mkt)
            for lado in (1, -1):
                f[f"y_{lado}"], f[f"r_{lado}"] = rot[lado]
            f = f[(f.index.minute == 0) & f.index.hour.isin(HORAS_PREGAO) & (f.index.dayofweek < 5)].iloc[20:]
            f["sym"] = sym
            linhas.append(f)
        L = pd.concat(linhas).sort_index()
        res = treinar(L, h)
        saida["configs"][nome] = {"tp": tp, "sl": sl, "candles": h, **res}
        alvo = " + ".join(f"{x:.1%}" for x in tp) + " (1/3 cada, stop → entrada)" if isinstance(tp, tuple) else f"{tp:.1%}"
        relatorio.append(f"\n=== Config {nome}: alvo {alvo} / stop {sl:.1%} / prazo {h} candles de 15 min (~{h / 4:.0f}h de mercado) — "
                         f"teste {res['teste'][0][:10]} a {res['teste'][1][:10]}, {res['linhas_teste']} decisões ===")
        for lado in (1, -1):
            relatorio.append(f"  {'LONG' if lado == 1 else 'SHORT'} (faixas da menor para a maior pontuação):")
            for x in res["faixas"][lado]:
                relatorio.append(f"    n {x['n']:6d}  acerto {x['acerto']:.3f}  R {x['r']:+.3f}  nota {x['nota']}")
            melhores = [x for x in res["faixas"][lado] if x["nota"] >= 8]
            relatorio.append(f"    -> faixas nota 8+: {len(melhores)} ({sum(x['n'] for x in melhores)} casos no teste)")
    cab = (f"Modelo de ações (preço + volume + QQQ) — {len(hist)} ações, 60 dias de pregão em 15 min, "
           f"decisões de hora em hora em dias úteis (pré, pregão e pós); faixas medidas fora da amostra. Gerado {saida['gerado_em']}.")
    with open(SAIDA, "w", encoding="utf-8") as f:
        f.write(cab + "\n" + "\n".join(relatorio) + "\n")
    with open(SAIDA_JSON, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)
    print(cab + "\n" + "\n".join(relatorio), flush=True)


if __name__ == "__main__":
    main()
