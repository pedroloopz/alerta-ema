"""Pesquisa: modelo de chance para AÇÕES em perp da BingX (só preço, volume e mercado).

O modelo de fluxo das criptos usa dados da Gate (liquidações, OI, taker) que não existem para
ações. Aqui o mesmo método (gradient boosting, decisões de hora em hora, faixas medidas FORA da
amostra) é treinado só com o que existe para ações: preço e volume de 15 min do próprio perp e
o QQQ como mercado. Testa 3 pares de alvo/stop e grava relatorio_ml_acoes.txt.

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
CONFIGS = {"A": (0.02, 0.03, 48), "B": (0.015, 0.02, 32), "C": (0.03, 0.03, 96)}   # alvo, stop, candles de 15 min
MAKER, TAKER_SLIP = modelo.MAKER, modelo.TAKER_SLIP
HORAS_PREGAO = range(14, 20)        # decisões só com a bolsa americana aberta (UTC)


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
    qqq = next((x for x in uni if x["nome"] == "QQQ"), None) or next(
        (x for x in dados.universo() if x["nome"] == "QQQ"), None)
    mkt = dados.klines(qqq["simbolo"], dias=DIAS)["close"]
    print(f"ações: {len(uni)}; mercado = QQQ ({len(mkt)} candles)", flush=True)

    hist = {}

    def baixar(at):
        try:
            df = dados.klines(at["simbolo"], dias=DIAS)
            if df is not None and len(df) > 1500:
                hist[at["simbolo"]] = df
        except Exception as e:
            print(f"falhou {at['simbolo']}: {e}", flush=True)

    dados.em_paralelo(baixar, uni, n=6)
    print(f"históricos: {len(hist)} ações em {time.time() - t0:.0f}s", flush=True)

    relatorio, saida = [], {"gerado_em": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                            "ativos": len(hist), "configs": {}}
    for nome, (tp, sl, h) in CONFIGS.items():
        linhas = []
        for sym, df in hist.items():
            rot = rotular(df, tp, sl, h)
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
        saida["configs"][nome] = {"tp": tp, "sl": sl, "horas": h / 4, **res}
        relatorio.append(f"\n=== Config {nome}: alvo {tp:.1%} / stop {sl:.1%} / prazo {h / 4:.0f}h — "
                         f"teste {res['teste'][0][:10]} a {res['teste'][1][:10]}, {res['linhas_teste']} decisões ===")
        for lado in (1, -1):
            relatorio.append(f"  {'LONG' if lado == 1 else 'SHORT'} (faixas da menor para a maior pontuação):")
            for x in res["faixas"][lado]:
                relatorio.append(f"    n {x['n']:6d}  acerto {x['acerto']:.3f}  R {x['r']:+.3f}  nota {x['nota']}")
            melhores = [x for x in res["faixas"][lado] if x["nota"] >= 8]
            relatorio.append(f"    -> faixas nota 8+: {len(melhores)} ({sum(x['n'] for x in melhores)} casos no teste)")
    cab = (f"Modelo de ações (preço + volume + QQQ) — {len(hist)} ações, {DIAS} dias de 15 min, "
           f"decisões de hora em hora no pregão; faixas medidas fora da amostra. Gerado {saida['gerado_em']}.")
    with open(SAIDA, "w", encoding="utf-8") as f:
        f.write(cab + "\n" + "\n".join(relatorio) + "\n")
    with open(SAIDA_JSON, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)
    print(cab + "\n" + "\n".join(relatorio), flush=True)


if __name__ == "__main__":
    main()
