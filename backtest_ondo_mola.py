"""
SISTEMA MOLA - swing trade na ONDOUSDT, grafico de 4h (spot, so compra).

Leitura dos 6 meses (28/03 a 28/09/2026): a ONDO sobe em arrancadas curtas e cai
devagar. Menos barras verdes (46%), mas maiores (corpo medio 1,62% x 1,25%), retorno
de 4h com assimetria positiva (skew +0,87) e caudas gordas (curtose 8,5). Medias
cruzadas, Ichimoku puro e "consenso de medias" apanharam do serrote (drawdowns de
40-55%). O que funcionou foi comprar a mola nos dois momentos em que ela solta:

  Filtro  : fechamento diario de ONTEM acima da EMA 50 diaria (regime de alta).
  Motor A : MOLA COMPRIMIDA - Bollinger (20,2) estreita (largura no percentil 20
            das ultimas 180 barras) em algum momento das ultimas 18 barras, e o
            candle de 4h fecha ACIMA da banda superior. Assimetria (skew 60) > -0,5.
            Saida: fechamento abaixo da media da Bollinger.
  Motor B : MOLA ESTICADA - RSI(14) de 4h abaixo de 35 dentro do regime de alta.
            Saida: RSI acima de 60. Se durante a posicao disparar o motor A,
            a posicao passa a seguir a regra de saida do A.
  Stop    : 3 ATR(14) abaixo da entrada, fixo. Uma posicao por vez.
  Execucao: sinal no fechamento do candle, entrada/saida na abertura do seguinte.
  Custo   : 0,15% por lado (taxa 0,1% + derrapagem 0,05%).

Uso: python backtest_ondo_mola.py   (le coleta/ondo/*.csv, gera relatorio_ondo_mola.txt)
"""
import numpy as np
import pandas as pd

CUSTO = 0.0015
P = dict(sq=0.2, sqjan=18, skmin=-0.5, rsi_in=35, rsi_out=60, stop=3.0)
PERIODOS = {"10/2025-03/2026 antes (queda)": ("2025-08-15", "2026-03-28"),
            "6 meses (28/03-28/09/2026)": ("2026-03-28", "2026-09-29"),
            "tudo (15/08/2025-28/09/2026)": ("2025-08-15", "2026-09-29")}


def carrega(tf):
    return pd.read_csv(f"coleta/ondo/ONDOUSDT_{tf}.csv", parse_dates=["data"]).set_index("data")


def rsi(c, n=14):
    dl = c.diff()
    up = dl.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-dl.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def indicadores(d, dd):
    d = d.copy()
    tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(), (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d["rsi"] = rsi(d.close)
    m, sd = d.close.rolling(20).mean(), d.close.rolling(20).std()
    d["bbm"], d["bbu"] = m, m + 2 * sd
    d["bbw_pct"] = (4 * sd / m).rolling(180).rank(pct=True)
    d["skew60"] = np.log(d.close).diff().rolling(60).skew()
    reg = (dd.close > dd.close.ewm(span=50, adjust=False).mean()).shift(1)   # dia anterior fechado
    d["regime"] = reg.reindex(d.index.floor("D")).values == True
    squeeze = (d.bbw_pct < P["sq"]).rolling(P["sqjan"]).max() > 0
    d["motorA"] = d.regime & squeeze & (d.close > d.bbu) & (d.skew60 > P["skmin"])
    d["motorB"] = d.regime & (d.rsi < P["rsi_in"])
    return d


def simula(d, ini, fim):
    x = d[(d.index >= ini) & (d.index < fim)]
    o, l, c = x.open.values, x.low.values, x.close.values
    cap, pos, trades, eq = 1.0, None, [], []
    for i in range(len(x) - 1):
        if pos:
            saida = None
            if l[i] <= stop:
                saida, motivo = min(o[i], stop), "stop"
            elif pos == "A" and c[i] < x.bbm.iat[i]:
                saida, motivo = o[i + 1], "fechou abaixo da media BB"
            elif pos == "B" and x.rsi.iat[i] > P["rsi_out"]:
                saida, motivo = o[i + 1], "RSI > 60"
            elif pos == "B" and x.motorA.iat[i]:
                pos = "A"
            if saida is not None:
                r = saida / pe * (1 - CUSTO) / (1 + CUSTO) - 1
                cap *= 1 + r
                trades.append((x.index[ie], x.index[i + 1], pos, pe, saida, r, motivo))
                pos = None
        if not pos and (x.motorA.iat[i] or x.motorB.iat[i]):
            pos = "A" if x.motorA.iat[i] else "B"
            ie, pe = i + 1, o[i + 1]
            stop = pe - P["stop"] * x.atr.iat[i]
        eq.append(cap * (c[i] / pe if pos else 1))
    if pos:
        r = c[-1] / pe * (1 - CUSTO) / (1 + CUSTO) - 1
        cap *= 1 + r
        trades.append((x.index[ie], x.index[-1], pos, pe, c[-1], r, "em aberto"))
    t = pd.DataFrame(trades, columns=["entrada", "saida", "motor", "preco_ent", "preco_sai", "ret", "motivo"])
    eq = pd.Series(eq, index=x.index[:-1])
    return t, cap - 1, (eq / eq.cummax() - 1).min(), eq


def main():
    d = indicadores(carrega("4h"), carrega("1d"))
    out = [f"SISTEMA MOLA - ONDOUSDT 4h | parametros {P} | custo {CUSTO:.2%} por lado", ""]
    for nome, (a, b) in PERIODOS.items():
        t, tot, dd_, _ = simula(d, a, b)
        px = d.close[(d.index >= a) & (d.index < b)]
        g, p = t.ret[t.ret > 0], t.ret[t.ret <= 0]
        pf = g.sum() / -p.sum() if len(p) else float("inf")
        out.append(f"{nome}: sistema {tot:+.1%} (drawdown {dd_:.1%}) | {len(t)} trades, acerto {len(g) / max(1, len(t)):.0%}, "
                   f"ganho medio {g.mean() if len(g) else 0:+.1%}, perda media {p.mean() if len(p) else 0:+.1%}, fator de lucro {pf:.2f} "
                   f"| buy&hold {px.iat[-1] / px.iat[0] - 1:+.1%} (drawdown {(px / px.cummax() - 1).min():.1%})")
    t, *_ = simula(d, *PERIODOS["tudo (15/08/2025-28/09/2026)"])
    out += ["", "TRADES", t.assign(ret=(t.ret * 100).round(1)).to_string(index=False)]
    u = d.iloc[-1]
    out += ["", f"AGORA ({d.index[-1]:%d/%m %H:%M} UTC): preco {u.close:.4f} | RSI {u.rsi:.1f} | regime {'ALTA' if u.regime else 'fora'} "
            f"| BB sup {u.bbu:.4f} media {u.bbm:.4f} | largura pct {u.bbw_pct:.2f} | motor A {bool(u.motorA)} | motor B {bool(u.motorB)}"]
    open("relatorio_ondo_mola.txt", "w").write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
