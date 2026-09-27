"""
MAXIMAS E MINIMAS (Larry Williams / QuantBrasil) - SO EUA, SO COMPRADO, SPOT 1x.

Regra base (igual ao artigo do QuantBrasil):
  - entrada: ordem na MINIMA dos 2 candles anteriores (abriu abaixo -> abertura)
  - alvo:    MAXIMA dos 2 candles anteriores, atualizada a cada candle
             (abriu acima -> abertura)
Variacoes testadas, todas definidas ANTES de ver o resultado:
  - filtro: nenhum | tendencia (fechamento > MME 80 e MME 80 subindo)
  - stop:   nenhum | tempo (N candles) | % fixo | 1,5 ATR
Tempos graficos: 120 min (ultimos ~2 anos, limite do Yahoo) e diario (2015+).
Candle de 120 min no pregao: 9h30-11h30, 11h30-13h30, 13h30-16h (NY).

Conservador de proposito:
  - no candle da entrada, so o stop pode sair (nao sabemos a ordem dos precos)
  - stop e alvo no mesmo candle -> conta o stop
  - custo por lado aplicado na entrada e na saida; teste com custo x1,5 e x2

Aprovada so se passar em TUDO:
  1. lucro por trade > 0 no treino (custo x1,5)
  2. lucro por trade > 0 na validacao (custo x1,5)
  3. lucro por trade > 0 com custo em dobro (periodo todo)
  4. t >= 2 na validacao (nao e sorte)
  5. rende mais por dia investido que comprar e segurar as mesmas acoes
"""

import os

import numpy as np
import pandas as pd
import yfinance as yf

from simbolos import SIMBOLOS

# ---------------------------------------------------------------- regras
CUSTO_LADO = 0.20          # % por lado (taxa + spread). Ajuste para a sua conta
ESTRESSE = 1.5
TAMANHO = 1000.0           # US$ por trade
T_MIN = 2.0
EMA_TEND = 80
ATR_N = 14

CARTEIRA = ["ABNB", "BE", "MVLL", "UNH", "NFLX", "UBER", "ENPH", "CRWV"]
SEM_SUBJACENTE = {"SPCX", "OPENAI", "ANTHROPIC", "UNITREE", "MINIMAX", "ZHIPU",
                  "CXMT", "SAMSUNG", "SKHYNIX", "SKHY", "HYUNDAI", "MEITUAN",
                  "KUAISHOU", "XIAOMI", "POPMART", "BNC", "GPRO"}
MAPA = {"STXX": "STX", "BBX": "BB", "SNXX": "SPY"}

# (rotulo, filtro_tendencia, tipo_stop, parametro)
VARIANTES_120 = [
    ("classico, sem stop",            False, None,   None),
    ("stop tempo 6 candles (2 dias)", False, "tempo", 6),
    ("stop tempo 15 candles (5 dias)", False, "tempo", 15),
    ("stop 3%",                       False, "pct",  3.0),
    ("stop 1,5 ATR",                  False, "atr",  1.5),
    ("tendencia, sem stop",           True,  None,   None),
    ("tendencia + stop 6 candles",    True,  "tempo", 6),
    ("tendencia + stop 3%",           True,  "pct",  3.0),
    ("tendencia + stop 1,5 ATR",      True,  "atr",  1.5),
]
VARIANTES_D = [
    ("classico, sem stop",        False, None,   None),
    ("stop tempo 3 dias",         False, "tempo", 3),
    ("stop 5%",                   False, "pct",  5.0),
    ("stop 1,5 ATR",              False, "atr",  1.5),
    ("tendencia, sem stop",       True,  None,   None),
    ("tendencia + stop 3 dias",   True,  "tempo", 3),
    ("tendencia + stop 5%",       True,  "pct",  5.0),
    ("tendencia + stop 1,5 ATR",  True,  "atr",  1.5),
]

fmt = lambda x: f"{x:+.2f}".replace(".", ",")
usd = lambda x: f"{x:+,.0f}".replace(",", ".")


# ---------------------------------------------------------------- dados
def universo():
    tick = []
    for s in SIMBOLOS:
        t = s.replace("USDT", "").replace("USD1", "")
        if t in SEM_SUBJACENTE:
            continue
        tick.append(MAPA.get(t, t))
    for t in CARTEIRA:
        if t not in tick:
            tick.append(t)
    return sorted(set(tick))


def baixar(tickers, **kw):
    bruto = yf.download(tickers, auto_adjust=True, progress=False,
                        group_by="ticker", threads=True, **kw)
    saida = {}
    for t in tickers:
        try:
            df = bruto[t][["Open", "High", "Low", "Close"]].dropna()
        except KeyError:
            continue
        if len(df) > 200:
            saida[t] = df
    return saida


def para_120(df):
    """60 min -> 3 candles por pregao: 9h30-11h30, 11h30-13h30, 13h30-16h."""
    idx = df.index.tz_convert("America/New_York")
    df = df.copy()
    df.index = idx
    minuto = idx.hour * 60 + idx.minute
    bloco = np.where(minuto < 11 * 60 + 30, 0, np.where(minuto < 13 * 60 + 30, 1, 2))
    chave = [idx.date, bloco]
    g = df.groupby(chave)
    out = pd.DataFrame({
        "Open": g["Open"].first(), "High": g["High"].max(),
        "Low": g["Low"].min(), "Close": g["Close"].last(),
    })
    out.index = [pd.Timestamp(d) + pd.Timedelta(hours=9.5 + 2 * b) for d, b in out.index]
    return out


# ---------------------------------------------------------------- motor
def simular(df, tendencia, tipo_stop, par):
    o, h, l, c = (df[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    n = len(c)
    entrada_ref = np.minimum(np.roll(l, 1), np.roll(l, 2))
    alvo_ref = np.maximum(np.roll(h, 1), np.roll(h, 2))
    ema = pd.Series(c).ewm(span=EMA_TEND, adjust=False).mean().to_numpy()
    tr = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1))))
    atr = pd.Series(tr).rolling(ATR_N).mean().to_numpy()
    datas = df.index

    trades = []
    dentro = False
    for i in range(max(EMA_TEND, ATR_N) + 2, n):
        if dentro:
            barras += 1
            alvo = alvo_ref[i]
            saida = None
            if stop is not None and o[i] <= stop:
                saida = o[i]
            elif stop is not None and l[i] <= stop:
                saida = stop
            elif o[i] >= alvo:
                saida = o[i]
            elif h[i] >= alvo:
                saida = alvo
            elif tipo_stop == "tempo" and barras >= par:
                saida = c[i]
            if saida is not None:
                trades.append((datas[ent_i], datas[i], ent, saida, barras))
                dentro = False
            continue

        if tendencia and not (c[i - 1] > ema[i - 1] and ema[i - 1] > ema[i - 2]):
            continue
        ref = entrada_ref[i]
        if l[i] >= ref:
            continue
        ent = o[i] if o[i] < ref else ref
        stop = None
        if tipo_stop == "pct":
            stop = ent * (1 - par / 100)
        elif tipo_stop == "atr" and not np.isnan(atr[i - 1]):
            stop = ent - par * atr[i - 1]
        # no proprio candle da entrada so o stop pode sair
        if stop is not None and l[i] <= stop:
            trades.append((datas[i], datas[i], ent, stop, 0))
            continue
        dentro, ent_i, barras = True, i, 0
    return trades


def rodar(dados, variantes):
    res = {}
    for rot, tend, tipo, par in variantes:
        linhas = []
        for t, df in dados.items():
            for ent_d, sai_d, ent, sai, barras in simular(df, tend, tipo, par):
                bruto = (sai / ent - 1) * 100
                linhas.append((t, ent_d, sai_d, bruto, barras))
        res[rot] = pd.DataFrame(linhas, columns=["ticker", "entrada", "saida", "bruto", "barras"])
    return res


def comprar_e_segurar(dados, corte):
    """Retorno medio por candle das mesmas acoes, treino e validacao."""
    tr, va = [], []
    for df in dados.values():
        r = df["Close"].pct_change().dropna() * 100
        tr.append(r[r.index < corte])
        va.append(r[r.index >= corte])
    return pd.concat(tr).mean(), pd.concat(va).mean()


def t_stat(x):
    return 0.0 if len(x) < 3 or x.std() == 0 else x.mean() / (x.std() / np.sqrt(len(x)))


def avaliar(rot, tr, corte, bh_va, linhas):
    if tr.empty:
        linhas.append(f"{rot}: sem trades")
        return None
    c = CUSTO_LADO * 2
    liq = lambda x, m: x["bruto"] - c * m
    treino, valid = tr[tr["entrada"] < corte], tr[tr["entrada"] >= corte]
    e_tr, e_va = liq(treino, ESTRESSE).mean(), liq(valid, ESTRESSE).mean()
    e_dobro = liq(tr, 2).mean()
    t_va = t_stat(liq(valid, ESTRESSE))
    por_barra = liq(valid, ESTRESSE).sum() / valid["barras"].clip(lower=1).sum()
    acerto = (liq(tr, ESTRESSE) > 0).mean() * 100
    ganhos, perdas = liq(tr, ESTRESSE)[lambda x: x > 0], liq(tr, ESTRESSE)[lambda x: x <= 0]
    pior = liq(tr, ESTRESSE).min()

    checks = [
        (e_tr > 0, f"lucro/trade no treino: {fmt(e_tr)}%"),
        (e_va > 0, f"lucro/trade na validacao: {fmt(e_va)}%"),
        (e_dobro > 0, f"com custo em dobro: {fmt(e_dobro)}%"),
        (t_va >= T_MIN, f"t na validacao: {t_va:.1f}".replace(".", ",")),
        (por_barra > bh_va, f"por candle investido: {fmt(por_barra)}% x segurar {fmt(bh_va)}%"),
    ]
    ok = all(x for x, _ in checks)
    linhas.append(f"{rot}: {'APROVADA ✅' if ok else 'REPROVADA'}")
    for x, txt in checks:
        linhas.append(f"  {'✅' if x else '❌'} {txt}")
    linhas.append(
        f"  {len(tr)} trades | acerto {acerto:.0f}% | ganho medio {fmt(ganhos.mean() if len(ganhos) else 0)}% "
        f"| perda media {fmt(perdas.mean() if len(perdas) else 0)}% | pior {fmt(pior)}% "
        f"| dura {tr['barras'].mean():.1f} candles".replace(".", ",", 1)
    )
    linhas.append(f"  em US$ 1.000 por trade (custo x1,5): {usd(liq(tr, ESTRESSE).sum() * TAMANHO / 100)}")
    linhas.append("")
    return ok, e_va, rot


def bloco(nome, dados, variantes, corte, linhas):
    linhas.append(f"===== {nome} =====")
    ini = min(df.index[0] for df in dados.values())
    fim = max(df.index[-1] for df in dados.values())
    linhas.append(f"{len(dados)} acoes | {ini:%d/%m/%Y} a {fim:%d/%m/%Y} | validacao a partir de {corte:%d/%m/%Y}")
    _, bh_va = comprar_e_segurar(dados, corte)
    res = rodar(dados, variantes)
    vered = []
    for rot, *_ in variantes:
        v = avaliar(rot, res[rot], corte, bh_va, linhas)
        if v:
            vered.append(v)

    # sua carteira, na melhor variante pela validacao
    melhor = max(vered, key=lambda v: v[1])[2] if vered else None
    if melhor:
        tr = res[melhor]
        linhas.append(f"Sua carteira na variante '{melhor}' (custo x1,5, lucro por trade):")
        for t in CARTEIRA:
            x = tr[tr["ticker"] == t]
            if len(x):
                m = (x["bruto"] - CUSTO_LADO * 2 * ESTRESSE).mean()
                linhas.append(f"  {t}: {len(x)} trades, {fmt(m)}%")
            else:
                linhas.append(f"  {t}: sem dados")
        linhas.append("")
    return vered


def main():
    tick = universo()
    linhas = ["MAXIMAS E MINIMAS - EUA, SO COMPRADO, SPOT 1x",
              f"custo estimado {fmt(CUSTO_LADO)}% por lado; testado a x{ESTRESSE} e x2",
              ""]

    d120 = {t: para_120(df) for t, df in baixar(tick, period="730d", interval="60m").items()}
    d120 = {t: df for t, df in d120.items() if len(df) > 300}
    datas = sorted({d for df in d120.values() for d in df.index})
    corte120 = datas[len(datas) // 2]
    v120 = bloco("120 MINUTOS", d120, VARIANTES_120, corte120, linhas)

    dd = baixar(tick, start="2015-01-01", interval="1d")
    vd = bloco("DIARIO", dd, VARIANTES_D, pd.Timestamp("2022-01-01"), linhas)

    aprov = [r for ok, _, r in v120 if ok] + [f"diario: {r}" for ok, _, r in vd if ok]
    topo = ["VEREDITO: " + ("aprovadas -> " + "; ".join(aprov) if aprov else "nenhuma variante passou em tudo"), ""]
    texto = "\n".join(topo + linhas)
    print(texto)
    with open("relatorio_maxmin.txt", "w") as f:
        f.write(texto)


if __name__ == "__main__":
    main()
