"""
BACKTEST FASE 3 - linha de base x 3 metodos de swing em spot, EUA e B3 separados.

Dados: cache da branch `dados` (historico.py) em DADOS (padrao: dados/).
Sinal no fechamento do dia; execucao no dia seguinte (rompimento: no nivel, ou na
abertura se abrir acima; demais: na abertura). Sem alavancagem. Posicao = patrimonio / VAGAS.

Metodos (parametros declarados ANTES de rodar; 3 variantes cada, nada de otimizar):
  BASE-EUA  Donchian 55/10 (o bot hoje): rompe a maxima de 55 pregoes com o indice
            acima da media de 200; sai no fechamento abaixo da minima de 10.
  BASE-10   regra dos 10% (EUA antes do Donchian e B3 hoje): carteira de 21/09/2026 em
            pesos iguais, realiza o excesso acima de +10% sobre o custo para o caixa;
            o caixa alimenta swings no recuo de 2 ATR abaixo da EMA 20 (acima da media
            de 200), saida na EMA 20 ou 10 pregoes. Recomeca do zero em cada periodo.
  M1 Rompimento com filtro de tendencia: rompe a maxima de N dias, acao e indice
            acima da media de 200; saida por stop movel de K x ATR(20) abaixo do maior
            fechamento desde a entrada.  Variantes: (20, 3), (40, 3), (20, 4).
  M2 Tendencia com entrada em recuo: acao acima da media de 200 e indice acima da
            media de 200; compra quando o RSI de 2 dias cai abaixo de X; vende quando
            fecha acima da media de M dias ou apos D pregoes.
            Variantes: (X=10, M=5, D=10), (X=5, M=5, D=10), (X=10, M=10, D=15).
  M3 Rotacao por forca relativa: a cada 21 pregoes, fica com as VAGAS acoes de maior
            alta em L pregoes (pulando os ultimos S); caixa se o indice < media de 200.
            Variantes: (L=126, S=21), (L=252, S=21), (L=63, S=0).

Amostra: DENTRO 2015-2021 (escolhe a variante pelo retorno/queda) | FORA 2022 em diante
(so confere). Walk-forward anual: a cada ano, a variante com melhor retorno/queda nos 4
anos anteriores; resultado so dos anos "de fora" (2019 em diante), costurados.
Custos por lado (estimativa; ver relatorio): EUA 0,25% (taxa + spread do token) e B3
0,10% (emolumentos + spread; corretagem zero). Estresse: EUA 0,50% e B3 0,25%.
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd

DADOS = os.environ.get("DADOS", "dados")
INICIO, CORTE = pd.Timestamp("2015-01-01"), pd.Timestamp("2022-01-01")
VAGAS = 7
CUSTO = {"EUA": 0.0025, "B3": 0.0010}
ESTRESSE = {"EUA": 0.0050, "B3": 0.0025}
LIQ_MIN = {"EUA": 20e6, "B3": 10e6}          # media de 60 pregoes, moeda local
INDICE = {"EUA": "SPY", "B3": "BOVA11.SA"}
# carteiras de 21/09/2026 (descritas pelo Pedro), base da regra dos 10%
CARTEIRA_10 = {"EUA": ["CRWV", "NFLX", "MVLL", "ENPH", "BE", "UBER", "UNH"],
               "B3": ["LREN3", "EZTC3", "VAMO3", "TTEN3", "ALOS3", "INBR32", "HBRE3", "LJQQ3",
                      "MOVI3", "MRVE3", "SIMH3"]}

pct = lambda x, c=1: "n/d" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.{c}f}%".replace(".", ",")
num = lambda x, c=1: "n/d" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.{c}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ================================================================ DADOS
def carregar():
    d = {k: pd.read_csv(f"{DADOS}/yahoo_{k}.csv.gz", index_col=0, parse_dates=True)
         for k in ("open", "high", "low", "close", "volume")}
    return d


def universo(mercado, d):
    if mercado == "EUA":
        u = [a["ticker"] for a in json.load(open("universo_ondo.json"))["ativos"] if a["tipo"] == "acao"]
    else:
        u = [c for c in d["close"].columns if c.endswith(".SA") and c != "BOVA11.SA"]
    u += [t + (".SA" if mercado == "B3" else "") for t in CARTEIRA_10[mercado]]
    return list(dict.fromkeys(t for t in u if t in d["close"].columns))


class Mercado:
    """Matrizes (dias x tickers) e indicadores pre-calculados."""

    def __init__(self, nome, d):
        self.nome = nome
        tick = universo(nome, d)
        idx = d["close"][INDICE[nome]].dropna().index
        idx = idx[idx >= pd.Timestamp("2013-06-01")]
        f = lambda k: d[k].reindex(index=idx, columns=tick)
        self.dias, self.tick = idx, tick
        self.o, self.h, self.l, self.c, self.v = f("open"), f("high"), f("low"), f("close"), f("volume")
        ic = d["close"][INDICE[nome]].reindex(idx).ffill()
        self.indice = ic
        self.indice_ok = (ic > ic.rolling(200).mean()).values
        c = self.c
        self.sma200 = c.rolling(200, min_periods=200).mean()
        self.liq = (c * self.v).rolling(60, min_periods=40).mean()
        ant = c.shift(1)
        tr = pd.concat([(self.h - self.l).stack(), (self.h - ant).abs().stack(), (self.l - ant).abs().stack()],
                       axis=1).max(axis=1).unstack().reindex(index=idx, columns=tick)
        self.atr = tr.ewm(alpha=1 / 20, adjust=False).mean()
        self.ema20 = c.ewm(span=20, adjust=False).mean()
        delta = c.diff()
        up = delta.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
        dn = (-delta.clip(upper=0)).ewm(alpha=1 / 2, adjust=False).mean()
        self.rsi2 = 100 - 100 / (1 + up / dn.replace(0, np.nan))
        self.ret126 = c / c.shift(126) - 1
        self._cache = {}

    def maxh(self, n):
        k = ("maxh", n)
        if k not in self._cache:
            self._cache[k] = self.h.rolling(n, min_periods=n).max().shift(1)   # maxima dos n anteriores
        return self._cache[k]

    def minl(self, n):
        k = ("minl", n)
        if k not in self._cache:
            self._cache[k] = self.l.rolling(n, min_periods=n).min().shift(1)
        return self._cache[k]

    def sma(self, n):
        k = ("sma", n)
        if k not in self._cache:
            self._cache[k] = self.c.rolling(n, min_periods=n).mean()
        return self._cache[k]

    def mom(self, L, S):
        k = ("mom", L, S)
        if k not in self._cache:
            self._cache[k] = self.c.shift(S) / self.c.shift(L) - 1
        return self._cache[k]


# ================================================================ SIMULADOR
class Carteira:
    def __init__(self, custo):
        self.caixa, self.pos, self.trades, self.custo = 1.0, {}, [], custo

    def valor(self, precos):
        return self.caixa + sum(p["n"] * precos.get(t, p["ult"]) for t, p in self.pos.items())

    def comprar(self, t, preco, valor, i, extra=None):
        valor = min(valor, self.caixa)
        if valor <= 1e-6 or not preco or preco <= 0 or math.isnan(preco):
            return False
        n = valor / (preco * (1 + self.custo))
        self.caixa -= n * preco * (1 + self.custo)
        self.pos[t] = {"n": n, "pe": preco, "ie": i, "ult": preco, "topo": preco, **(extra or {})}
        return True

    def vender(self, t, preco, i, fracao=1.0):
        p = self.pos[t]
        if not preco or math.isnan(preco):
            preco = p["ult"]
        n = p["n"] * fracao
        self.caixa += n * preco * (1 - self.custo)
        if fracao >= 0.999:
            self.trades.append({"ret": preco * (1 - self.custo) / (p["pe"] * (1 + self.custo)) - 1,
                                "dias": i - p["ie"], "saida": i})
            del self.pos[t]
        else:
            p["n"] -= n


def simular(m, metodo, params, custo, inicio=INICIO):
    """Devolve (serie de patrimonio, lista de trades). Uma passada no periodo todo."""
    O, H, L, C = m.o.values, m.h.values, m.l.values, m.c.values
    liq_ok = (m.liq.values >= LIQ_MIN[m.nome])
    acima200 = C > m.sma200.values
    col = {t: j for j, t in enumerate(m.tick)}
    i0 = int(np.searchsorted(m.dias, inicio))
    cart = Carteira(custo)
    pat = np.full(len(m.dias), np.nan)
    saidas_pend, entradas_pend = set(), []          # decididos no fechamento de i-1
    atr, ema20, rsi2 = m.atr.values, m.ema20.values, m.rsi2.values
    if metodo in ("BASE", "M1"):
        N = 55 if metodo == "BASE" else params[0]
        nivel = m.maxh(N).values
        prio = m.ret126.values
    if metodo == "BASE":
        sai10 = m.minl(10).values
    if metodo == "M2":
        X, Mm, D = params
        smaM = m.sma(Mm).values
    if metodo == "M3":
        Lb, S = params
        score = m.mom(Lb, S).values
    if metodo == "BASE10":
        suf = ".SA" if m.nome == "B3" else ""
        fixas = [t + suf for t in CARTEIRA_10[m.nome] if t + suf in col]
        iniciou = False

    for i in range(i0, len(m.dias)):
        precos_o = {t: O[i, col[t]] for t in cart.pos}
        # ---- 1) saidas decididas ontem: na abertura de hoje
        for t in list(saidas_pend):
            if t in cart.pos:
                cart.vender(t, O[i, col[t]], i)
        saidas_pend.clear()
        # ---- 2) entradas na abertura (M2, M3, swings B3)
        if entradas_pend:
            vagas = VAGAS - len(cart.pos)
            for t in entradas_pend[:max(vagas, 0)]:
                if t not in cart.pos:
                    cart.comprar(t, O[i, col[t]], cart.valor(precos_o) / VAGAS, i)
            entradas_pend = []
        # ---- 3) rompimentos intradiarios (BASE, M1): no nivel ou na abertura se abrir acima
        if metodo in ("BASE", "M1") and i > 0 and m.indice_ok[i - 1]:
            vagas = VAGAS - len(cart.pos)
            if vagas > 0:
                cand = []
                for t, j in col.items():
                    if t in cart.pos or not liq_ok[i - 1, j] or math.isnan(nivel[i, j]):
                        continue
                    if metodo == "M1" and not acima200[i - 1, j]:
                        continue
                    if H[i, j] >= nivel[i, j] > 0:
                        cand.append((-(prio[i - 1, j] if not math.isnan(prio[i - 1, j]) else -9), t, j))
                for _, t, j in sorted(cand)[:vagas]:
                    preco = max(O[i, j], nivel[i, j])
                    cart.comprar(t, preco, cart.valor({**precos_o, **{t: preco}}) / VAGAS, i)
        # ---- 4) fechamento: marca, decide saidas/entradas de amanha
        precos_c = {}
        for t, p in cart.pos.items():
            x = C[i, col[t]]
            if not math.isnan(x):
                p["ult"] = x
                p["topo"] = max(p["topo"], x)
            precos_c[t] = p["ult"]
        if metodo == "BASE":
            for t in cart.pos:
                j = col[t]
                if C[i, j] < sai10[i, j]:
                    saidas_pend.add(t)
        elif metodo == "M1":
            K = params[1]
            for t, p in cart.pos.items():
                j = col[t]
                if C[i, j] < p["topo"] - K * atr[i, j]:
                    saidas_pend.add(t)
        elif metodo == "M2":
            for t, p in cart.pos.items():
                j = col[t]
                if C[i, j] > smaM[i, j] or i - p["ie"] >= D:
                    saidas_pend.add(t)
            if m.indice_ok[i]:
                cand = [(rsi2[i, j], t) for t, j in col.items()
                        if t not in cart.pos and liq_ok[i, j] and acima200[i, j] and rsi2[i, j] < X]
                entradas_pend = [t for _, t in sorted(cand)]
        elif metodo == "M3":
            if (i - i0) % 21 == 0:
                if not m.indice_ok[i]:
                    saidas_pend |= set(cart.pos)
                else:
                    ranking = sorted(((score[i, j], t) for t, j in col.items()
                                      if liq_ok[i, j] and not math.isnan(score[i, j])), reverse=True)
                    alvo = [t for _, t in ranking[:VAGAS]]
                    saidas_pend |= {t for t in cart.pos if t not in alvo}
                    entradas_pend = [t for t in alvo if t not in cart.pos]
        elif metodo == "BASE10":
            if not iniciou:                         # carteira de 21/09, pesos iguais, comprada no 1o dia
                ok = [t for t in fixas if not math.isnan(C[i, col[t]])]
                for t in ok:
                    cart.comprar(t, C[i, col[t]], 1.0 / len(ok) * (1 - 1e-9), i, {"base": None, "fixa": True})
                for t in ok:
                    cart.pos[t]["base"] = cart.pos[t]["n"] * cart.pos[t]["pe"]
                iniciou = True
                pat[i] = cart.valor(precos_c)
                continue
            for t, p in list(cart.pos.items()):
                j = col[t]
                if p.get("fixa"):                   # regra dos 10%: excesso vai para o caixa
                    valor = p["n"] * C[i, j]
                    if not math.isnan(valor) and valor >= p["base"] * 1.10:
                        cart.vender(t, C[i, j], i, fracao=(valor - p["base"]) / valor)
                        cart.trades.append({"ret": valor / p["base"] - 1, "dias": i - p["ie"], "saida": i})
                elif C[i, j] >= ema20[i, j] or i - p["ie"] >= 10:
                    saidas_pend.add(t)
            swings = [t for t, p in cart.pos.items() if not p.get("fixa")]
            if m.indice_ok[i] and len(swings) < 2 and cart.caixa > 0.02:
                cand = [((ema20[i, j] - C[i, j]) / atr[i, j], t) for t, j in col.items()
                        if t not in cart.pos and liq_ok[i, j] and acima200[i, j] and atr[i, j] > 0
                        and (ema20[i, j] - C[i, j]) / atr[i, j] >= 2]
                entradas_pend = [t for _, t in sorted(cand, reverse=True)][:2 - len(swings)]
        pat[i] = cart.valor(precos_c)
    s = pd.Series(pat, index=m.dias).iloc[i0:].ffill()
    return s, cart.trades


# ================================================================ METRICAS
def metricas(s, trades, dias, ini, fim):
    s = s[(s.index >= ini) & (s.index < fim)].dropna()
    if len(s) < 60:
        return None
    anos = (s.index[-1] - s.index[0]).days / 365.25
    cagr = (s.iloc[-1] / s.iloc[0]) ** (1 / anos) - 1
    dd = (s / s.cummax() - 1).min()
    i_ini, i_fim = np.searchsorted(dias, ini), np.searchsorted(dias, fim)
    tr = [t for t in trades if i_ini <= t["saida"] < i_fim]
    ganhos = [t["ret"] for t in tr if t["ret"] > 0]
    perdas = [t["ret"] for t in tr if t["ret"] <= 0]
    return {"cagr": cagr, "dd": dd, "rd": cagr / abs(dd) if dd else np.nan,
            "acerto": len(ganhos) / len(tr) if tr else np.nan,
            "gp": (np.mean(ganhos) / abs(np.mean(perdas))) if ganhos and perdas else np.nan,
            "ops_mes": 2 * len(tr) / (anos * 12), "dias": np.mean([t["dias"] for t in tr]) if tr else np.nan,
            "n": len(tr)}


def segurar(serie, ini, fim):
    s = serie[(serie.index >= ini) & (serie.index < fim)].dropna()
    anos = (s.index[-1] - s.index[0]).days / 365.25
    return {"cagr": (s.iloc[-1] / s.iloc[0]) ** (1 / anos) - 1, "dd": (s / s.cummax() - 1).min()}


VARIANTES = {
    "M1": [(20, 3.0), (40, 3.0), (20, 4.0)],
    "M2": [(10, 5, 10), (5, 5, 10), (10, 10, 15)],
    "M3": [(126, 21), (252, 21), (63, 0)],
}
NOMES = {"BASE": "Linha de base: Donchian 55/10 (bot hoje)",
         "BASE10": "Linha de base: regra dos 10% + swings (carteira de 21/09)",
         "M1": "M1 Rompimento + filtro de tendencia",
         "M2": "M2 Tendencia com entrada em recuo",
         "M3": "M3 Rotacao por forca relativa"}


def walk_forward(series, ini_ano=2019):
    """Cada ano, a variante com melhor retorno/queda nos 4 anos anteriores; costura os anos."""
    partes, escolhas = [], []
    ultimo = max(s.index[-1] for s in series.values())
    for ano in range(ini_ano, ultimo.year + 1):
        a0, a1 = pd.Timestamp(f"{ano - 4}-01-01"), pd.Timestamp(f"{ano}-01-01")
        melhor = max(series, key=lambda v: (lambda x: x["cagr"] / abs(x["dd"]) if x["dd"] else -9)(
            segurar(series[v], a0, a1)))
        escolhas.append(f"{ano}:{melhor}")
        s = series[melhor]
        r = s[(s.index >= a1) & (s.index < pd.Timestamp(f"{ano + 1}-01-01"))].pct_change().dropna()
        partes.append(r)
    r = pd.concat(partes)
    eq = (1 + r).cumprod()
    return eq, escolhas


def rodar(mercado, d, saida):
    m = Mercado(mercado, d)
    fim = m.dias[-1] + pd.Timedelta(days=1)
    L = saida
    L.append(f"\n{'=' * 78}\n{mercado}: {len(m.tick)} acoes | indice {INDICE[mercado]} | "
             f"{m.dias[0]:%d/%m/%Y} a {m.dias[-1]:%d/%m/%Y}\n{'=' * 78}")
    linhas = []
    bases = ["BASE", "BASE10"] if mercado == "EUA" else ["BASE10"]
    for metodo in bases + ["M1", "M2", "M3"]:
        variantes = VARIANTES.get(metodo, [None])
        res = {}
        for v in variantes:
            s, tr = simular(m, metodo, v, CUSTO[mercado])
            res[v] = (s, tr, metricas(s, tr, m.dias, INICIO, CORTE))
        escolhida = max(variantes, key=lambda v: (res[v][2] or {}).get("rd", -9))
        s, tr, dentro = res[escolhida]
        ini_fora = CORTE if metodo == "BASE10" else INICIO
        if metodo == "BASE10":
            s, tr = simular(m, metodo, escolhida, CUSTO[mercado], inicio=CORTE)
        fora = metricas(s, tr, m.dias, CORTE, fim)
        s2, tr2 = simular(m, metodo, escolhida, ESTRESSE[mercado], inicio=ini_fora)
        fora_estresse = metricas(s2, tr2, m.dias, CORTE, fim)
        wf, escolhas = walk_forward({v: res[v][0] for v in variantes}) if len(variantes) > 1 else (None, [])
        wf_m = segurar(wf, pd.Timestamp("2019-01-01"), fim) if wf is not None else None
        if metodo == "BASE10":
            s, tr = simular(m, metodo, escolhida, CUSTO[mercado], inicio=pd.Timestamp("2024-01-01"))
        desde24 = metricas(s, tr, m.dias, pd.Timestamp("2024-01-01"), fim)
        linhas.append((metodo, escolhida, dentro, fora, fora_estresse, wf_m, desde24))
        L.append(f"\n{NOMES[metodo]}" + (f" | variante escolhida no DENTRO: {escolhida}" if escolhida else ""))
        for v in variantes:
            x = res[v][2]
            if x and len(variantes) > 1:
                L.append(f"   dentro {v}: {pct(x['cagr'])} a.a. / queda {pct(x['dd'])} (ret/queda {num(x['rd'], 2)})")
        for rot, x in (("DENTRO 2015-21", dentro), ("FORA 2022-26", fora),
                       ("FORA custo estresse", fora_estresse), ("desde 2024", desde24)):
            if x:
                L.append(f"   {rot}: {pct(x['cagr'])} a.a. | queda {pct(x['dd'])} | acerto {pct(x['acerto'], 0)} | "
                         f"ganho/perda {num(x['gp'], 2)} | {num(x['ops_mes'], 1)} ordens/mes | "
                         f"{num(x['dias'], 0)} pregoes por operacao | {x['n']} operacoes")
        if wf_m:
            L.append(f"   walk-forward 2019-26 (so anos de fora): {pct(wf_m['cagr'])} a.a. | queda {pct(wf_m['dd'])} "
                     f"| escolhas {' '.join(escolhas)}")
    bh_d = segurar(m.indice, INICIO, CORTE)
    bh_f = segurar(m.indice, CORTE, fim)
    bh_24 = segurar(m.indice, pd.Timestamp("2024-01-01"), fim)
    L.append(f"\nSegurar {INDICE[mercado]}: dentro {pct(bh_d['cagr'])} / {pct(bh_d['dd'])} | "
             f"fora {pct(bh_f['cagr'])} / {pct(bh_f['dd'])} | desde 2024 {pct(bh_24['cagr'])} / {pct(bh_24['dd'])}")
    return linhas, (bh_d, bh_f, bh_24)


def tabela(mercado, linhas, bh):
    T = [f"\n### {mercado} (FORA da amostra: 2022 a hoje; custo base)",
         "| Metodo | Ret. a.a. | Queda max | Acertos | Ganho/perda | Ordens/mes | Pregoes/op | Ret. a.a. estresse | Walk-forward 2019+ | Dentro 2015-21 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for metodo, v, dentro, fora, est, wf, _ in linhas:
        if not fora:
            continue
        T.append(f"| {NOMES[metodo]}{' ' + str(v) if v else ''} | {pct(fora['cagr'])} | {pct(fora['dd'])} | "
                 f"{pct(fora['acerto'], 0)} | {num(fora['gp'], 2)} | {num(fora['ops_mes'], 1)} | {num(fora['dias'], 0)} | "
                 f"{pct(est['cagr']) if est else 'n/d'} | {pct(wf['cagr']) + ' / ' + pct(wf['dd']) if wf else '-'} | "
                 f"{pct(dentro['cagr']) + ' / ' + pct(dentro['dd']) if dentro else 'n/d'} |")
    T.append(f"| Segurar {INDICE[mercado]} | {pct(bh[1]['cagr'])} | {pct(bh[1]['dd'])} | - | - | - | - | - | - | "
             f"{pct(bh[0]['cagr'])} / {pct(bh[0]['dd'])} |")
    return T


def main():
    d = carregar()
    L = ["BACKTEST FASE 3 - linha de base x 3 metodos (spot, sem alavancagem)",
         f"Custos por lado: EUA {pct(CUSTO['EUA'], 2)} (estresse {pct(ESTRESSE['EUA'], 2)}) | "
         f"B3 {pct(CUSTO['B3'], 2)} (estresse {pct(ESTRESSE['B3'], 2)}) | {VAGAS} vagas"]
    md = []
    for mercado in ("EUA", "B3"):
        linhas, bh = rodar(mercado, d, L)
        md += tabela(mercado, linhas, bh)
    texto = "\n".join(L) + "\n\n" + "\n".join(md) + "\n"
    print(texto)
    open("relatorio_fase3.txt", "w", encoding="utf-8").write(texto)


if __name__ == "__main__":
    main()
