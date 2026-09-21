"""
Backtest da regra no historico da Aster.

Reaproveita as MESMAS funcoes do alerta_ema.py - nota, ATR, custo,
rejeicao - entao o que for testado aqui e exatamente o que roda ao vivo.

Regras da simulacao:
  - entrada no fechamento do candle que encostou na media
  - percorre os candles seguintes ate bater SL, bater TP ou estourar o prazo
  - se SL e TP couberem no MESMO candle, conta como SL (pessimista)
  - custo de ida e volta descontado do resultado
"""

import os
from collections import defaultdict

import pandas as pd

from simbolos import SIMBOLOS, setor_de, nome_curto
from alerta_ema import (
    baixar_candles, atr_percentual, limitar, custo_ida_volta, tp_com_custo,
    forca_rejeicao, calcular_nota, lado_antes_do_toque, candles_no_lado,
    MULT_SL, PERIODO_EMA, PERIODO_ATR, PERIODO_VOLUME, CANDLES_INCLINACAO,
    DIAS_LIMITE, NOTA_MINIMA, MAX_DIST_ENTRADA_ATR, avisar, num,
)

CANDLES_4H = 1500          # teto da API: ~250 dias
CANDLES_DIA = 500
CANDLES_POR_DIA = 6        # 6 candles de 4h em um dia


def simular_saida(candles, inicio, direcao, preco_sl, preco_tp):
    """Percorre os candles seguintes. Devolve (resultado, candles_ate_sair)."""
    limite = inicio + 1 + DIAS_LIMITE * CANDLES_POR_DIA
    for j in range(inicio + 1, min(limite, len(candles))):
        c = candles.iloc[j]
        if direcao == "LONG":
            bateu_sl = c["Low"] <= preco_sl
            bateu_tp = c["High"] >= preco_tp
        else:
            bateu_sl = c["High"] >= preco_sl
            bateu_tp = c["Low"] <= preco_tp
        if bateu_sl:                       # pessimista: SL primeiro
            return "SL", j - inicio
        if bateu_tp:
            return "TP", j - inicio
    return "PRAZO", min(limite, len(candles)) - inicio


def testar(simbolo):
    try:
        c4 = baixar_candles(simbolo, "4h", CANDLES_4H)
        d1 = baixar_candles(simbolo, "1d", CANDLES_DIA)
    except Exception as e:
        print(f"{simbolo}: {e}")
        return []

    minimo = PERIODO_EMA + CANDLES_INCLINACAO + PERIODO_VOLUME + 5
    if len(c4) < minimo + 50 or len(d1) < PERIODO_ATR + 5:
        print(f"{simbolo}: historico curto")
        return []

    c4 = c4.copy()
    c4["EMA"] = c4["Close"].ewm(span=PERIODO_EMA, adjust=False).mean()
    d1 = d1.copy()
    d1["EMA"] = d1["Close"].ewm(span=PERIODO_EMA, adjust=False).mean()
    d1["dia"] = d1["Inicio"].dt.date

    operacoes = []

    for i in range(minimo, len(c4) - 1):
        atual = c4.iloc[i]
        if not (atual["Low"] <= atual["EMA"] <= atual["High"]):
            continue

        janela = c4.iloc[: i + 1]
        lado = lado_antes_do_toque(janela, i)
        if lado is None:
            continue

        direcao = "LONG" if lado == "acima" else "SHORT"

        inclinacao = (atual["EMA"] / c4.iloc[i - CANDLES_INCLINACAO]["EMA"] - 1) * 100
        if (direcao == "LONG" and inclinacao < 0) or \
           (direcao == "SHORT" and inclinacao > 0):
            continue

        if (direcao == "LONG" and atual["Close"] < atual["EMA"]) or \
           (direcao == "SHORT" and atual["Close"] > atual["EMA"]):
            continue

        # Dados diarios ANTERIORES ao candle, para nao espiar o futuro
        dia_atual = atual["Inicio"].date()
        passado = d1[d1["dia"] < dia_atual]
        if len(passado) < PERIODO_ATR + 1:
            continue
        atrd = atr_percentual(passado)
        dia_acima = bool(passado["Close"].iloc[-1] > passado["EMA"].iloc[-1])
        dia_concorda = dia_acima if direcao == "LONG" else not dia_acima

        atr4 = atr_percentual(janela)
        distancia = (atual["Close"] / atual["EMA"] - 1) * 100
        if abs(distancia) > MAX_DIST_ENTRADA_ATR * atr4:
            continue

        sl_pct = limitar(MULT_SL * atrd)
        liq = float(janela["VolumeUSD"].iloc[-6:].sum())
        custo = custo_ida_volta(liq)
        tp_pct = tp_com_custo(sl_pct, custo)

        media_vol = janela["Volume"].iloc[-PERIODO_VOLUME - 1:-1].mean()
        vol_rel = float(atual["Volume"] / media_vol) if media_vol > 0 else None
        rejeicao = forca_rejeicao(float(atual["High"]), float(atual["Low"]),
                                  float(atual["Close"]), direcao)
        no_lado = candles_no_lado(janela, i, lado)

        nota, _ = calcular_nota(sl_pct, tp_pct, atrd, inclinacao, distancia,
                                atr4, vol_rel, no_lado, liq, rejeicao, dia_concorda)

        entrada = float(atual["Close"])
        if direcao == "LONG":
            preco_sl, preco_tp = entrada * (1 - sl_pct / 100), entrada * (1 + tp_pct / 100)
        else:
            preco_sl, preco_tp = entrada * (1 + sl_pct / 100), entrada * (1 - tp_pct / 100)

        saida, duracao = simular_saida(c4, i, direcao, preco_sl, preco_tp)

        if saida == "TP":
            bruto = tp_pct
        elif saida == "SL":
            bruto = -sl_pct
        else:                                   # saiu por prazo, no preco do momento
            fim = min(i + DIAS_LIMITE * CANDLES_POR_DIA, len(c4) - 1)
            var = (c4.iloc[fim]["Close"] / entrada - 1) * 100
            bruto = var if direcao == "LONG" else -var

        operacoes.append({
            "simbolo": simbolo, "setor": setor_de(simbolo), "direcao": direcao,
            "nota": nota, "saida": saida, "duracao_candles": duracao,
            "sl_pct": sl_pct, "tp_pct": tp_pct, "custo": custo, "liq": liq,
            "resultado_pct": bruto - custo,
            "data": atual["Inicio"].isoformat(),
        })

    return operacoes


def faixa(nota):
    if nota >= 9:
        return "9,0 - 10"
    if nota >= 8:
        return "8,0 - 8,9"
    if nota >= 7:
        return "7,0 - 7,9"
    if nota >= 6:
        return "6,0 - 6,9"
    return "abaixo de 6"


def main():
    todas = []
    for s in SIMBOLOS:
        ops = testar(s)
        todas.extend(ops)
        print(f"{nome_curto(s)}: {len(ops)} sinais")

    if not todas:
        print("Nenhum sinal gerado.")
        return

    df = pd.DataFrame(todas)
    df.to_csv("backtest.csv", index=False)

    linhas = []
    linhas.append(f"Total de sinais: {len(df)}")
    per = (df["data"].max()[:10], df["data"].min()[:10])
    linhas.append(f"Periodo: {per[1]} a {per[0]}")
    linhas.append("")
    linhas.append(f"{'faixa':>12s} {'n':>5s} {'acerto':>7s} {'result/op':>10s} {'total':>9s}")

    for f in ["9,0 - 10", "8,0 - 8,9", "7,0 - 7,9", "6,0 - 6,9", "abaixo de 6"]:
        sub = df[df["nota"].apply(faixa) == f]
        if sub.empty:
            continue
        ganhos = (sub["resultado_pct"] > 0).mean() * 100
        media = sub["resultado_pct"].mean()
        linhas.append(f"{f:>12s} {len(sub):5d} {ganhos:6.1f}% {media:9.2f}% "
                      f"{sub['resultado_pct'].sum():8.1f}%")

    validos = df[df["nota"] >= NOTA_MINIMA]
    linhas.append("")
    linhas.append(f"=== Nota >= {NOTA_MINIMA} (o que o robo envia) ===")
    if validos.empty:
        linhas.append("Nenhum sinal passaria do corte.")
    else:
        acerto = (validos["resultado_pct"] > 0).mean() * 100
        linhas.append(f"Operacoes: {len(validos)}")
        linhas.append(f"Taxa de acerto: {acerto:.1f}%")
        linhas.append(f"Resultado medio: {validos['resultado_pct'].mean():.2f}% por operacao")
        linhas.append(f"Em dolar (1000 a 2x): "
                      f"US$ {validos['resultado_pct'].mean() * 20:.2f} por operacao")
        saidas = validos["saida"].value_counts().to_dict()
        linhas.append(f"Saidas: {saidas}")
        dias = len(pd.to_datetime(validos['data']).dt.date.unique())
        linhas.append(f"Media de {len(validos)/max(dias,1):.1f} sinais por dia com sinal")

        linhas.append("")
        linhas.append("Por setor:")
        for setor, sub in validos.groupby("setor"):
            if len(sub) < 5:
                continue
            linhas.append(f"  {setor:16s} n={len(sub):4d} "
                          f"acerto={(sub['resultado_pct']>0).mean()*100:5.1f}% "
                          f"media={sub['resultado_pct'].mean():6.2f}%")

    texto = "\n".join(linhas)
    print("\n" + texto)

    if os.environ.get("TELEGRAM_TOKEN"):
        avisar("RESULTADO DO BACKTEST\n\n" + texto[:3800])


if __name__ == "__main__":
    main()
