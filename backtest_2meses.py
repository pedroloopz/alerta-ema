"""
ESTIMATIVA PARA 2 MESES (42 pregoes): Donchian escolhido x sistema atual.

Nao e previsao: e a distribuicao de TODAS as janelas de 42 pregoes da
validacao (2022-2026), aplicada ao seu capital da carteira.json.

Sistemas:
  A. Donchian 55/10 + filtro SPY + tamanho por vol, 5 posicoes, spot 1x
  B. Atual - nucleo: as 7 acoes da carteira, US$ 1.000 cada, excesso acima de
     +10% vai para a reserva (parada, sem render)
  C. Atual - swings: pullback 2 ATR abaixo da MME 20 (backtest_sistemas.py)
  D. QQQ comprar e segurar (referencia)
Custos iguais aos testes anteriores (x1,5).
"""

import json

import numpy as np
import pandas as pd

import backtest_donchian as bd
import backtest_sistemas as bs

JANELA = 42
CORTE = pd.Timestamp("2022-01-01")
fmt = lambda x: f"{x:+.1f}%".replace(".", ",")
usd = lambda x: f"US$ {x:+,.0f}".replace(",", ".")


def distribuicao(curva):
    s = curva[curva.index >= CORTE].dropna()
    r = (s.shift(-JANELA) / s - 1).dropna() * 100
    return dict(med=r.median(), media=r.mean(), p10=r.quantile(0.10), p90=r.quantile(0.90),
                perda=(r < 0).mean() * 100, pior=r.min(), melhor=r.max(), n=len(r))


def nucleo(capital, ativos):
    bruto = bd.yf.download(ativos, start="2021-06-01", auto_adjust=True, progress=False, group_by="ticker")
    closes = pd.DataFrame({t: bruto[t]["Close"] for t in ativos if t in bruto.columns.get_level_values(0)}).ffill()
    closes = closes[closes.index >= CORTE]
    base = capital / len(ativos)
    custo = 0.20 / 100 * 1.5
    qtd, reserva, caixa_ocioso, curva = {}, 0.0, 0.0, []
    for d, linha in closes.iterrows():
        for t in closes.columns:
            p = linha[t]
            if np.isnan(p):
                continue
            if t not in qtd:
                qtd[t] = base * (1 - custo) / p
            valor = qtd[t] * p
            if valor > base * 1.10:
                vende = valor - base
                qtd[t] -= vende / p
                reserva += vende * (1 - custo)
        investido = sum(qtd[t] * linha[t] for t in qtd if not np.isnan(linha[t]))
        ocioso = base * (len(closes.columns) - len(qtd))
        curva.append(investido + reserva + ocioso)
    return pd.Series(curva, index=closes.index), list(closes.columns)


def main():
    cart = json.load(open("carteira.json"))
    capital = cart["capital_inicial"]
    ativos = list(cart["ativos"])

    # A. Donchian escolhido
    tick, dias, campos, spy, qqq = bd.carregar()
    idx = pd.DatetimeIndex(dias)
    spy_ok = spy > pd.Series(spy).rolling(200).mean().to_numpy()
    tr = bd.gerar_trades(campos, 55, 10, 1)
    curva_a, _, _ = bd.simular(tr, campos, spy_ok, 5, 1, "vol", True)
    A = pd.Series(curva_a, index=idx)

    # B. nucleo atual
    B, usados = nucleo(capital, ativos)

    # C. swings atuais (pullback 2 ATR) como carteira
    brutos = bs.baixar(bs.universo())
    dados = {t: bs.ind(df) for t, df in brutos.items()}
    dados = {t: x for t, x in dados.items() if len(x) > 100}
    trades = [(t, *x) for t, df in dados.items() for x in bs.sistema("Pullback 2 ATR (bot atual)", df)]
    C, _ = bs.carteira(trades, dados, capital)

    D = pd.Series(qqq, index=idx)

    linhas = [f"ESTIMATIVA PARA 2 MESES ({JANELA} pregoes) - capital {usd(capital)[1:]}",
              f"base: todas as janelas de {JANELA} pregoes de {CORTE:%d/%m/%Y} a {idx[-1]:%d/%m/%Y}",
              f"nucleo atual usa: {', '.join(usados)}", ""]
    for nome, s in [("A. Donchian 55/10 escolhido", A), ("B. Atual - nucleo 7 acoes com regra +10%", B),
                    ("C. Atual - swings pullback 2 ATR", C), ("D. QQQ segurar", D)]:
        d = distribuicao(s)
        linhas += [nome,
                   f"  mediana {fmt(d['med'])} ({usd(capital * d['med'] / 100)}) | media {fmt(d['media'])}",
                   f"  cenario ruim (10%) {fmt(d['p10'])} ({usd(capital * d['p10'] / 100)}) | bom (90%) {fmt(d['p90'])} ({usd(capital * d['p90'] / 100)})",
                   f"  chance de terminar no prejuizo {d['perda']:.0f}% | pior janela {fmt(d['pior'])} | melhor {fmt(d['melhor'])}".replace(".", ",", 0),
                   ""]
    # situacao hoje do Donchian
    H, C = campos["High"], campos["Close"]
    romp = []
    for k in range(1, 6):
        hi55 = np.nanmax(H[-56 - k:-k], axis=0)
        for a in np.where(H[-k] >= hi55)[0]:
            if tick[a] not in romp:
                romp.append(tick[a])
    linhas.append(f"Romperam a maxima de 55 dias nos ultimos 5 pregoes: {', '.join(romp) if romp else 'nenhuma'}")
    linhas.append(f"SPY acima da MMA 200 hoje: {'sim' if spy_ok[-1] else 'nao'}")
    texto = "\n".join(linhas)
    print(texto)
    open("relatorio_2meses.txt", "w").write(texto)


if __name__ == "__main__":
    main()
