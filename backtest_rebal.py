"""
CARTEIRA REBALANCEADA DE 8 TOKENS NO SPOT.

US$ 8.000 divididos igualmente em 8 ativos. Periodicamente, vende o excesso
de quem subiu e compra quem ficou para tras, voltando a partes iguais.

Essenciais: CRWV, IREN, NFLX. Demais vagas: os mais volateis entre os tokens
liquidos no spot de cada narrativa (volatilidade dos ultimos 12 meses).

Veredito definido antes do resultado:
  1. Rebalancear SEMANAL agrega se bater "nunca" nas DUAS janelas
  2. O hedge de narrativa funciona se a carteira diversificada tiver queda
     maxima menor que a de semicondutores nas DUAS janelas
"""

import os

import numpy as np
import pandas as pd

from alerta_ema import avisar
from backtest_spot import universo_spot, historico, custo_spot, LIQ_MIN, ESTRESSE

ESSENCIAIS = ["CRWV", "IREN", "NFLX"]
NARRATIVAS = {
    "ia_computacao": ["CRWV", "IREN", "NBIS"],
    "semicondutores": ["NVDA", "AVGO", "AMD", "MU", "INTC", "MRVL", "CRDO", "SNDK",
                       "WDC", "STX", "AAOI", "COHR", "LITE", "GLW", "AXTI", "QCOM",
                       "ARM", "AMAT", "TSM", "ASML"],
    "consumo_digital": ["NFLX", "DIS", "UBER", "DKNG", "HIMS", "EBAY", "BABA", "GME"],
    "espaco": ["RKLB", "ASTS"],
    "nuclear": ["OKLO", "NNE", "CEG", "VST"],
}
COMPOSICOES = {
    "DIVERSIFICADA": {"semicondutores": 2, "consumo_digital": 1, "espaco": 1, "nuclear": 1},
    "FOCO SEMIS": {"semicondutores": 5},
}
CAPITAL = 8000.0
REGRAS = [("semanal", "semanal"), ("mensal", "mensal"), ("banda20", "desvio 20%"),
          ("nunca", "nunca")]
BANDA = 0.20
INICIO_LONGA_MAX = pd.Timestamp("2021-12-31")

pct = lambda x: f"{x*100:+.1f}%".replace(".", ",")
usd = lambda x: f"{x:+,.0f}".replace(",", ".")
dec = lambda x: f"{x:.2f}".replace(".", ",")


def narrativa_de(t):
    for n, lista in NARRATIVAS.items():
        if t in lista:
            return n
    return "outros"


def volatilidade(serie):
    r = serie.pct_change().dropna().iloc[-252:]
    return float(r.std() * np.sqrt(252)) if len(r) > 60 else 0.0


def montar(nome, vagas, candidatos, hist):
    escolhidos = list(ESSENCIAIS)
    for narr, qtd in vagas.items():
        opcoes = [t for t in NARRATIVAS[narr] if t in candidatos and t not in escolhidos]
        opcoes.sort(key=lambda t: volatilidade(hist[t]["Close"]), reverse=True)
        escolhidos += opcoes[:qtd]
    return escolhidos


def simular(precos, custos, regra):
    datas = precos.index
    n = precos.shape[1]
    ret = precos.pct_change().fillna(0).to_numpy()
    taxa = np.array([custos[t] for t in precos.columns]) / 2 / 100 * ESTRESSE  # um lado
    valores = np.full(n, CAPITAL / n)
    custo = float((valores * taxa).sum())
    valores = valores * (1 - taxa)
    semana, mes = datas.to_period("W"), datas.to_period("M")
    curva, giro = [], 0.0
    for i in range(len(datas)):
        if i > 0:
            valores = valores * (1 + ret[i])
            nova_semana = semana[i] != semana[i - 1]
            gatilho = (regra == "semanal" and nova_semana) or \
                      (regra == "mensal" and mes[i] != mes[i - 1]) or \
                      (regra == "banda20" and nova_semana and
                       (np.abs(valores / (valores.sum() / n) - 1) > BANDA).any())
            if gatilho:
                total = valores.sum()
                trocas = total / n - valores
                c = float((np.abs(trocas) * taxa).sum())
                custo += c
                giro += np.abs(trocas).sum() / 2
                valores = np.full(n, (total - c) / n)
        curva.append(valores.sum())
    return pd.Series(curva, index=datas), custo, giro


def metricas(curva):
    total = curva.iloc[-1] / CAPITAL - 1
    queda = float((curva / curva.cummax() - 1).min())
    sem = curva.resample("W").last().pct_change().dropna()
    return total, queda, float(sem.min()) if len(sem) else 0.0


def correlacao(precos):
    c = precos.pct_change().dropna().corr().to_numpy()
    return float(c[np.triu_indices_from(c, 1)].mean())


def main():
    univ = universo_spot()
    todos = sorted({t for lista in NARRATIVAS.values() for t in lista})
    hist = historico(todos + ["QQQ"])

    liquidos = {t for t, i in univ.items() if i["vol"] >= LIQ_MIN and t in hist}
    alertas = []
    for t in ESSENCIAIS:
        if t not in hist:
            alertas.append(f"{t}: sem historico - fora do teste")
        elif t not in univ:
            alertas.append(f"{t}: sem token no spot")
        elif univ[t]["vol"] < LIQ_MIN:
            alertas.append(f"{t}: pouco liquido no spot (US$ {univ[t]['vol']/1000:,.0f} mil/dia)"
                           .replace(",", "."))
    candidatos = liquidos | {t for t in ESSENCIAIS if t in hist}
    custos = {t: custo_spot(univ[t]["vol"]) if t in univ else custo_spot(0) for t in candidatos}

    carteiras = {n: [t for t in montar(n, v, candidatos, hist) if t in hist]
                 for n, v in COMPOSICOES.items()}

    M1 = ["CARTEIRA REBALANCEADA - 8 TOKENS SPOT", ""]
    for n, lista in carteiras.items():
        M1.append(f"{n}: " + ", ".join(lista))
    if alertas:
        M1 += ["", "⚠️ " + "\n⚠️ ".join(alertas)]

    resultados = {}
    M2 = []
    for janela in ("CURTA", "LONGA"):
        M2.append(f"===== JANELA {janela} =====")
        for n, lista in carteiras.items():
            inicios = {t: hist[t]["Close"].first_valid_index() for t in lista}
            if janela == "CURTA":
                ativos = lista
            else:
                ativos = [t for t in lista if inicios[t] <= INICIO_LONGA_MAX]
            if len(ativos) < 4:
                M2.append(f"{n}: menos de 4 ativos com historico - pulada")
                continue
            ini = max(inicios[t] for t in ativos)
            precos = pd.DataFrame({t: hist[t]["Close"] for t in ativos}).loc[ini:].dropna()
            anos = (precos.index[-1] - precos.index[0]).days / 365.25
            M2.append(f"{n} ({len(ativos)} ativos, {precos.index[0]:%m/%Y}-"
                      f"{precos.index[-1]:%m/%Y}, {dec(anos)} anos)")
            if janela == "LONGA" and len(ativos) < len(lista):
                M2.append("  sem: " + ", ".join(t for t in lista if t not in ativos))
            M2.append(f"  correlacao media: {dec(correlacao(precos))}")
            for regra, rot in REGRAS:
                curva, custo, giro = simular(precos, custos, regra)
                tot, queda, pior = metricas(curva)
                resultados[(janela, n, regra)] = {"total": tot, "queda": queda,
                                                  "final": curva.iloc[-1], "custo": custo}
                M2.append(f"  {rot}: {pct(tot)} | queda max {pct(queda)} | "
                          f"pior semana {pct(pior)} | custo US$ {custo:,.0f}".replace(",", "."))
            if "QQQ" in hist:
                q = hist["QQQ"]["Close"].loc[precos.index[0]:precos.index[-1]]
                qc = CAPITAL * q / q.iloc[0]
                qt, qq, _ = metricas(qc)
                M2.append(f"  QQQ no periodo: {pct(qt)} | queda max {pct(qq)}")
            M2.append("")

    # ------------------------------------------------ veredito
    M3 = ["VEREDITO"]
    for n in carteiras:
        ok = []
        for j in ("CURTA", "LONGA"):
            s, nn = resultados.get((j, n, "semanal")), resultados.get((j, n, "nunca"))
            if s and nn:
                ok.append(s["final"] > nn["final"])
                M3.append(f"  {n} {j.lower()}: semanal x nunca = "
                          f"US$ {usd(s['final'] - nn['final'])}")
        agrega = len(ok) == 2 and all(ok)
        M3.append(f"  -> rebalancear semanal agrega em {n}: {'SIM' if agrega else 'NAO'}")
    ok = []
    for j in ("CURTA", "LONGA"):
        d = resultados.get((j, "DIVERSIFICADA", "semanal"))
        s = resultados.get((j, "FOCO SEMIS", "semanal"))
        if d and s:
            ok.append(d["queda"] > s["queda"])
            M3.append(f"  queda max {j.lower()}: diversificada {pct(d['queda'])} x "
                      f"semis {pct(s['queda'])}")
    hedge = len(ok) == 2 and all(ok)
    M3 += [f"  -> hedge de narrativa funciona: {'SIM' if hedge else 'NAO'}", "",
           "LIMITACOES",
           "  - janela curta: so ~18 meses, todos em ciclo de alta de IA",
           "  - sobrevivencia: ativos escolhidos hoje, varios se recuperaram de quedas",
           "  - vagas preenchidas pelos mais volateis de HOJE",
           "  - rebalanceamento no fechamento; custo x1,5 sobre o giro"]

    for bloco in (M1, M2, M3):
        texto = "\n".join(bloco)
        print(texto + "\n")
        if os.environ.get("TELEGRAM_TOKEN"):
            avisar(texto[:3900])
    with open("relatorio_rebal.txt", "w", encoding="utf-8") as f:
        f.write("\n\n".join("\n".join(b) for b in (M1, M2, M3)))


if __name__ == "__main__":
    main()
