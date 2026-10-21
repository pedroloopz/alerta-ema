"""Checagens do vencedor do torneio: amostra intocada, custos dobrados, vela da entrada pessimista, por lado e por mês.

  python kronos/torneio_checagem.py <pasta com dados/> <resultado.json> <saida.json>
"""
import json
import sys

import numpy as np

import torneio as T

HOLD = 1788566400000            # 2026-09-05 00:00 UTC: últimos ~45 dias, nunca usados para escolher nada


def m(tr, cv, dias):
    return T.metricas(tr, cv, dias=dias)


def rodar(pasta, res_json, destino):
    ops, _ = T.carregar(pasta)
    res = json.load(open(res_json))
    f = T.TRADERS[res["vencedor"]][0]
    p, saida, gv = res["params"], res["saida_escolhida"], res["vagas_escolhidas"]
    kw = dict(saida=saida, vagas=gv["vagas"], risco_aberto=gv["risco_aberto"], por_grupo=gv["por_grupo"],
              risco=res["risco_escolhido"]["risco"])
    fim = ops[-1]["t"] + T.DIA
    out = {}

    # 1) Escolha refeita SEM os últimos 45 dias: o juiz só enxerga até 05/09; depois mede em 05/09–20/10.
    T.CORTE_ORIG = T.CORTE
    ops_cedo = [o for o in ops if o["t"] < HOLD]
    res_cedo, _ = T.julgar(ops_cedo)
    pros = {k: v for k, v in res_cedo.items() if v["categoria"] == "pro"}
    ok = {k: v for k, v in pros.items() if v["fora"]["r_medio"] > 0 and v["fora"]["n"] >= 20}
    base = ok or pros
    venc_cedo = max(base, key=lambda k: base[k]["treino"]["nota"] + base[k]["fora"]["nota"])
    out["escolha_sem_ultimos_45d"] = {"vencedor": venc_cedo, "params": res_cedo[venc_cedo]["params"],
                                      "placar": {k: v["fora"]["r_medio"] for k, v in res_cedo.items()}}
    # cada trader na amostra intocada, com os parâmetros escolhidos sem ela
    intocada = {}
    for k, v in res_cedo.items():
        fn = T.TRADERS[k][0]
        x = {"martingale": k.endswith("martingale"), "risco_fixo": 0.05 if k.endswith("alta") else None}
        tr, cv = T.carteira(ops, fn, v["params"], ini=HOLD, **x)
        intocada[k] = m(tr, cv, (fim - HOLD) / T.DIA)
    out["intocada_por_trader"] = intocada
    tr, cv = T.carteira(ops, f, p, ini=HOLD, **kw)
    out["vencedor_setup_final_intocada"] = m(tr, cv, (fim - HOLD) / T.DIA)

    # 2) custos: entrada limite cobrada como mercado (0,12%) e tudo com custo dobrado
    for nome, cm, cu in (("limite_como_mercado", T.CUSTO, T.CUSTO), ("custo_dobrado", 2 * T.CUSTO_MAKER, 2 * T.CUSTO)):
        a, b = T.CUSTO_MAKER, T.CUSTO
        T.CUSTO_MAKER, T.CUSTO = cm, cu
        tr, cv = T.carteira(ops, f, p, ini=T.CORTE, **kw)
        out[nome] = m(tr, cv, (fim - T.CORTE) / T.DIA)
        T.CUSTO_MAKER, T.CUSTO = a, b

    # 3) vela da entrada pessimista: se a mínima (long) passou do stop na vela em que a limite foi executada, stop
    orig = T.simular

    def pess(o, s, x):
        r = orig(o, s, x)
        if r is None or x.get("entrada") != "recuo":
            return r
        re = o["reais"]
        sl = T.stop_de(o, s, x)
        d = max(o["recuo"]["LONG" if s == 1 else "SHORT"]["q50"] * x.get("recuo_k", 0.5), 0.001)
        alvo = (1 + re[0][0]) * (1 - s * d)
        for i in range(min(3, len(re))):
            lo, hi = re[i][2], re[i][1]
            if (s == 1 and 1 + lo <= alvo) or (s == -1 and 1 + hi >= alvo):
                pior = s * ((1 + (lo if s == 1 else hi)) / alvo - 1)
                if pior <= -sl:
                    return (-sl - T.CUSTO_MAKER) / sl, i + 1
                break
        return r
    T.simular = pess
    tr, cv = T.carteira(ops, f, p, ini=T.CORTE, **kw)
    out["vela_entrada_pessimista"] = m(tr, cv, (fim - T.CORTE) / T.DIA)
    tr_all, _ = T.carteira(ops, f, p, **kw)
    T.simular = orig

    # 4) com tudo pessimista: por mês e por lado (ano todo, setup final)
    meses, lados = {}, {}
    for t in tr_all:
        k = np.datetime64(int(t["t"]), "ms").astype("datetime64[M]").astype(str)
        meses.setdefault(k, []).append(t["r"])
        lados.setdefault(("long" if t["lado"] == 1 else "short") + (" fora" if t["t"] >= T.CORTE else " treino"), []).append(t["r"])
    out["por_mes_pessimista"] = {k: {"n": len(v), "r_medio": float(np.mean(v)), "r_total": float(np.sum(v))}
                                 for k, v in sorted(meses.items())}
    out["por_lado_pessimista"] = {k: {"n": len(v), "r_medio": float(np.mean(v))} for k, v in sorted(lados.items())}

    # 5) sensibilidade: vizinhos dos parâmetros escolhidos (fora da amostra) — um pico isolado é sorte
    viz = []
    for pp in (0.6, 0.65, 0.7, 0.75, 0.8):
        for rk in (0.6, 0.8, 1.0, 1.2):
            for tk in (4, 5, 6):
                s2 = dict(saida, trail_k=tk)
                tr, cv = T.carteira(ops, f, {"p": pp, "recuo_k": rk}, **(kw | {"saida": s2}), ini=T.CORTE)
                mm = m(tr, cv, (fim - T.CORTE) / T.DIA)
                tr2, cv2 = T.carteira(ops, f, {"p": pp, "recuo_k": rk}, **(kw | {"saida": s2}), fim=T.CORTE)
                m2 = T.metricas(tr2, cv2)
                viz.append({"p": pp, "recuo_k": rk, "trail_k": tk, "treino_r": m2["r_medio"], "treino_n": m2["n"],
                            "fora_r": mm["r_medio"], "fora_n": mm["n"]})
    out["vizinhos"] = viz
    json.dump(out, open(destino, "w"), ensure_ascii=False, indent=1, default=float)
    for k in ("escolha_sem_ultimos_45d", "vencedor_setup_final_intocada", "limite_como_mercado", "custo_dobrado",
              "vela_entrada_pessimista", "por_lado_pessimista", "por_mes_pessimista"):
        print(k, json.dumps(out[k], ensure_ascii=False, default=float)[:900])
    print("intocada:", {k: (v["n"], round(v["r_medio"], 3)) for k, v in intocada.items()})
    pos = sum(1 for v in viz if v["fora_r"] > 0 and v["treino_r"] > 0)
    print(f"vizinhos positivos no treino e fora: {pos} de {len(viz)}")


if __name__ == "__main__":
    rodar(*sys.argv[1:4])
