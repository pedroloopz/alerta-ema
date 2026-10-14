"""EXA long em swing (1 a 3 dias) nas ações dos EUA, com entrada em 3 parcelas e carteira alavancada.

Sinal (mesma regra do painel, em candles de 1h do pregão): RSI(14) < 25 em um dos 2 candles
anteriores e candle que fecha acima da máxima do anterior com volume >= 1,3x a média de 20.

Entrada em 3 parcelas iguais: 1/3 no fechamento do sinal; 1/3 limitada a -0,5 ATR; 1/3 a -1 ATR
(as limitadas só valem antes do stop). Stop em ATR(14) de 1h abaixo do fechamento do sinal.
Alvo em R (R = distância do preço médio planejado até o stop). Saída por tempo após 1, 2 ou 3
pregões (7 candles cada). Parcial: 50% no 1R e stop do resto vai para o preço médio.
Gap através do stop sai na abertura (pior preço). Custos: maker 0,02% nas limitadas e no alvo;
taker 0,05% + 0,03% de slippage no stop e na saída por tempo; funding de 0,01% a cada 8h
(3x por dia corrido) sobre o valor da posição.

Escolha da configuração só com os primeiros 75% do período; o resultado que vale é o do teste
(últimos 25%), que a escolha não viu.

Carteira: banca US$ 14.500 sem reinvestir; cada trade usa o mesmo valor nocional, até o limite
de exposição total; se não houver espaço, o sinal é ignorado.
Uso: DADOS=dados python painel/backtest_swing.py  -> grava relatorio_swing.txt
"""
import os

import numpy as np
import pandas as pd

DADOS = os.environ.get("DADOS", "dados")
SAIDA = os.path.join(os.path.dirname(__file__), "..", "relatorio_swing.txt")
MAKER, TAKER_SLIP, FUND_DIA = 0.0002, 0.0008, 3 * 0.0001
POR_DIA = 7
SEMIS = {"AMD", "NVDA", "AVGO", "MU", "TSM", "ASML", "AMAT", "LRCX", "KLAC", "MRVL", "QCOM", "INTC",
         "ARM", "ON", "TXN", "ADI", "SWKS", "SMCI", "TER", "ONTO", "ENTG", "COHU", "ACMR", "AEHR",
         "MXL", "STM", "TSEM", "UMC", "VSH", "AXTI", "CRDO", "SNDK", "WDC", "STX"}
BANCA = 14_500
CENARIOS = (  # nome, exposição máxima, nocional por trade
    ("2x por posição (margem isolada cabe na banca)", 29_000, 29_000 / 6),
    ("Exposição 45 mil (9 posições de 5 mil)", 45_000, 5_000),
)


def carregar():
    campos = {k: pd.read_csv(f"{DADOS}/yahoo_1h_{k}.csv.gz", index_col=0, parse_dates=True)
              for k in ("open", "high", "low", "close", "volume")}
    cols = [c for c in campos["close"].columns
            if not c.endswith(".SA") and not c.startswith("^") and "=" not in c and "-" not in c
            and c not in ("SPY", "QQQ")]
    out = {}
    for c in cols:
        df = pd.DataFrame({k: v[c] for k, v in campos.items()}).dropna()
        if len(df) > 1000:
            out[c] = df
    return out


def preparar(df):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    pc = c.shift()
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    vm = v.rolling(20).mean().shift()
    sinal = (rsi.rolling(2).min().shift() < 25) & (c > h.shift()) & (v >= 1.3 * vm) & (vm > 0)
    return atr.to_numpy(), np.flatnonzero(sinal.to_numpy())


def simular(df, atr, i, sl_atr, tp_r, dias, parcial):
    """Um trade. Devolve (retorno sobre o nocional cheio, nº de candles, saiu_no_lucro) ou None."""
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    n, a, e0 = len(df), atr[i], c[i]
    fim = i + 1 + dias * POR_DIA
    if fim > n or not a > 0:
        return None
    stop = e0 - sl_atr * a
    niveis = [e0, e0 - 0.5 * a, e0 - 1.0 * a]
    medio_plan = np.mean(niveis)
    risco = medio_plan - stop
    alvo = medio_plan + tp_r * risco
    cheias = [(e0, MAKER + TAKER_SLIP)]           # 1ª parcela a mercado
    pend = niveis[1:]
    resto, ganho, parc_feita = 1.0, 0.0, False

    def pl(preco, frac, custo_saida):
        if not cheias:
            return 0.0
        q = len(cheias) / 3
        med = np.mean([p for p, _ in cheias])
        custo_ent = np.mean([k for _, k in cheias])
        return frac * q * ((preco - med) / med - custo_ent - custo_saida)

    for j in range(i + 1, fim):
        stop_atual = stop
        if parc_feita:
            stop_atual = max(stop, np.mean([p for p, _ in cheias]))
        if l[j] <= stop_atual:                        # stop (gap sai na abertura)
            px = min(o[j], stop_atual)
            ganho += pl(px, resto, TAKER_SLIP)
            return ganho - FUND_DIA * (j - i) / POR_DIA * 1.4, j - i, ganho > 0
        for p in list(pend):                          # parcelas limitadas
            if l[j] <= p:
                cheias.append((min(o[j], p), MAKER)); pend.remove(p)
        if parcial and not parc_feita and h[j] >= medio_plan + risco:
            ganho += pl(medio_plan + risco, 0.5, MAKER); resto, parc_feita = 0.5, True
        if h[j] >= alvo:
            ganho += pl(max(o[j], alvo), resto, MAKER)
            return ganho - FUND_DIA * (j - i) / POR_DIA * 1.4, j - i, True
    ganho += pl(c[fim - 1], resto, TAKER_SLIP)
    # 7 candles de pregão ~ 1,4 dia corrido de funding no perp 24/7
    return ganho - FUND_DIA * dias * 1.4, fim - 1 - i, ganho > 0


def main():
    base = carregar()
    prep = {s: preparar(df) for s, df in base.items()}
    t0 = min(df.index.min() for df in base.values())
    t1 = max(df.index.max() for df in base.values())
    corte = t0 + (t1 - t0) * 0.75
    grade = [(sl, tp, d, p) for sl in (2.0, 3.0, 4.0) for tp in (1.5, 2.0, 3.0)
             for d in (1, 2, 3) for p in (False, True)]
    linhas = []
    for cfg in grade:
        for s, df in base.items():
            atr, sig = prep[s]
            for i in sig:
                r = simular(df, atr, i, *cfg)
                if r:
                    linhas.append((*cfg, s, df.index[i], df.index[i + r[1]], r[0], r[2]))
    t = pd.DataFrame(linhas, columns=["sl", "tp", "dias", "parcial", "ativo", "ent", "sai", "ret", "lucro"])
    t["teste"] = t["ent"] >= corte
    chave = ["sl", "tp", "dias", "parcial"]
    g = t.groupby(chave + ["teste"]).agg(n=("ret", "size"), acerto=("lucro", "mean"), ret=("ret", "mean"))
    g = g.unstack("teste")
    g.columns = [f"{a}_{'teste' if b else 'treino'}" for a, b in g.columns]
    g = g.sort_values("ret_treino", ascending=False)
    melhor = g.index[0]

    L = [f"EXA long swing (1h do pregão) — ações dos EUA, {len(base)} ativos, {t0:%d/%m/%Y} a {t1:%d/%m/%Y}",
         f"Escolha da configuração até {corte:%d/%m/%Y}; teste (não visto) depois disso.",
         "Retorno médio por trade sobre o nocional, já com taxas, slippage e funding. Sem alavancagem.", "",
         "10 melhores configurações no treino:",
         f"{'stop ATR':>8} {'alvo R':>6} {'dias':>4} {'parcial':>7} | {'n trei':>6} {'acerto':>6} {'ret%':>6} | {'n teste':>7} {'acerto':>6} {'ret%':>6}"]
    for k, x in g.head(10).iterrows():
        L.append(f"{k[0]:8.1f} {k[1]:6.1f} {k[2]:4d} {'sim' if k[3] else 'não':>7} | {x['n_treino']:6.0f} "
                 f"{x['acerto_treino']*100:5.0f}% {x['ret_treino']*100:+6.2f} | {x['n_teste']:7.0f} "
                 f"{x['acerto_teste']*100:5.0f}% {x['ret_teste']*100:+6.2f}")
    L += ["", "Efeito da parcial e do prazo (média de todas as configurações, período de teste):"]
    te = t[t["teste"]]
    for col in ("parcial", "dias"):
        for v, x in te.groupby(col):
            L.append(f"  {col}={v}: acerto {x['lucro'].mean()*100:.0f}%, ret médio {x['ret'].mean()*100:+.2f}%")

    m = t[(t[chave] == pd.Series(melhor, index=chave)).all(axis=1)].sort_values("ent")
    ms = m[m["ativo"].isin(SEMIS)]
    L += ["", f"Configuração escolhida: stop {melhor[0]} ATR, alvo {melhor[1]} R, {melhor[2]} dia(s), parcial {'sim' if melhor[3] else 'não'}",
          f"  semicondutores: {len(ms)} trades, acerto {ms['lucro'].mean()*100:.0f}%, ret médio {ms['ret'].mean()*100:+.2f}%",
          f"  demais ações:   {len(m)-len(ms)} trades, acerto {m[~m['ativo'].isin(SEMIS)]['lucro'].mean()*100:.0f}%, "
          f"ret médio {m[~m['ativo'].isin(SEMIS)]['ret'].mean()*100:+.2f}%"]

    rng, aleat = np.random.default_rng(0), []          # mesma saída com entrada sorteada
    for s, df in base.items():
        atr, sig = prep[s]
        for i in rng.choice(np.arange(30, len(df) - 25), size=5 * max(len(sig), 1), replace=False):
            r = simular(df, atr, i, *melhor)
            if r:
                aleat.append((df.index[i] >= corte, r[0], r[2]))
    al = pd.DataFrame(aleat, columns=["teste", "ret", "lucro"])
    for k, x in al.groupby("teste"):
        L.append(f"  entrada sorteada ({'teste' if k else 'treino'}): acerto {x['lucro'].mean()*100:.0f}%, "
                 f"ret médio {x['ret'].mean()*100:+.2f}%  <- quanto do resultado é só a alta do mercado")

    for nome, expo, noc in CENARIOS:
        for periodo, mm in (("período todo", m), ("só o teste", m[m["ent"] >= corte])):
            abertas, fechados = [], []
            for _, x in mm.iterrows():
                abertas = [a for a in abertas if a > x["ent"]]
                if (len(abertas) + 1) * noc > expo + 1:
                    continue
                abertas.append(x["sai"])
                fechados.append((x["sai"], x["ret"] * noc))
            if not fechados:
                continue
            f = pd.DataFrame(fechados, columns=["t", "pnl"]).sort_values("t").set_index("t")["pnl"]
            eq = BANCA + f.cumsum()
            dd = (eq - eq.cummax()).min()
            meses = max((f.index.max() - f.index.min()).days / 30.4, 1)
            mensal = f.resample("ME").sum()
            L += ["", f"Carteira — {nome}, {periodo} (banca US$ {BANCA:,}".replace(",", ".") + ")",
                  f"  trades {len(f)}, lucro total US$ {f.sum():,.0f} ({f.sum()/BANCA*100:+.0f}% da banca), "
                  f"média {f.sum()/BANCA/meses*100:+.1f}% ao mês".replace(",", "."),
                  f"  pior queda (drawdown) US$ {dd:,.0f} ({dd/BANCA*100:.0f}% da banca)".replace(",", "."),
                  f"  meses no lucro {(mensal > 0).mean()*100:.0f}%, pior mês {mensal.min()/BANCA*100:+.1f}%, "
                  f"melhor mês {mensal.max()/BANCA*100:+.1f}%"]
    L += ["", "Juros do consignado ~2,25% da banca ao mês: é o mínimo para empatar.",
          "Backtest não garante resultado futuro. Dados do pregão; o perp 24/7 pode ter preço diferente à noite."]
    texto = "\n".join(L)
    print(texto)
    with open(SAIDA, "w", encoding="utf-8") as fh:
        fh.write(texto + "\n")


if __name__ == "__main__":
    main()
