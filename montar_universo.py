"""
MONTA universo_ondo.json (~100 acoes + ASTER) a partir da coleta.

Fontes (baixadas pelo workflow coleta, branch dados-backtest):
  - ondo.finance/ondo-stocks: lista oficial de ativos da Ondo (simbolo, ticker,
    tipo, preco do token)
  - CoinGecko coins/list (include_platform): contrato do token na BNB Chain,
    a rede do Binance Wallet
  - CoinGecko categoria bstocks-ecosystem: bStocks da Binance (sufixo B)
  - Binance Wallet/Alpha (lista publica de tokens): multiplicador oficial
  - Yahoo Finance: historico diario, liquidez, setor e industria

Regra de selecao (nesta ordem):
  1. As 7 posicoes atuais (carteira.json), com o token que voce ja tem.
  2. So acoes (nada de ETF), com pelo menos 252 pregoes (1 ano) no Yahoo e
     liquidez media de 60 pregoes >= US$ 50 milhoes/dia.
  3. Setores evitados (saude, consumo basico, materiais, imoveis, utilidades): fora.
  4. Tecnologia (inclui semicondutores) e comunicacao: as 78 melhores pela
     media do ranking de liquidez e do ranking de alta em 6 meses.
     Classes duplicadas da mesma empresa (GOOG x GOOGL) entram uma vez so.
  5. Consumo ciclico, industria, financeiro e energia: as 15 de maior alta em 6 meses.
  6. ASTER (ASTERUSDT, Binance spot).
Token: Ondo (sufixo "on") na BNB Chain; bStocks (sufixo "B") so quando nao ha Ondo.
"""

import json
import os
from datetime import date

import numpy as np
import pandas as pd

COLETA = os.environ.get("COLETA", "/tmp/claude-0/c/coleta")
DADOS = os.environ.get("DADOS", "dados")
LIQ_MIN = 50.0            # US$ milhoes por dia
N_TIER1 = 78
N_TIER2 = 15
DUPLICADAS = {"GOOG"}      # mesma empresa que GOOGL (outra classe de acao)
TIER1 = {"Technology", "Communication Services"}
TIER2 = {"Consumer Cyclical", "Industrials", "Financial Services", "Energy"}
SETOR_PT = {"Technology": "Tecnologia", "Communication Services": "Comunicacao",
            "Consumer Cyclical": "Consumo ciclico", "Industrials": "Industria",
            "Financial Services": "Financeiro", "Energy": "Energia", "Healthcare": "Saude",
            "Basic Materials": "Materiais", "Utilities": "Utilidades",
            "Consumer Defensive": "Consumo basico", "Real Estate": "Imoveis"}


def main():
    cand = {c["ticker"]: c for c in json.load(open("coleta/candidatos.json"))["candidatos"]}
    info = json.load(open(f"{DADOS}/yahoo_info.json"))
    alpha = json.load(open(f"{COLETA}/binance_alpha_tokens.json"))["data"]
    mult_alpha = {t["symbol"]: float(t["rwaInfo"]["multiplier"]) for t in alpha if t.get("rwaInfo")}
    cart = json.load(open("carteira.json", encoding="utf-8"))
    atuais = {t: a["token"] for t, a in cart["ativos"].items()}

    C = pd.read_csv(f"{DADOS}/yahoo_close.csv.gz", index_col=0, parse_dates=True)
    V = pd.read_csv(f"{DADOS}/yahoo_volume.csv.gz", index_col=0, parse_dates=True)
    dias = C["SPY"].dropna().index
    C, V = C.reindex(dias), V.reindex(dias)
    liq = (C * V).iloc[-60:].mean() / 1e6
    hist = C.notna().sum()
    mom = C.iloc[-1] / C.iloc[-127] - 1

    linhas = []
    for t, c in cand.items():
        i = info.get(t, {})
        linhas.append(dict(ticker=t, setor=i.get("sector"), industria=i.get("industry"),
                           tipo_yahoo=i.get("quoteType"), liq=float(liq.get(t, np.nan)),
                           n_hist=int(hist.get(t, 0)), mom=float(mom.get(t, np.nan))))
    df = pd.DataFrame(linhas).set_index("ticker")
    base = df[(df.n_hist >= 252) & (df.liq >= LIQ_MIN) & (df.tipo_yahoo == "EQUITY")]
    base = base[~base.index.isin(atuais) & ~base.index.isin(DUPLICADAS)]

    t1 = base[base.setor.isin(TIER1)].copy()
    t1["score"] = (t1.liq.rank(ascending=False) + t1.mom.rank(ascending=False)) / 2
    t1 = t1.sort_values("score").head(N_TIER1)
    t2 = base[base.setor.isin(TIER2)].sort_values("mom", ascending=False).head(N_TIER2)

    def token_de(t):
        c = cand[t]
        if t in atuais:
            return atuais[t]
        if c["token_ondo"] and c["ondo_bsc"]:
            return c["token_ondo"]
        return c["token_bstocks"]

    ativos = []
    for motivo, lista in (("carteira atual", list(atuais)), ("tecnologia/comunicacao", list(t1.index)),
                          ("alto momentum (outros setores)", list(t2.index))):
        for t in lista:
            r = df.loc[t]
            tok = token_de(t)
            c = cand[t]
            emissor = "Ondo" if tok.endswith("on") else "bStocks (Binance)"
            mult = mult_alpha.get(tok)
            if mult is None and emissor == "Ondo" and c["preco_token"] and not np.isnan(C[t].iloc[-1]):
                mult = round(c["preco_token"] / float(C[t].iloc[-1]), 4)
                fonte_m = "estimado: preco do token (ondo.finance) / fechamento Yahoo 25/09/2026"
            elif mult is not None:
                fonte_m = "Binance Wallet (lista publica de tokens)"
            else:
                mult, fonte_m = 1.0, "bStocks: 1 token = 1 acao (a conferir)"
            ativos.append(dict(
                ticker=t, tipo="acao", token=tok, emissor=emissor,
                token_alternativo=(c["token_bstocks"] if emissor == "Ondo" else
                                   (c["token_ondo"] if c["ondo_bsc"] else None)),
                nome=c["nome"], setor=SETOR_PT.get(r.setor, r.setor) if isinstance(r.setor, str) else "ETF alavancado",
                industria=r.industria if isinstance(r.industria, str) else None, motivo=motivo,
                liquidez_60d_musd=round(r.liq, 1), alta_6m=round(r.mom, 4) if not np.isnan(r.mom) else None,
                pregoes_historico=int(r.n_hist),
                multiplicador=mult, fonte_multiplicador=fonte_m,
                passo_quantidade=None, ordem_minima_usd=None))
    ativos.append(dict(ticker="ASTER", tipo="cripto", token="ASTER", par="ASTERUSDT",
                       emissor="Binance spot", nome="Aster", setor="Cripto", motivo="pedido",
                       multiplicador=1.0, passo_quantidade=0.01, ordem_minima_usd=5.0,
                       fonte_ordem="Binance exchangeInfo (LOT_SIZE stepSize 0,01; NOTIONAL minimo US$ 5), 27/09/2026"))

    saida = {
        "consulta": str(date.today()),
        "fontes": {
            "lista_ondo": "https://ondo.finance/ondo-stocks (lista embutida no site; 452 ativos em 27/09/2026)",
            "rede_bnb": "https://api.coingecko.com/api/v3/coins/list?include_platform=true (450 de 452 com contrato na BNB Chain)",
            "bstocks": "https://api.coingecko.com/api/v3/coins/markets?category=bstocks-ecosystem (72 tokens)",
            "multiplicador": "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list (80 tokens Ondo)",
            "precos_setores": "Yahoo Finance (yfinance), pregao de 25/09/2026",
            "aster": "https://data-api.binance.vision/api/v3/exchangeInfo e /klines",
        },
        "regra": __doc__.split("Regra de selecao")[1].strip(),
        "observacoes": [
            "Disponibilidade no Binance Wallet inferida pelo contrato na BNB Chain: a lista publica do Binance Alpha tem 80 tokens Ondo, mas voce ja tem ABNBon e ENPHon, que nao estao nela.",
            "Ondo: 1 token pode valer mais de 1 acao (dividendos reinvestidos e desdobramentos). Ex.: NFLXon = 10 acoes NFLX. O bot usa o multiplicador para calcular a quantidade de tokens.",
            "passo_quantidade e ordem_minima_usd das acoes: nao encontrados em fonte publica; a confirmar no app.",
        ],
        "ativos": ativos,
    }
    json.dump(saida, open("universo_ondo.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    n = {m: sum(1 for a in ativos if a["motivo"] == m) for m in dict.fromkeys(a["motivo"] for a in ativos)}
    print(len(ativos), n)


if __name__ == "__main__":
    main()
