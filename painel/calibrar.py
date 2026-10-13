"""Calibração diária do painel: baixa ~60 dias de candles de 15 min de todo o universo,
testa cada setup com 6 pares de SL/TP em ATR e grava painel/stats.json.

Validação honesta (fora da amostra): as estatísticas dos primeiros 75% do período dão
notas aos sinais dos últimos 25%; o painel mostra quanto cada faixa de nota acertou ali.

Uso (precisa de internet — roda no GitHub Actions):
    python painel/calibrar.py            # universo inteiro
    python painel/calibrar.py --max 20   # ensaio rápido
"""
import argparse
import datetime
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import dados  # noqa: E402
import modelo  # noqa: E402
import nucleo as nc  # noqa: E402

DIAS = 90
SAIDA = os.path.join(os.path.dirname(__file__), "stats.json")


PERFIS = []
ML_LINHAS, KL, BTC = [], {}, {}
AQUI = os.path.dirname(__file__)
LOG_ML = os.path.join(AQUI, "..", "coleta", "ml_log.csv")


def linhas_modelo(ativo, df):
    """Decisões de hora em hora do modelo de fluxo (só cripto com histórico longo)."""
    try:
        g = modelo.gate_1h(ativo["nome"])
        if g is None or len(g) < 500:
            return
        d = modelo.juntar(df, g)
        f = modelo.features(d, BTC["serie"])
        rot = modelo.rotular(d)
        for lado in (1, -1):
            f[f"y_{lado}"], f[f"r_{lado}"] = rot[lado]
        f = f[f.index.minute == 0].iloc[96:]
        f["sym"] = ativo["simbolo"]
        ML_LINHAS.append(f)
    except Exception as e:
        print(f"modelo: falhou {ativo['simbolo']}: {e}", flush=True)


def placar_vivo():
    """Resultado REAL dos sinais do modelo registrados pelo scanner (já passadas 12h)."""
    if not os.path.exists(LOG_ML):
        return None
    log = pd.read_csv(LOG_ML, parse_dates=["tempo"]).drop_duplicates(["tempo", "simbolo", "lado"])
    res = []
    for row in log.itertuples():
        k = KL.get(row.simbolo)
        if k is None:
            continue
        fut = k[(k.index > row.tempo) & (k.index <= row.tempo + pd.Timedelta(hours=modelo.H // 4))]
        if len(fut) < modelo.H:
            continue
        g, r = 0, None
        for hi, lo in zip(fut["high"], fut["low"]):
            if (row.lado == 1 and lo <= row.sl) or (row.lado == -1 and hi >= row.sl):
                r = -1.0; break
            if (row.lado == 1 and hi >= row.tp) or (row.lado == -1 and lo <= row.tp):
                g, r = 1, modelo.TP / modelo.SL; break
        if r is None:
            r = row.lado * (fut["close"].iloc[-1] / row.entrada - 1) / modelo.SL
        res.append({"tempo": str(row.tempo), "ativo": row.ativo, "lado": int(row.lado), "ganhou": g, "r": round(float(r), 3)})
    if not res:
        return {"n": 0}
    x = pd.DataFrame(res)
    return {"n": len(x), "acerto": round(float(x["ganhou"].mean()), 3), "r_medio": round(float(x["r"].mean()), 3),
            "ultimos": res[-15:]}


def sessao_acao():
    """Quanto cada hora da semana (UTC) negocia nas ações perp, em % da hora mais cheia.
    Mostra no painel se o mercado das ações está aberto, em pré/pós ou parado."""
    if not PERFIS:
        return {}
    m = pd.concat(PERFIS, axis=1).median(axis=1)
    m = m / m.max()
    return {f"{d}|{h}": round(float(v), 3) for (d, h), v in m.items()}


def processar(ativo):
    try:
        df = dados.historico(ativo, dias=DIAS)
        if df is None or len(df) < 600:
            return None
        if ativo["classe"] == "cripto" and dados.FONTE.get(ativo["simbolo"]) == "binance":
            KL[ativo["simbolo"]] = df
            linhas_modelo(ativo, df)
        if ativo["classe"] == "acao":
            v = df["volume"] * df["close"]
            perfil = v.groupby([df.index.dayofweek, df.index.hour]).mean()
            if perfil.max() > 0:
                PERFIS.append(perfil / perfil.max())
        df = nc.preparar(df)
        sig = nc.sinais(df)
        if sig.empty:
            return None
        partes = []
        for sl, tp in nc.GRADE_SAIDA:
            r = nc.simular(df, sig, sl, tp)
            r["sl"], r["tp"] = sl, tp
            partes.append(r)
        out = pd.concat(partes, ignore_index=True)
        out["ativo"], out["classe"] = ativo["simbolo"], ativo["classe"]
        out["hora"] = out["tempo"].dt.hour
        return out.drop(columns="i")
    except Exception as e:
        print(f"falhou {ativo['simbolo']}: {e}", flush=True)
        return None


def escolher_saidas(t):
    """Para cada classe+setup+lado, o par SL/TP com melhor R médio (mínimo 100 trades)."""
    g = t.groupby(["classe", "setup", "lado", "sl", "tp"]).agg(n=("r", "size"), r=("r", "mean"), p=("ganhou", "mean")).reset_index()
    g = g[g["n"] >= 100]
    out = {}
    for (classe, setup, lado), grp in g.groupby(["classe", "setup", "lado"]):
        m = grp.sort_values("r", ascending=False).iloc[0]
        out[f"{classe}|{setup}|{lado}"] = {"sl": float(m["sl"]), "tp": float(m["tp"]),
                                    "p": round(float(m["p"]), 3), "r": round(float(m["r"]), 3), "n": int(m["n"])}
    return out


def filtrar_saidas(t, saidas):
    k = t["classe"] + "|" + t["setup"] + "|" + t["lado"].astype(str)
    sl = k.map(lambda x: saidas.get(x, {}).get("sl"))
    tp = k.map(lambda x: saidas.get(x, {}).get("tp"))
    return t[(t["sl"] == sl) & (t["tp"] == tp)]


def montar_stats(t):
    def agrega(grp, chave):
        d = {}
        for k, x in grp:
            k = k if isinstance(k, tuple) else (k,)
            d["|".join(str(v) for v in k)] = {"n": int(len(x)), "g": int(x["ganhou"].sum()), "r": round(float(x["r"].sum()), 3)}
        return d
    classe = {c: {"p": float(x["ganhou"].mean()), "r": float(x["r"].mean()), "n": int(len(x))}
              for c, x in t.groupby("classe")}
    return {
        "base": agrega(t.groupby(["classe", "setup", "lado", "alinhado", "regime"]), None),
        "ativo": agrega(t.groupby(["ativo", "setup", "lado"]), None),
        "hora": agrega(t.groupby(["classe", "hora"]), None),
        "classe": classe,
    }


def validar(treino, teste):
    st = montar_stats(treino)
    linhas = []
    for row in teste.itertuples():
        e = nc.estimar(st, row.classe, row.ativo, row.setup, row.lado, row.alinhado, row.hora, row.regime)
        if e:
            linhas.append((nc.nota_de(e["p"], e["ev_r"], e["n_base"]), row.ganhou, row.r, row.classe))
    v = pd.DataFrame(linhas, columns=["nota", "ganhou", "r", "classe"])
    out = {}
    for nota in range(1, 11):
        x = v[v["nota"] == nota]
        if len(x):
            out[str(nota)] = {"n": int(len(x)), "acerto": round(float(x["ganhou"].mean()), 3),
                              "r_medio": round(float(x["r"].mean()), 3)}
    for rotulo, x in (("8+", v[v["nota"] >= 8]), ("3-", v[v["nota"] <= 3]), ("todos", v)):
        out[rotulo] = {"n": int(len(x)), "acerto": round(float(x["ganhou"].mean()), 3) if len(x) else None,
                       "r_medio": round(float(x["r"].mean()), 3) if len(x) else None}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    a = ap.parse_args()
    t0 = time.time()
    uni = dados.universo()
    uni.sort(key=lambda x: -x["vol24h"])
    if a.max:
        uni = uni[: a.max]
    print(f"universo: {len(uni)} ativos ({sum(x['classe'] == 'cripto' for x in uni)} cripto, "
          f"{sum(x['classe'] == 'acao' for x in uni)} ações)", flush=True)
    BTC["serie"] = dados.klines_binance("BTCUSDT", DIAS)["close"]
    res = [r for r in dados.em_paralelo(processar, uni, n=6) if r is not None]
    t = pd.concat(res, ignore_index=True).dropna(subset=["r"])
    print(f"sinais simulados: {len(t)} em {time.time() - t0:.0f}s", flush=True)
    for c, x in t.groupby("classe"):
        print(f"  {c}: {x['ativo'].nunique()} ativos, de {x['tempo'].min()} a {x['tempo'].max()}", flush=True)
    print("fontes:", dict(pd.Series(list(dados.FONTE.values())).value_counts()), flush=True)

    corte = t["tempo"].min() + (t["tempo"].max() - t["tempo"].min()) * 0.75
    treino_all, teste_all = t[t["tempo"] < corte], t[t["tempo"] >= corte]
    saidas_treino = escolher_saidas(treino_all)
    validacao = validar(filtrar_saidas(treino_all, saidas_treino), filtrar_saidas(teste_all, saidas_treino))

    saidas = escolher_saidas(t)
    final = filtrar_saidas(t, saidas)
    st = montar_stats(final)
    st.update({
        "gerado_em": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "dias": DIAS,
        "periodo": [str(t["tempo"].min()), str(t["tempo"].max())],
        "corte_validacao": str(corte),
        "saidas": saidas,
        "validacao": validacao,
        "universo": [{k: x[k] for k in ("simbolo", "nome", "classe", "rank")} for x in uni],
        "sessao_acao": sessao_acao(),
    })
    if ML_LINHAS:
        L = pd.concat(ML_LINHAS)
        pacote = modelo.treinar(L)
        modelo.salvar(pacote, os.path.join(AQUI, "modelo.pkl"))
        st["ml"] = {"tp": modelo.TP, "sl": modelo.SL, "horas": modelo.H // 4, "faixas": pacote["faixas"],
                    "teste": pacote["teste"], "linhas": int(len(L)), "ativos": int(L["sym"].nunique())}
        print(f"modelo de fluxo: {len(L)} linhas, {L['sym'].nunique()} ativos", flush=True)
        for lado in (1, -1):
            print("  faixas", lado, pacote["faixas"][lado][-3:], flush=True)
    st["placar_ml"] = placar_vivo()
    with open(SAIDA, "w") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    print("saídas escolhidas:", json.dumps(saidas, indent=1))
    print("validação fora da amostra:", json.dumps(validacao, indent=1))
    print(f"ok em {time.time() - t0:.0f}s; stats.json {os.path.getsize(SAIDA) // 1024} KB")


if __name__ == "__main__":
    main()
