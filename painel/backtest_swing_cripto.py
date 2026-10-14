"""EXA long em swing nas criptos (perps da BingX do top 300) e carteira combinada ações + cripto.

Mesma regra e mesma simulação de backtest_swing.py, em candles de 1h 24/7 da Binance spot
(1 dia de trade = 24 candles; funding de 1 dia corrido por dia). A configuração da cripto é
escolhida só no treino (primeiros 75%); a das ações é a escolhida pelo backtest_swing.py.

Carteira combinada: as duas classes dividem o mesmo limite de exposição, no período em que
as duas têm dados; "teste" = depois do maior dos dois cortes (nenhuma escolha viu esse trecho).

Uso (GitHub Actions, workflow painel-swing): precisa da branch dados em dados/ (ações).
Grava relatorio_swing_cripto.txt.
"""
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import backtest_swing as bs  # noqa: E402
import dados  # noqa: E402

DIAS_HIST = 1000
SAIDA = os.path.join(os.path.dirname(__file__), "..", "relatorio_swing_cripto.txt")
CENARIOS = (  # nome, exposição máxima, nocional por trade
    ("Exposição 29 mil (6 posições de ~4.833)", 29_000, 29_000 / 6),
    ("Exposição 45 mil (9 posições de 5 mil)", 45_000, 5_000),
)


def klines_1h(par):
    linhas, ini = [], int(time.time() * 1000) - DIAS_HIST * 86_400_000
    while True:
        d = dados._get(dados.VISION, {"symbol": par, "interval": "1h", "startTime": ini, "limit": 1000}, tentativas=3)
        if not d:
            break
        linhas += d
        ini = d[-1][0] + 3_600_000
        if len(d) < 1000:
            break
    if not linhas:
        return None
    df = pd.DataFrame([r[:6] for r in linhas], columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"].astype("int64") + 3_600_000, unit="ms", utc=True)
    df = df.astype({k: float for k in ("open", "high", "low", "close", "volume")})
    df = df.drop_duplicates("time").set_index("time").sort_index()
    return df[df.index <= pd.Timestamp.now(tz="UTC")]


def baixar(ativo):
    try:
        df = klines_1h(ativo["nome"] + "USDT")
        if df is not None and len(df) > 24 * 120:
            return ativo["nome"], df
    except Exception as e:
        print(f"falhou {ativo['nome']}: {e}", flush=True)
    return None


def main():
    uni = [a for a in dados.universo() if a["classe"] == "cripto" and a["nome"] not in ("USDT", "USDC", "FDUSD", "DAI")]
    base = dict(x for x in dados.em_paralelo(baixar, uni, n=6) if x)
    print(f"cripto: {len(base)} ativos com histórico", flush=True)

    # ---------------- cripto
    bs.POR_DIA, bs.FATOR_FUND = 24, 1.0
    prep = {s: bs.preparar(df) for s, df in base.items()}
    t0 = min(df.index.min() for df in base.values())
    t1 = max(df.index.max() for df in base.values())
    corte_c = t0 + (t1 - t0) * 0.75
    tc = bs.trades(base, prep)
    gc = bs.tabela(tc, corte_c)
    melhor_c = gc.index[0]
    cfg_acao = (3.0, 3.0, 3, False)
    L = [f"EXA long swing (1h, 24h por dia) — cripto, {len(base)} ativos, {t0:%d/%m/%Y} a {t1:%d/%m/%Y}",
         f"Escolha da configuração até {corte_c:%d/%m/%Y}; teste (não visto) depois disso.",
         "Retorno médio por trade sobre o nocional, já com taxas, slippage e funding. Sem alavancagem.", "",
         "10 melhores configurações no treino:"] + bs.linhas_tabela(gc)
    if cfg_acao in gc.index:
        x = gc.loc[cfg_acao]
        L += ["", f"Configuração das ações (stop 3 ATR, alvo 3R, 3 dias, sem parcial) na cripto: "
                  f"treino {x['acerto_treino']*100:.0f}% / {x['ret_treino']*100:+.2f}%, "
                  f"teste {x['acerto_teste']*100:.0f}% / {x['ret_teste']*100:+.2f}%"]
    L += ["", f"Configuração escolhida para cripto: stop {melhor_c[0]} ATR, alvo {melhor_c[1]} R, "
              f"{melhor_c[2]} dia(s), parcial {'sim' if melhor_c[3] else 'não'}"]
    L += bs.sorteado(base, prep, melhor_c, corte_c)
    mc = bs.da_config(tc, melhor_c)
    for periodo, mm in (("período todo", mc), ("só o teste", mc[mc["ent"] >= corte_c])):
        L += bs.carteira(mm, "só cripto, exposição 29 mil", periodo, 29_000, 29_000 / 6)

    # ---------------- ações (mesma conta do backtest_swing.py) e carteira combinada
    bs.POR_DIA, bs.FATOR_FUND = 7, 1.4
    ba = bs.carregar()
    pa = {s: bs.preparar(df) for s, df in ba.items()}
    a0 = min(df.index.min() for df in ba.values())
    a1 = max(df.index.max() for df in ba.values())
    corte_a = a0 + (a1 - a0) * 0.75
    ma = bs.da_config(bs.trades(ba, pa, grade=[cfg_acao]), cfg_acao)
    for col in ("ent", "sai"):        # cache das ações: horário de Brasília sem fuso
        ma[col] = ma[col].dt.tz_localize("America/Sao_Paulo").dt.tz_convert("UTC")
    corte_a = pd.Timestamp(corte_a).tz_localize("America/Sao_Paulo").tz_convert("UTC")
    ini = max(ma["ent"].min(), mc["ent"].min())
    junto = pd.concat([ma.assign(classe="ação"), mc.assign(classe="cripto")])
    junto = junto[junto["ent"] >= ini].sort_values("ent")
    corte = max(corte_a, corte_c)
    L += ["", f"===== Carteira combinada (ações + cripto), {ini:%d/%m/%Y} a {junto['sai'].max():%d/%m/%Y}; "
              f"teste depois de {corte:%d/%m/%Y} ====="]
    for nome, expo, noc in CENARIOS:
        for periodo, mm in (("período todo", junto), ("só o teste", junto[junto["ent"] >= corte])):
            L += bs.carteira(mm, nome, periodo, expo, noc)
    te = junto[junto["ent"] >= corte]
    for k, x in te.groupby("classe"):
        L.append(f"  sinais no teste, {k}: {len(x)}, acerto {x['lucro'].mean()*100:.0f}%, ret médio {x['ret'].mean()*100:+.2f}%")
    L += ["", "Queda medida nos trades fechados: no meio do trade ela pode ser maior.",
          "Juros do consignado ~2,25% da banca ao mês: é o mínimo para empatar.",
          "Backtest não garante resultado futuro."]
    texto = "\n".join(L)
    print(texto)
    with open(SAIDA, "w", encoding="utf-8") as fh:
        fh.write(texto + "\n")


if __name__ == "__main__":
    main()
