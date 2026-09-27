"""
COMPARATIVO DE SISTEMAS DE SWING TRADE - SO EUA, SO COMPRADO, SPOT 1x.

Simula a SUA carteira: capital da carteira.json, no maximo 7 posicoes,
cada uma com 1/7 do patrimonio no momento da entrada (juros compostos).
Diario, 2015+. Sinal no fechamento, ordem no dia seguinte.

Sistemas (todos definidos antes de ver o resultado):
  1. IFR2 (Connors/QuantBrasil): IFR 2 < 10 acima da MMA 200; alvo = maxima
     dos 2 ultimos dias; saida no tempo em 7 dias
  2. 123 + Eden dos Traders: rompimento da maxima do candle sinal; alvo =
     amplitude do 123; stop = fundo
  3. 123 + inside bar + Eden
  4. Setup 9.1 (Larry Williams): MME 9 vira para cima, rompe a maxima;
     stop na minima do sinal; sai quando a MME 9 vira para baixo
  5. PFR comprador na tendencia (MME 80 subindo): alvo 2x o risco
  6. Donchian 20/10 (tartarugas): compra a maxima de 20 dias, sai na
     minima de 10 dias
  7. Bollinger: fecha abaixo da banda inferior acima da MMA 200; sai ao
     fechar acima da MMA 20 ou em 10 dias
  8. Pullback 2 ATR abaixo da MME 20 (o setup atual do bot)
  9. Momentum mensal: todo mes, as 7 com maior alta em 6 meses acima da
     MMA 200 (fica com a mesma se continuar entre as 7)
Referencia: comprar e segurar SPY e QQQ (sem vies de sobrevivencia).

Conservador: stop e alvo no mesmo candle -> stop; no dia da entrada so o
stop sai; custo por lado x1,5.
"""

import json

import numpy as np
import pandas as pd
import yfinance as yf

from simbolos import SIMBOLOS

CUSTO_LADO = 0.20          # % por lado, estimativa (taxa + spread)
ESTRESSE = 1.5
MAX_POS = 7
INICIO = "2015-01-01"
CORTE = pd.Timestamp("2022-01-01")
T_MIN = 2.0

CARTEIRA = ["ABNB", "BE", "MVLL", "UNH", "NFLX", "UBER", "ENPH", "CRWV"]
SEM_SUBJACENTE = {"SPCX", "OPENAI", "ANTHROPIC", "UNITREE", "MINIMAX", "ZHIPU",
                  "CXMT", "SAMSUNG", "SKHYNIX", "SKHY", "HYUNDAI", "MEITUAN",
                  "KUAISHOU", "XIAOMI", "POPMART", "BNC", "GPRO"}
MAPA = {"STXX": "STX", "BBX": "BB", "SNXX": "SPY"}

fmt = lambda x: f"{x:+.2f}".replace(".", ",")
pct1 = lambda x: f"{x:+.1f}%".replace(".", ",")
usd = lambda x: f"US$ {x:,.0f}".replace(",", ".")


# ---------------------------------------------------------------- dados
def universo():
    t = []
    for s in SIMBOLOS:
        x = s.replace("USDT", "").replace("USD1", "")
        if x not in SEM_SUBJACENTE:
            t.append(MAPA.get(x, x))
    return sorted(set(t + CARTEIRA))


def baixar(tickers):
    bruto = yf.download(tickers, start=INICIO, auto_adjust=True, progress=False,
                        group_by="ticker", threads=True)
    out = {}
    for t in tickers:
        try:
            df = bruto[t][["Open", "High", "Low", "Close"]].dropna()
        except KeyError:
            continue
        df = df[(df["Low"] > 0) & (df["High"] >= df["Low"])]
        if len(df) > 260:
            out[t] = df
    return out


def ind(df):
    c, h, l = df["Close"], df["High"], df["Low"]
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 2, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    x = pd.DataFrame({
        "o": df["Open"], "h": h, "l": l, "c": c,
        "rsi2": 100 - 100 / (1 + up / dn.replace(0, np.nan)),
        "sma200": c.rolling(200).mean(), "sma20": c.rolling(20).mean(),
        "ema9": c.ewm(span=9, adjust=False).mean(),
        "ema8": c.ewm(span=8, adjust=False).mean(),
        "ema20": c.ewm(span=20, adjust=False).mean(),
        "ema80": c.ewm(span=80, adjust=False).mean(),
        "atr": tr.rolling(14).mean(),
        "sd20": c.rolling(20).std(),
        "hi20": h.rolling(20).max(), "lo10": l.rolling(10).min(),
        "mom126": c / c.shift(126) - 1,
    })
    return x.iloc[200:]


# ---------------------------------------------------------------- motor por ativo
def seguir(x, i0, ent, stop, alvo_fn, sair_fn, prazo):
    """Acompanha a posicao aberta no dia i0. Devolve (i_saida, preco)."""
    o, h, l, c = x["o"].values, x["h"].values, x["l"].values, x["c"].values
    if stop is not None and l[i0] <= stop:
        return i0, min(stop, o[i0])
    for j in range(i0 + 1, len(c)):
        if stop is not None and o[j] <= stop:
            return j, o[j]
        if stop is not None and l[j] <= stop:
            return j, stop
        alvo = alvo_fn(j) if alvo_fn else None
        if alvo is not None:
            if o[j] >= alvo:
                return j, o[j]
            if h[j] >= alvo:
                return j, alvo
        if sair_fn and sair_fn(j):                  # decidido no fechamento de j-1
            return j, o[j]
        if prazo and j - i0 >= prazo:
            return j, c[j]
    return None, None


def rompe(x, i, nivel):
    """Ordem de compra stop em 'nivel' no dia i. Devolve preco ou None."""
    o, h = x["o"].values[i], x["h"].values[i]
    if o > nivel:
        return o
    if h >= nivel:
        return nivel
    return None


def sistema(nome, x):
    o, h, l, c = x["o"].values, x["h"].values, x["l"].values, x["c"].values
    n = len(c)
    v = {k: x[k].values for k in x.columns}
    trades, i = [], 2
    while i < n - 1:
        s = i          # candle do sinal (fechamento)
        e = i + 1      # dia da ordem
        ent = stop = None
        alvo_fn = sair_fn = None
        prazo = None
        score = 0.0

        if nome == "IFR2":
            if v["rsi2"][s] < 10 and c[s] > v["sma200"][s]:
                ent = o[e]
                alvo_fn = lambda j: max(h[j - 1], h[j - 2])
                prazo, score = 7, -v["rsi2"][s]
        elif nome in ("123 + Eden", "123 + inside bar"):
            sinal = l[s] > l[s - 1] and l[s - 1] < l[s - 2]
            if nome == "123 + inside bar":
                sinal = sinal and h[s] < h[s - 1]
            eden = v["ema8"][s] > v["ema8"][s - 1] and v["ema80"][s] > v["ema80"][s - 1]
            if sinal and eden:
                ent = rompe(x, e, h[s])
                if ent is not None:
                    amp = max(h[s - 2:s + 1]) - min(l[s - 2:s + 1])
                    stop = l[s - 1]
                    a = h[s] + amp
                    alvo_fn = lambda j, a=a: a
                    score = v["mom126"][s]
        elif nome == "Setup 9.1":
            if v["ema9"][s] > v["ema9"][s - 1] and v["ema9"][s - 1] <= v["ema9"][s - 2]:
                ent = rompe(x, e, h[s])
                if ent is not None:
                    stop = l[s]
                    sair_fn = lambda j: v["ema9"][j - 1] < v["ema9"][j - 2]
                    score = v["mom126"][s]
        elif nome == "PFR na tendencia":
            pfr = l[s] < min(l[s - 1], l[s - 2]) and c[s] > c[s - 1]
            if pfr and v["ema80"][s] > v["ema80"][s - 1]:
                ent = rompe(x, e, h[s])
                if ent is not None and ent > l[s]:
                    stop = l[s]
                    a = ent + 2 * (ent - stop)
                    alvo_fn = lambda j, a=a: a
                    score = v["mom126"][s]
        elif nome == "Donchian 20/10":
            nivel = v["hi20"][s]
            ent = rompe(x, e, nivel)
            if ent is not None:
                sair_fn = lambda j: c[j - 1] < v["lo10"][j - 2]
                score = v["mom126"][s]
        elif nome == "Bollinger na tendencia":
            if c[s] < v["sma20"][s] - 2 * v["sd20"][s] and c[s] > v["sma200"][s]:
                ent = o[e]
                sair_fn = lambda j: c[j - 1] > v["sma20"][j - 1]
                prazo, score = 10, -(c[s] / v["sma20"][s])
        elif nome == "Pullback 2 ATR (bot atual)":
            if c[s] <= v["ema20"][s] - 2 * v["atr"][s]:
                ent = o[e]
                sair_fn = lambda j: c[j - 1] > v["ema20"][j - 1]
                prazo, score = 20, (v["ema20"][s] - c[s]) / v["atr"][s]

        if ent is None or ent <= 0:
            i += 1
            continue
        js, px = seguir(x, e, ent, stop, alvo_fn, sair_fn, prazo)
        if js is None:
            break
        trades.append((x.index[e], x.index[js], ent, px, score))
        i = max(js, e)
    return trades


def momentum(dados):
    """Todo 1o pregao do mes: as 7 maiores altas em 6 meses acima da MMA 200."""
    closes = pd.DataFrame({t: d["c"] for t, d in dados.items()})
    opens = pd.DataFrame({t: d["o"] for t, d in dados.items()})
    mom = pd.DataFrame({t: d["mom126"] for t, d in dados.items()})
    ok = pd.DataFrame({t: d["c"] > d["sma200"] for t, d in dados.items()})
    dias = closes.index
    meses = dias.to_series().groupby([dias.year, dias.month]).first().values
    aberto, trades = {}, []
    for k, d in enumerate(meses):
        pos = dias.get_loc(d)
        if pos == 0:
            continue
        ontem = dias[pos - 1]
        rank = mom.loc[ontem].where(ok.loc[ontem]).dropna().sort_values(ascending=False)
        top = list(rank.index[:MAX_POS])
        for t in list(aberto):
            if t not in top and not np.isnan(opens.at[d, t]):
                ed, ep = aberto.pop(t)
                trades.append((t, ed, d, ep, opens.at[d, t], 0.0))
        for t in top:
            if t not in aberto and not np.isnan(opens.at[d, t]):
                aberto[t] = (d, opens.at[d, t])
    return trades


# ---------------------------------------------------------------- carteira
def carteira(trades, dados, capital):
    """Maximo MAX_POS posicoes, 1/MAX_POS do patrimonio cada. Marca a mercado."""
    custo = CUSTO_LADO * ESTRESSE / 100
    closes = pd.DataFrame({t: d["c"] for t, d in dados.items()}).ffill()
    dias = closes.index
    por_dia = {}
    for tr in trades:
        por_dia.setdefault(tr[1], []).append(tr)
    caixa, abertas, curva, feitos = capital, [], [], []
    for d in dias:
        # saidas de dias anteriores ja liberaram vaga; entradas abrem na abertura
        cand = sorted(por_dia.get(d, []), key=lambda t: -t[5])
        for t, ed, sd, ep, xp, _ in cand:
            if len(abertas) >= MAX_POS:
                break
            if any(a[0] == t for a in abertas):
                continue
            patr = caixa + sum(a[3] * closes.at[d, a[0]] for a in abertas)
            valor = min(patr / MAX_POS, caixa)
            if valor <= 0:
                continue
            q = valor / (ep * (1 + custo))
            caixa -= valor
            abertas.append([t, sd, xp, q, ep])
        restantes = []
        for a in abertas:
            if a[1] == d:
                caixa += a[3] * a[2] * (1 - custo)
                feitos.append((a[1], (a[2] * (1 - custo)) / (a[4] * (1 + custo)) - 1))
            else:
                restantes.append(a)
        abertas = restantes
        curva.append(caixa + sum(a[3] * closes.at[d, a[0]] for a in abertas))
    return pd.Series(curva, index=dias), feitos


def cagr(s):
    anos = (s.index[-1] - s.index[0]).days / 365.25
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / anos) - 1) * 100 if anos > 0 else 0.0


def dd(s):
    return ((s / s.cummax()) - 1).min() * 100


def t_stat(x):
    x = np.asarray(x)
    return 0.0 if len(x) < 3 or x.std() == 0 else x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))


# ---------------------------------------------------------------- main
def main():
    capital = json.load(open("carteira.json"))["capital_inicial"]
    tick = universo()
    brutos = baixar(tick + ["SPY", "QQQ"])
    ref = {k: brutos.pop(k)["Close"] for k in ("SPY", "QQQ") if k in brutos}
    dados = {t: ind(df) for t, df in brutos.items()}
    dados = {t: x for t, x in dados.items() if len(x) > 100}

    nomes = ["IFR2", "123 + Eden", "123 + inside bar", "Setup 9.1", "PFR na tendencia",
             "Donchian 20/10", "Bollinger na tendencia", "Pullback 2 ATR (bot atual)"]
    resultados = []
    for nome in nomes:
        trades = [(t, *tr) for t, x in dados.items() for tr in sistema(nome, x)]
        resultados.append((nome, trades))
    resultados.append(("Momentum mensal (top 7)", momentum(dados)))

    # referencia
    refs = {}
    for k, s in ref.items():
        s = s[s.index >= dados[next(iter(dados))].index[0]]
        refs[k] = (cagr(s), dd(s), cagr(s[s.index >= CORTE]), dd(s[s.index >= CORTE]))

    linhas, tabela = [], []
    c2 = CUSTO_LADO * 2
    for nome, trades in resultados:
        curva, feitos = carteira(trades, dados, capital)
        r = pd.DataFrame(feitos, columns=["saida", "ret"])
        r["ret"] *= 100
        tr_, va_ = r[r["saida"] < CORTE]["ret"], r[r["saida"] >= CORTE]["ret"]
        dobro = (r["ret"] - c2 * (2 - ESTRESSE)).mean() if len(r) else 0
        cv = curva[curva.index >= CORTE]
        linha = dict(
            nome=nome, n=len(r), acerto=(r["ret"] > 0).mean() * 100 if len(r) else 0,
            ev_tr=tr_.mean() if len(tr_) else 0, ev_va=va_.mean() if len(va_) else 0,
            dobro=dobro, t=t_stat(va_), cagr=cagr(curva), dd=dd(curva),
            cagr_va=cagr(cv), dd_va=dd(cv), final=curva.iloc[-1],
        )
        checks = [linha["ev_tr"] > 0, linha["ev_va"] > 0, linha["dobro"] > 0,
                  linha["t"] >= T_MIN, linha["cagr_va"] > refs["SPY"][2]]
        linha["ok"] = sum(checks)
        linha["aprovado"] = all(checks)
        tabela.append(linha)

    tabela.sort(key=lambda z: (z["aprovado"], z["ok"], z["cagr_va"] / max(1, -z["dd_va"])), reverse=True)
    ini = min(x.index[0] for x in dados.values())
    fim = max(x.index[-1] for x in dados.values())
    linhas += [
        "COMPARATIVO DE SISTEMAS - EUA, SO COMPRADO, SPOT 1x",
        f"{len(dados)} acoes | {ini:%d/%m/%Y} a {fim:%d/%m/%Y} | validacao a partir de {CORTE:%d/%m/%Y}",
        f"carteira simulada: {usd(capital)}, ate {MAX_POS} posicoes de 1/{MAX_POS} | custo {fmt(CUSTO_LADO)}% por lado x{ESTRESSE}",
        "",
        "Aprovacao: lucro/trade > 0 no treino e na validacao, > 0 com custo em dobro,",
        f"t >= {T_MIN} na validacao e CAGR na validacao acima do SPY.",
        "",
    ]
    status = lambda z: "APROVADO ✅" if z["aprovado"] else f"reprovado ({z['ok']}/5 criterios)"
    for z in tabela:
        linhas.append(f"{z['nome']}: {status(z)}")
        linhas.append(f"  trades {z['n']} | acerto {z['acerto']:.0f}% | lucro/trade treino {fmt(z['ev_tr'])}% | valid {fmt(z['ev_va'])}% | custo x2 {fmt(z['dobro'])}% | t {z['t']:.1f}")
        linhas.append(f"  carteira: CAGR {pct1(z['cagr'])} (valid {pct1(z['cagr_va'])}) | pior queda {pct1(z['dd'])} (valid {pct1(z['dd_va'])}) | {usd(capital)} -> {usd(z['final'])}")
        linhas.append("")
    for k, (a, b, cva, dva) in refs.items():
        linhas.append(f"Comprar e segurar {k}: CAGR {pct1(a)} (valid {pct1(cva)}) | pior queda {pct1(b)} (valid {pct1(dva)})")
    linhas.append("")
    linhas.append("Obs.: universo = acoes de hoje (vies de sobrevivencia favorece todos os sistemas).")

    aprov = [z["nome"] for z in tabela if z["aprovado"]]
    topo = "VEREDITO: " + (", ".join(aprov) if aprov else f"nenhum aprovado; menos pior = {tabela[0]['nome']}")
    texto = "\n".join([topo, ""] + linhas)
    print(texto)
    open("relatorio_sistemas.txt", "w").write(texto)
    pd.DataFrame(tabela).to_csv("sistemas.csv", index=False)


if __name__ == "__main__":
    main()
