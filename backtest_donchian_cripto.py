"""
DONCHIAN EM CRIPTO - a MESMA regra escolhida nas acoes, sem reotimizar:
  entra na maxima de 55 dias, sai na minima de 10 dias, so comprado,
  filtro: BTC acima da MMA 200, tamanho por volatilidade, 5 posicoes, spot 1x.
Dados: Binance spot (diario, 2018+). Custo 0,10% por lado x1,5.
Referencia: comprar e segurar BTC.
Obs.: moedas de hoje (as que morreram ficam de fora -> resultado inflado).
"""

import json

import numpy as np
import pandas as pd
import requests

import backtest_donchian as bd

API = "https://data-api.binance.vision/api/v3/klines"
MOEDAS = ["BTC", "ETH", "BNB", "XRP", "SOL", "ADA", "DOGE", "TRX", "LINK", "AVAX",
          "DOT", "LTC", "BCH", "XLM", "ATOM", "ETC", "FIL", "NEAR", "UNI", "AAVE",
          "ALGO", "HBAR", "VET", "ICP", "INJ", "SUI", "APT", "ARB", "OP", "TON"]
INICIO = pd.Timestamp("2018-01-01")
CORTE = pd.Timestamp("2022-01-01")


def klines(par):
    out, ini = [], int(INICIO.timestamp() * 1000)
    while True:
        r = requests.get(API, params={"symbol": par, "interval": "1d", "startTime": ini, "limit": 1000}, timeout=30)
        if r.status_code != 200:
            return None
        k = r.json()
        if not k:
            break
        out += k
        ini = k[-1][0] + 86_400_000
        if len(k) < 1000:
            break
    if len(out) < 300:
        return None
    df = pd.DataFrame(out).iloc[:, :5].astype(float)
    df.columns = ["t", "Open", "High", "Low", "Close"]
    df.index = pd.to_datetime(df["t"], unit="ms")
    return df.drop(columns="t")


def main():
    capital = json.load(open("carteira.json"))["capital_inicial"]
    dados = {m: d for m in MOEDAS if (d := klines(m + "USDT")) is not None}
    dias = sorted(set().union(*[d.index for d in dados.values()]))
    idx = pd.DatetimeIndex(dias)
    nomes = list(dados)
    campos = {k: np.column_stack([dados[m][k].reindex(idx).to_numpy() for m in nomes])
              for k in ("Open", "High", "Low", "Close")}
    btc = dados["BTC"]["Close"].reindex(idx).ffill().to_numpy()
    btc_ok = btc > pd.Series(btc).rolling(200).mean().to_numpy()

    bd.CUSTO_SPOT = 0.10 / 100
    tr = bd.gerar_trades(campos, 55, 10, 1)
    linhas = ["DONCHIAN EM CRIPTO (regra das acoes, sem reotimizar)",
              f"{len(nomes)} moedas | {idx[200]:%d/%m/%Y} a {idx[-1]:%d/%m/%Y} | validacao a partir de {CORTE:%d/%m/%Y}",
              "custo 0,10%/lado x1,5 | spot 1x | 5 posicoes | so comprado", ""]
    m_all = np.arange(len(idx)) >= 200
    m_tr = m_all & (idx < CORTE)
    m_va = idx >= CORTE
    for rot, filtro, tam in [("com filtro BTC + tamanho por vol (a regra escolhida)", True, "vol"),
                             ("sem filtro, tamanho igual (Donchian puro)", False, "igual")]:
        curva, liqs, quebrou = bd.simular(tr, campos, btc_ok, 5, 1, tam, filtro)
        a, b = bd.metricas(curva, idx, m_tr)
        c, d = bd.metricas(curva, idx, m_va)
        e, f = bd.metricas(curva, idx, m_all)
        linhas += [rot,
                   f"  treino: CAGR {bd.pct(a)} | pior queda {bd.pct(b)}",
                   f"  validacao: CAGR {bd.pct(c)} | pior queda {bd.pct(d)}",
                   f"  todo: CAGR {bd.pct(e)} | pior queda {bd.pct(f)} | {bd.usd(capital)} -> {bd.usd(capital * curva[-1] / curva[200])}", ""]
    s = pd.Series(btc, index=idx)
    for rot, m in [("treino", m_tr), ("validacao", m_va), ("todo", m_all)]:
        x = s[m]
        anos = (x.index[-1] - x.index[0]).days / 365.25
        linhas.append(f"BTC segurar {rot}: CAGR {bd.pct(((x.iloc[-1] / x.iloc[0]) ** (1 / anos) - 1) * 100)} | pior queda {bd.pct(((x / x.cummax()) - 1).min() * 100)}")

    r = pd.DataFrame(tr, columns=["a", "lado", "e", "js", "ent", "sai", "sc", "atr"])
    r["ret"] = (r["sai"] / r["ent"] - 1) * 100 - 0.30
    g, p = r[r.ret > 0].ret, r[r.ret <= 0].ret
    linhas += ["", f"Trades (sem filtro): {len(r)} | acerto {len(g) / len(r) * 100:.0f}% | ganho medio {bd.pct(g.mean())} | perda media {bd.pct(p.mean())} | maior ganho {bd.pct(g.max())}"]
    # mesma estatistica nas acoes, para comparar
    _, _, camp_acoes, _, _ = bd.carregar()
    ra = pd.DataFrame(bd.gerar_trades(camp_acoes, 55, 10, 1), columns=["a", "lado", "e", "js", "ent", "sai", "sc", "atr"])
    ra["ret"] = (ra["sai"] / ra["ent"] - 1) * 100 - 0.60
    ra["dias"] = ra["js"] - ra["e"]
    g, p = ra[ra.ret > 0], ra[ra.ret <= 0]
    top = ra.ret.sort_values(ascending=False)
    linhas += [f"Trades acoes 55/10 (sem filtro): {len(ra)} | acerto {len(g) / len(ra) * 100:.0f}% | ganho medio {bd.pct(g.ret.mean())} em {g.dias.mean():.0f} pregoes | perda media {bd.pct(p.ret.mean())} em {p.dias.mean():.0f} pregoes",
               f"  os 10% melhores trades somam {top.head(len(top) // 10).sum() / ra.ret.sum() * 100:.0f}% do lucro liquido total"]
    texto = "\n".join(linhas)
    print(texto)
    open("relatorio_donchian_cripto.txt", "w").write(texto)


if __name__ == "__main__":
    main()
