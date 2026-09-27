"""
DONCHIAN - OTIMIZACAO COM VALIDACAO (so EUA).

Grade testada (192 x 2 lados = 384 carteiras):
  entrada: rompe maxima de 20 ou 55 dias | saida: minima de 10 ou 20 dias
  filtro de mercado: nenhum | SPY acima da MMA 200 (short: SPY abaixo)
  tamanho: igual (1/K) | por volatilidade (risco 1% com 2 ATR)
  posicoes maximas K: 5, 7, 10
  alavancagem: 1x (spot) | 1,5x | 2x | 3x (perps)
  lados: so comprado | comprado + vendido (so perps)

Como escolhe sem se enganar: a melhor configuracao e escolhida SO no treino
(2015-2021) pelo retorno/queda (CAGR / pior queda). Depois olha a validacao
(2022-2026), que ela nunca viu.

Perps (estimativas - ajuste para os valores reais do app):
  taxa+spread 0,10% por lado | funding 10% ao ano sobre o nocional (comprado)
  liquidacao quando a perda chega a 90% da margem (sem stop de protecao)
  NAO modela gap de fim de semana: o perp anda sabado e domingo, a acao nao.
Spot: 0,20% por lado. Todos os custos x1,5.
"""

import json

import numpy as np
import pandas as pd
import yfinance as yf

from simbolos import SIMBOLOS

ESTRESSE = 1.5
CUSTO_SPOT = 0.20 / 100
CUSTO_PERP = 0.10 / 100
FUNDING_ANO = 0.10
LIQ = 0.90
RISCO_VOL = 0.01
INICIO = "2015-01-01"
CORTE = pd.Timestamp("2022-01-01")
QUEDA_MAX_ACEITA = -50.0

CARTEIRA = ["ABNB", "BE", "MVLL", "UNH", "NFLX", "UBER", "ENPH", "CRWV"]
SEM_SUBJACENTE = {"SPCX", "OPENAI", "ANTHROPIC", "UNITREE", "MINIMAX", "ZHIPU",
                  "CXMT", "SAMSUNG", "SKHYNIX", "SKHY", "HYUNDAI", "MEITUAN",
                  "KUAISHOU", "XIAOMI", "POPMART", "BNC", "GPRO"}
MAPA = {"STXX": "STX", "BBX": "BB", "SNXX": "SPY"}

pct = lambda x: f"{x:+.1f}%".replace(".", ",")
usd = lambda x: f"US$ {x:,.0f}".replace(",", ".")
alav = lambda x: f"{x:g}x".replace(".", ",")


def universo():
    t = []
    for s in SIMBOLOS:
        x = s.replace("USDT", "").replace("USD1", "")
        if x not in SEM_SUBJACENTE:
            t.append(MAPA.get(x, x))
    return sorted(set(t + CARTEIRA) - {"SPY", "QQQ"})


def carregar():
    tick = universo()
    bruto = yf.download(tick + ["SPY", "QQQ"], start=INICIO, auto_adjust=True,
                        progress=False, group_by="ticker", threads=True)
    dias = bruto.index
    campos = {}
    ok = []
    for t in tick:
        try:
            df = bruto[t][["Open", "High", "Low", "Close"]]
        except KeyError:
            continue
        if df["Close"].notna().sum() > 300:
            ok.append(t)
    for k in ("Open", "High", "Low", "Close"):
        campos[k] = np.column_stack([bruto[t][k].to_numpy() for t in ok])
    spy = bruto["SPY"]["Close"].to_numpy()
    qqq = bruto["QQQ"]["Close"].to_numpy()
    return ok, dias, campos, spy, qqq


def rolling(a, n, f):
    return getattr(pd.DataFrame(a).rolling(n, min_periods=n), f)().to_numpy()


def gerar_trades(campos, n_ent, n_sai, lado):
    """Trades por ativo, sem sobreposicao. lado +1 comprado, -1 vendido."""
    O, H, L, C = (campos[k] for k in ("Open", "High", "Low", "Close"))
    if lado == 1:
        nivel = rolling(H, n_ent, "max")
        saida = rolling(L, n_sai, "min")
    else:
        nivel = rolling(L, n_ent, "min")
        saida = rolling(H, n_sai, "max")
    tr_ = np.fmax(H - L, np.fmax(np.abs(H - np.roll(C, 1, 0)), np.abs(L - np.roll(C, 1, 0))))
    atr = rolling(tr_, 20, "mean")
    mom = C / np.roll(C, 126, 0) - 1
    out = []
    nd, na = C.shape
    for a in range(na):
        i = max(n_ent, 130)
        while i < nd - 1:
            s, e = i, i + 1
            nv = nivel[s, a]
            if np.isnan(nv) or np.isnan(O[e, a]):
                i += 1
                continue
            if lado == 1:
                ent = O[e, a] if O[e, a] > nv else (nv if H[e, a] >= nv else None)
            else:
                ent = O[e, a] if O[e, a] < nv else (nv if L[e, a] <= nv else None)
            if ent is None:
                i += 1
                continue
            js = None
            for j in range(e + 1, nd):
                if np.isnan(C[j - 1, a]) or np.isnan(O[j, a]):
                    continue
                ref = saida[j - 2, a]
                if (lado == 1 and C[j - 1, a] < ref) or (lado == -1 and C[j - 1, a] > ref):
                    js = j
                    break
            if js is None:
                break
            out.append((a, lado, e, js, ent, O[js, a], lado * mom[s, a], atr[s, a] / C[s, a]))
            i = js
    return out


def simular(trades, campos, spy_ok, K, lev, tamanho, filtro):
    O, H, L, C = (campos[k] for k in ("Open", "High", "Low", "Close"))
    nd = C.shape[0]
    perp = lev > 1 or any(t[1] == -1 for t in trades)
    custo = (CUSTO_PERP if perp else CUSTO_SPOT) * ESTRESSE
    fund_dia = FUNDING_ANO * ESTRESSE / 252 if perp else 0.0
    por_dia = {}
    for t in trades:
        por_dia.setdefault(t[2], []).append(t)
    caixa, pos, curva, liqs = 1.0, [], np.empty(nd), 0
    ultimo = np.nan_to_num(C[0])
    for d in range(nd):
        cd = np.where(np.isnan(C[d]), ultimo, C[d])
        eq_ant = curva[d - 1] if d > 0 else caixa     # patrimonio no fechamento de ontem
        # entradas na abertura
        cands = sorted(por_dia.get(d, []), key=lambda t: -t[6])
        for a, lado, e, js, ent, sai, sc, atrp in cands:
            if len(pos) >= K or eq_ant <= 0:
                break
            if filtro and ((lado == 1 and not spy_ok[e - 1]) or (lado == -1 and spy_ok[e - 1])):
                continue
            if any(p["a"] == a for p in pos):
                continue
            noc = eq_ant * lev / K
            if tamanho == "vol" and atrp > 0 and not np.isnan(atrp):
                noc = min(eq_ant * RISCO_VOL / (2 * atrp), noc * 1.5)
            margem = noc / lev
            if margem > caixa:
                margem = caixa
                noc = margem * lev
            if margem <= 0:
                continue
            caixa -= margem + noc * custo
            liq_px = ent * (1 - lado * LIQ / lev) if (lev > 1 or lado == -1) else None
            pos.append(dict(a=a, lado=lado, noc=noc, margem=margem, ent=ent, js=js, sai=sai, liq=liq_px))
        # durante o dia: liquidacao, saidas, funding
        rest = []
        for p in pos:
            a = p["a"]
            if p["liq"] is not None and not np.isnan(L[d, a]) and (
                    (p["lado"] == 1 and L[d, a] <= p["liq"]) or (p["lado"] == -1 and H[d, a] >= p["liq"])):
                liqs += 1
                continue                      # margem perdida
            if p["js"] == d:
                pnl = p["lado"] * p["noc"] * (p["sai"] / p["ent"] - 1)
                caixa += p["margem"] + pnl - p["noc"] * custo
                continue
            if p["lado"] == 1:
                caixa -= p["noc"] * fund_dia
            rest.append(p)
        pos = rest
        ultimo = cd
        curva[d] = caixa + sum(p["margem"] + p["lado"] * p["noc"] * (cd[p["a"]] / p["ent"] - 1) for p in pos)
        if curva[d] <= 0.02:
            curva[d:] = max(curva[d], 0.0)
            return curva, liqs, True
    return curva, liqs, False


def metricas(curva, dias, mascara):
    s = pd.Series(curva, index=dias)[mascara]
    s = s[s.index >= s.index[0]]
    anos = (s.index[-1] - s.index[0]).days / 365.25
    if s.iloc[0] <= 0 or anos <= 0:
        return -100.0, -100.0
    fim = max(s.iloc[-1], 1e-9)
    cagr = ((fim / s.iloc[0]) ** (1 / anos) - 1) * 100
    queda = ((s / s.cummax()) - 1).min() * 100
    return cagr, queda


def main():
    capital = json.load(open("carteira.json"))["capital_inicial"]
    tick, dias, campos, spy, qqq = carregar()
    inicio_util = 200
    dias_idx = pd.DatetimeIndex(dias)
    spy_ok = spy > pd.Series(spy).rolling(200).mean().to_numpy()
    m_tr = (dias_idx < CORTE) & (np.arange(len(dias)) >= inicio_util)
    m_va = dias_idx >= CORTE
    m_all = np.arange(len(dias)) >= inicio_util

    cache = {}
    for ne in (20, 55):
        for ns in (10, 20):
            for lado in (1, -1):
                cache[(ne, ns, lado)] = gerar_trades(campos, ne, ns, lado)

    linhas = []
    for ne in (20, 55):
        for ns in (10, 20):
            for filtro in (False, True):
                for tam in ("igual", "vol"):
                    for K in (5, 7, 10):
                        for lev in (1, 1.5, 2, 3):
                            for lados in ("comprado", "ambos"):
                                tr = cache[(ne, ns, 1)] + (cache[(ne, ns, -1)] if lados == "ambos" else [])
                                curva, liqs, quebrou = simular(tr, campos, spy_ok, K, lev, tam, filtro)
                                c_tr, q_tr = metricas(curva, dias_idx, m_tr)
                                c_va, q_va = metricas(curva, dias_idx, m_va)
                                c_all, q_all = metricas(curva, dias_idx, m_all)
                                linhas.append(dict(
                                    entrada=ne, saida=ns, filtro=filtro, tamanho=tam, K=K, lev=lev,
                                    lados=lados, cagr_tr=c_tr, queda_tr=q_tr, cagr_va=c_va, queda_va=q_va,
                                    cagr=c_all, queda=q_all, liquidacoes=liqs, quebrou=quebrou,
                                    final=capital * curva[-1] / curva[inicio_util] if curva[inicio_util] > 0 else 0))
    df = pd.DataFrame(linhas)
    df["mar_tr"] = df["cagr_tr"] / df["queda_tr"].abs().clip(lower=1)
    df["mar_va"] = df["cagr_va"] / df["queda_va"].abs().clip(lower=1)
    df.to_csv("donchian_grade.csv", index=False)

    elegiveis = df[(~df["quebrou"]) & (df["queda_tr"] > QUEDA_MAX_ACEITA)]
    esc = elegiveis.sort_values("mar_tr", ascending=False)

    def desc(r):
        return (f"entra {r.entrada}d, sai {r.saida}d | filtro SPY {'sim' if r.filtro else 'nao'} | "
                f"tamanho {r.tamanho} | {r.K} posicoes | {alav(r.lev)} | {r.lados}")

    def bloco(r):
        return [f"  treino: CAGR {pct(r.cagr_tr)} | pior queda {pct(r.queda_tr)}",
                f"  VALIDACAO: CAGR {pct(r.cagr_va)} | pior queda {pct(r.queda_va)}",
                f"  periodo todo: CAGR {pct(r.cagr)} | pior queda {pct(r.queda)} | liquidacoes {r.liquidacoes} | {usd(capital)} -> {usd(r.final)}"]

    q = pd.Series(qqq, index=dias_idx)
    def ref(mask):
        s = q[mask]; anos = (s.index[-1] - s.index[0]).days / 365.25
        return ((s.iloc[-1] / s.iloc[0]) ** (1 / anos) - 1) * 100, ((s / s.cummax()) - 1).min() * 100

    out = ["DONCHIAN - OTIMIZACAO (escolha so no treino, conferida na validacao)",
           f"{len(tick)} acoes | {dias_idx[inicio_util]:%d/%m/%Y} a {dias_idx[-1]:%d/%m/%Y} | validacao a partir de {CORTE:%d/%m/%Y}",
           f"custos x{ESTRESSE}: spot 0,20%/lado | perp 0,10%/lado + funding 10% a.a. | liquidacao a 90% da margem",
           f"{len(df)} carteiras testadas | quebraram (patrimonio ~zero): {int(df['quebrou'].sum())}",
           ""]
    if len(esc):
        best = esc.iloc[0]
        out += ["ESCOLHIDA NO TREINO (melhor CAGR/queda):", desc(best)] + bloco(best) + [""]
        out.append("A mesma configuracao, mudando so a alavancagem:")
        for lev in (1, 1.5, 2, 3):
            r = df[(df.entrada == best.entrada) & (df.saida == best.saida) & (df.filtro == best.filtro) &
                   (df.tamanho == best.tamanho) & (df.K == best.K) & (df.lados == best.lados) & (df.lev == lev)].iloc[0]
            out.append(f"  {alav(lev)}: valid CAGR {pct(r.cagr_va)} / queda {pct(r.queda_va)} | todo CAGR {pct(r.cagr)} / queda {pct(r.queda)} | liquid. {r.liquidacoes}{' | QUEBROU' if r.quebrou else ''}")
        out.append("")
        r2 = df[(df.entrada == best.entrada) & (df.saida == best.saida) & (df.filtro == best.filtro) &
                (df.tamanho == best.tamanho) & (df.K == best.K) & (df.lev == best.lev) & (df.lados != best.lados)]
        if len(r2):
            r2 = r2.iloc[0]
            out.append(f"Trocando para '{r2.lados}': valid CAGR {pct(r2.cagr_va)} / queda {pct(r2.queda_va)}")
            out.append("")
        out.append("Top 5 do treino e como foram na validacao:")
        for _, r in esc.head(5).iterrows():
            out.append(f"- {desc(r)}")
            out.append(f"  treino {pct(r.cagr_tr)}/{pct(r.queda_tr)} -> valid {pct(r.cagr_va)}/{pct(r.queda_va)}")
        out.append("")
    so_cagr = df[~df["quebrou"]].sort_values("cagr_tr", ascending=False).iloc[0]
    out += ["Se escolhesse so pelo maior CAGR no treino (sem ligar para a queda):", desc(so_cagr)] + bloco(so_cagr) + [""]
    a, b = ref(m_tr); c, d = ref(m_va); e, f = ref(m_all)
    out.append(f"QQQ comprar e segurar: treino {pct(a)}/{pct(b)} | valid {pct(c)}/{pct(d)} | todo {pct(e)}/{pct(f)}")
    out.append("Obs.: universo = acoes de hoje (vies de sobrevivencia). Fim de semana do perp nao modelado.")
    texto = "\n".join(out)
    print(texto)
    open("relatorio_donchian.txt", "w").write(texto)


if __name__ == "__main__":
    main()
