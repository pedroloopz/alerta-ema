"""
Alerta de toque no EMA 20 em candles de 4h nos perps de acoes da Binance.

Fonte: a propria Binance. O preco do perp e o preco que voce opera -
nao o da acao na bolsa, que pode divergir 1% ou mais.

Regra de direcao:
  - preco vinha ACIMA da media e caiu ate encostar   -> LONG
  - preco vinha ABAIXO da media e subiu ate encostar -> SHORT

Regras de envio:
  - descarta sinal contra a inclinacao da media
  - so envia nota >= NOTA_MINIMA
  - nao envia dois sinais seguidos do mesmo setor; o retido volta a ser
    avaliado nas rodadas seguintes, desde que o preco siga colado na media
  - todo sinal avaliado vai para sinais.csv
"""

import csv
import json
import os
import pathlib
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from simbolos import SIMBOLOS, EQUIVALENTES, setor_de, nome_curto

# ----------------------------------------------------------------------
BASE_KLINES = os.environ.get("BINANCE_KLINES",
                             "https://fapi.binance.com/fapi/v1/klines")

PERIODO_EMA = 20
PERIODO_ATR = 20
PERIODO_VOLUME = 20
CANDLES_INCLINACAO = 5
CANDLES_PARA_TRAS = 6
CANDLES_BAIXADOS = 200

# O stop e ancorado no ATR DIARIO, nao no de 4h.
# Motivo: a posicao fica aberta 2 a 3 dias, entao o stop precisa
# sobreviver ao ruido de um dia inteiro. Como um dia tem 6 candles
# de 4h e a amplitude cresce com a raiz do tempo, o ATR diario e
# sempre 2 a 3 vezes o de 4h - usar o de 4h deixava o stop dentro
# da oscilacao normal de um unico dia.
MULT_SL = 0.8               # stop = 0,8 x ATR diario
SL_MINIMO = 1.5
SL_MAXIMO = 8.0

BANCA = 8000.0
VALOR_OPERACAO = 1000.0
ALAVANCAGEM = 2.0
RISCO_ALVO_PCT = 1.0

# Custo estimado de ida e volta, em % do valor operado.
# Calibre com as suas execucoes reais: veja o preco que voce pediu
# contra o preco que saiu, e ajuste a tabela.
TAXA_POR_LADO = 0.05
SLIPPAGE_POR_LIQUIDEZ = [      # (volume 24h minimo, slippage ida+volta %)
    (1_000_000.0, 0.10),
    (200_000.0,   0.25),
    (50_000.0,    0.50),
    (20_000.0,    1.00),
    (0.0,         2.00),
]

LIQ_OTIMA = 1_000_000.0    # volume de 24h em dolar
LIQ_BOA = 200_000.0
LIQ_MINIMA = 50_000.0
LIQ_CRITICA = 20_000.0     # abaixo disso, aviso na mensagem

DIAS_LIMITE = 3            # nao andou nesse prazo, encerra
NOTA_MINIMA = 7.0
MAX_DIST_ENTRADA_ATR = 0.5
PAUSA_ENTRE_CHAMADAS = 0.12
# ----------------------------------------------------------------------

ARQUIVO_ESTADO = pathlib.Path("estado.json")
ARQUIVO_CSV = pathlib.Path("sinais.csv")
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

COLUNAS_CSV = [
    "horario_utc", "simbolo", "setor", "direcao", "nota", "enviado",
    "motivo_nao_envio", "entrada", "ema", "dist_ema_pct", "atr_4h_pct",
    "atr_diario_pct", "sl_pct", "tp_pct", "preco_sl", "preco_tp",
    "risco_usd", "risco_banca_pct", "inclinacao_pct", "vol_relativo",
    "candles_no_lado", "liquidez_24h_usd", "custo_estimado_pct",
    "rejeicao", "diario_concorda", "perda_real_pct", "ganho_real_pct", "candle",
]


def num(valor: float, casas: int = 2) -> str:
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def avisar(texto: str) -> None:
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": texto},
        timeout=20,
    ).raise_for_status()


def baixar_candles(simbolo: str, intervalo: str, limite: int) -> pd.DataFrame:
    """Klines da Binance. Ja vem no intervalo pedido - nao precisa montar."""
    r = requests.get(
        BASE_KLINES,
        params={"symbol": simbolo, "interval": intervalo, "limit": limite},
        timeout=30,
    )
    if r.status_code == 451:
        raise RuntimeError("HTTP 451: bloqueio geografico neste IP")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")

    bruto = r.json()
    if not bruto:
        raise RuntimeError("resposta vazia")

    df = pd.DataFrame(bruto, columns=[
        "abertura_ms", "Open", "High", "Low", "Close", "Volume",
        "fechamento_ms", "quote", "trades", "tb_base", "tb_quote", "ignorar"])
    for col in ("Open", "High", "Low", "Close", "Volume", "quote"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Inicio"] = pd.to_datetime(df["abertura_ms"], unit="ms", utc=True)
    df = df.rename(columns={"quote": "VolumeUSD"})
    return df[["Inicio", "Open", "High", "Low", "Close", "Volume", "VolumeUSD"]].dropna()


def atr_percentual(df: pd.DataFrame, periodo: int = PERIODO_ATR) -> float:
    anterior = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"],
                    (df["High"] - anterior).abs(),
                    (df["Low"] - anterior).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / periodo, adjust=False).mean()
    return float(atr.iloc[-1] / df["Close"].iloc[-1] * 100)


def limitar(valor: float) -> float:
    return max(SL_MINIMO, min(SL_MAXIMO, valor))


def custo_ida_volta(liq_24h: float) -> float:
    """Taxa dos dois lados + slippage estimado pela liquidez, em %."""
    for minimo, slip in SLIPPAGE_POR_LIQUIDEZ:
        if liq_24h >= minimo:
            return 2 * TAXA_POR_LADO + slip
    return 2 * TAXA_POR_LADO + SLIPPAGE_POR_LIQUIDEZ[-1][1]


def tp_com_custo(sl_pct: float, custo: float) -> float:
    """
    TP que entrega 2:1 LIQUIDO.
    Ganho real = tp - custo ; perda real = sl + custo.
    Impondo (tp - custo) = 2*(sl + custo)  ->  tp = 2*sl + 3*custo
    """
    return 2 * sl_pct + 3 * custo


def forca_rejeicao(alta: float, baixa: float, fechamento: float, direcao: str) -> float:
    """Quanto do candle foi rejeitado: 1,0 = fechou na ponta oposta ao pavio."""
    faixa = alta - baixa
    if faixa <= 0:
        return 0.0
    if direcao == "LONG":
        return (fechamento - baixa) / faixa
    return (alta - fechamento) / faixa


def lado_antes_do_toque(candles: pd.DataFrame, atual: int):
    for i in range(atual - 1, max(atual - CANDLES_PARA_TRAS, -1), -1):
        linha = candles.iloc[i]
        if linha["Low"] > linha["EMA"]:
            return "acima"
        if linha["High"] < linha["EMA"]:
            return "abaixo"
    return None


def candles_no_lado(candles: pd.DataFrame, atual: int, lado: str) -> int:
    conta = 0
    for i in range(atual - 1, -1, -1):
        linha = candles.iloc[i]
        if lado == "acima" and linha["Low"] > linha["EMA"]:
            conta += 1
        elif lado == "abaixo" and linha["High"] < linha["EMA"]:
            conta += 1
        else:
            break
    return conta


def calcular_nota(sl_pct, tp_pct, atr_diario, inclinacao, distancia,
                  atr_4h, vol_rel, no_lado, liq_24h, rejeicao, dia_concorda):
    """
    Nota de 0 a 10.
    Pesos: tendencia 2,0 | tendencia maior 1,5 | volatilidade 2,5 |
           toque 1,0 | rejeicao 1,5 | extensao 0,5 | volume 0,5 | liquidez 0,5
    """
    partes = {}
    partes["tendencia"] = min(2.0, abs(inclinacao) / 1.5 * 2.0)
    partes["tendencia_maior"] = 1.5 if dia_concorda else 0.0

    compat = 0.0
    if SL_MINIMO < sl_pct < SL_MAXIMO:
        compat += 0.8          # stop nao saturou no piso nem no teto
    if sl_pct >= 0.75 * atr_diario:
        compat += 0.9          # stop cobre a maior parte do ruido de um dia
    if tp_pct <= 3 * atr_diario:
        compat += 0.8          # alvo alcancavel em ~3 dias
    partes["volatilidade"] = compat

    prox = abs(distancia) / atr_4h if atr_4h > 0 else 99
    partes["toque"] = 1.0 if prox <= 0.3 else 0.5 if prox <= 0.6 else 0.0

    if rejeicao >= 0.66:
        partes["rejeicao"] = 1.5
    elif rejeicao >= 0.50:
        partes["rejeicao"] = 1.0
    elif rejeicao >= 0.33:
        partes["rejeicao"] = 0.5
    else:
        partes["rejeicao"] = 0.0

    partes["extensao"] = 0.5 if no_lado >= 6 else 0.25 if no_lado >= 3 else 0.0

    if vol_rel is None:
        partes["volume"] = 0.25
    elif vol_rel <= 0.8:
        partes["volume"] = 0.5
    elif vol_rel <= 1.2:
        partes["volume"] = 0.35
    else:
        partes["volume"] = 0.15

    if liq_24h >= LIQ_OTIMA:
        partes["liquidez"] = 0.5
    elif liq_24h >= LIQ_BOA:
        partes["liquidez"] = 0.35
    elif liq_24h >= LIQ_MINIMA:
        partes["liquidez"] = 0.2
    else:
        partes["liquidez"] = 0.0

    return round(sum(partes.values()), 1), partes


def registrar(linha: dict) -> None:
    novo = not ARQUIVO_CSV.exists()
    with ARQUIVO_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS_CSV)
        if novo:
            w.writeheader()
        w.writerow({k: linha.get(k, "") for k in COLUNAS_CSV})


def carregar_estado() -> dict:
    if ARQUIVO_ESTADO.exists():
        return json.loads(ARQUIVO_ESTADO.read_text())
    return {"ativos": {}, "ultimo_setor": None, "ultima_empresa": None,
            "dia_do_ultimo_setor": None, "retidos": []}


def main() -> None:
    estado = carregar_estado()
    por_ativo = estado.setdefault("ativos", {})
    candidatos, falhas = [], []

    for simbolo in SIMBOLOS:
        curto = nome_curto(simbolo)
        try:
            candles = baixar_candles(simbolo, "4h", CANDLES_BAIXADOS)
            diario = baixar_candles(simbolo, "1d", 120)
            time.sleep(PAUSA_ENTRE_CHAMADAS)
        except Exception as e:
            falhas.append(f"{simbolo}: {e}")
            continue

        if len(candles) < PERIODO_EMA + CANDLES_INCLINACAO + 1 or len(diario) < PERIODO_ATR + 1:
            falhas.append(f"{simbolo}: historico curto")
            continue

        candles["EMA"] = candles["Close"].ewm(span=PERIODO_EMA, adjust=False).mean()
        i = len(candles) - 1
        atual = candles.iloc[i]
        carimbo = atual["Inicio"].isoformat()

        tocou = atual["Low"] <= atual["EMA"] <= atual["High"]
        distancia = (atual["Close"] / atual["EMA"] - 1) * 100
        print(f"{curto}: EMA={atual['EMA']:.4f} dist={distancia:+.2f}% tocou={tocou}")

        if not tocou or por_ativo.get(simbolo) == carimbo:
            continue

        lado = lado_antes_do_toque(candles, i)
        if lado is None:
            continue

        inclinacao = (atual["EMA"] / candles.iloc[i - CANDLES_INCLINACAO]["EMA"] - 1) * 100
        direcao = "LONG" if lado == "acima" else "SHORT"

        if (direcao == "LONG" and inclinacao < 0) or (direcao == "SHORT" and inclinacao > 0):
            registrar({"horario_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "simbolo": simbolo, "setor": setor_de(simbolo), "direcao": direcao,
                       "enviado": "nao", "motivo_nao_envio": "contra a inclinacao da media",
                       "entrada": round(float(atual["Close"]), 6),
                       "inclinacao_pct": round(inclinacao, 2), "candle": carimbo})
            continue

        # Confirmacao de rejeicao: o candle tem de estar de volta
        # no lado de onde o preco veio. Se fechou atravessado, foi
        # rompimento, nao toque.
        if (direcao == "LONG" and atual["Close"] < atual["EMA"]) or \
           (direcao == "SHORT" and atual["Close"] > atual["EMA"]):
            registrar({"horario_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "simbolo": simbolo, "setor": setor_de(simbolo), "direcao": direcao,
                       "enviado": "nao",
                       "motivo_nao_envio": "sem rejeicao: fechou do outro lado da media",
                       "entrada": round(float(atual["Close"]), 6), "candle": carimbo})
            continue

        rejeicao = forca_rejeicao(float(atual["High"]), float(atual["Low"]),
                                  float(atual["Close"]), direcao)

        # Filtro de tendencia maior: EMA 20 do grafico diario
        diario = diario.copy()
        diario["EMA"] = diario["Close"].ewm(span=PERIODO_EMA, adjust=False).mean()
        dia_acima = bool(diario["Close"].iloc[-1] > diario["EMA"].iloc[-1])
        dia_concorda = dia_acima if direcao == "LONG" else not dia_acima

        atr4 = atr_percentual(candles)
        atrd = atr_percentual(diario)
        sl_pct = limitar(MULT_SL * atrd)

        media_vol = candles["Volume"].iloc[-PERIODO_VOLUME - 1:-1].mean()
        vol_rel = float(atual["Volume"] / media_vol) if media_vol > 0 else None

        # Volume financeiro das ultimas 24h = 6 candles de 4h
        liq_24h = float(candles["VolumeUSD"].iloc[-6:].sum())

        # TP ja embute taxa e slippage, para o 2:1 valer LIQUIDO
        custo = custo_ida_volta(liq_24h)
        tp_pct = tp_com_custo(sl_pct, custo)

        no_lado = candles_no_lado(candles, i, lado)
        nota, partes = calcular_nota(sl_pct, tp_pct, atrd, inclinacao,
                                     distancia, atr4, vol_rel, no_lado,
                                     liq_24h, rejeicao, dia_concorda)

        entrada = float(atual["Close"])
        if direcao == "LONG":
            preco_sl, preco_tp = entrada * (1 - sl_pct / 100), entrada * (1 + tp_pct / 100)
        else:
            preco_sl, preco_tp = entrada * (1 + sl_pct / 100), entrada * (1 - tp_pct / 100)

        candidatos.append({
            "simbolo": simbolo, "curto": curto, "setor": setor_de(simbolo),
            "empresa": EQUIVALENTES.get(simbolo, simbolo),
            "direcao": direcao, "nota": nota, "partes": partes,
            "entrada": entrada, "ema": float(atual["EMA"]), "distancia": distancia,
            "atr4": atr4, "atrd": atrd, "sl_pct": sl_pct, "tp_pct": tp_pct,
            "preco_sl": preco_sl, "preco_tp": preco_tp,
            "risco_usd": VALOR_OPERACAO * ALAVANCAGEM * sl_pct / 100,
            "risco_banca": VALOR_OPERACAO * ALAVANCAGEM * sl_pct / 100 / BANCA * 100,
            "tamanho_1pct": (BANCA * RISCO_ALVO_PCT / 100) / (ALAVANCAGEM * sl_pct / 100),
            "inclinacao": inclinacao, "vol_rel": vol_rel, "liq_24h": liq_24h,
            "custo": custo, "rejeicao": rejeicao, "dia_concorda": dia_concorda,
            "perda_real": sl_pct + custo, "ganho_real": tp_pct - custo,
            "no_lado": no_lado, "carimbo": carimbo,
        })

    candidatos.sort(key=lambda c: c["nota"], reverse=True)

    hoje = datetime.now(timezone.utc).date().isoformat()
    if estado.get("dia_do_ultimo_setor") != hoje:
        ultimo_setor = ultima_empresa = None
    else:
        ultimo_setor = estado.get("ultimo_setor")
        ultima_empresa = estado.get("ultima_empresa")

    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    retidos = []

    for c in candidatos:
        afastou = abs(c["distancia"]) > MAX_DIST_ENTRADA_ATR * c["atr4"]
        motivo = ""
        if c["nota"] < NOTA_MINIMA:
            motivo = f"nota {c['nota']} abaixo de {NOTA_MINIMA}"
        elif afastou:
            motivo = "preco ja se afastou da media"
        elif c["setor"] == ultimo_setor:
            motivo = f"retido: setor {c['setor']} repetido - sera reavaliado"
            retidos.append(c["curto"])
        elif c["empresa"] == ultima_empresa:
            motivo = "retido: mesma empresa do sinal anterior - sera reavaliado"
            retidos.append(c["curto"])

        registrar({
            "horario_utc": agora, "simbolo": c["simbolo"], "setor": c["setor"],
            "direcao": c["direcao"], "nota": c["nota"],
            "enviado": "nao" if motivo else "sim", "motivo_nao_envio": motivo,
            "entrada": round(c["entrada"], 6), "ema": round(c["ema"], 6),
            "dist_ema_pct": round(c["distancia"], 2),
            "atr_4h_pct": round(c["atr4"], 2), "atr_diario_pct": round(c["atrd"], 2),
            "sl_pct": round(c["sl_pct"], 2), "tp_pct": round(c["tp_pct"], 2),
            "preco_sl": round(c["preco_sl"], 6), "preco_tp": round(c["preco_tp"], 6),
            "risco_usd": round(c["risco_usd"], 2),
            "risco_banca_pct": round(c["risco_banca"], 2),
            "inclinacao_pct": round(c["inclinacao"], 2),
            "vol_relativo": round(c["vol_rel"], 2) if c["vol_rel"] else "",
            "candles_no_lado": c["no_lado"],
            "liquidez_24h_usd": round(c["liq_24h"], 0),
            "custo_estimado_pct": round(c["custo"], 2),
            "rejeicao": round(c["rejeicao"], 2),
            "diario_concorda": "sim" if c["dia_concorda"] else "nao",
            "perda_real_pct": round(c["perda_real"], 2),
            "ganho_real_pct": round(c["ganho_real"], 2),
            "candle": c["carimbo"],
        })

        if motivo:
            print(f"{c['curto']}: {motivo}")
            continue

        emoji = "\U0001F7E2" if c["direcao"] == "LONG" else "\U0001F534"
        p = c["partes"]
        casas = 2 if c["entrada"] >= 1 else 5
        aviso_liq = ("  \u26A0\uFE0F livro fino"
                     if c["liq_24h"] < LIQ_CRITICA else "")

        # Ordem pronta para digitar na corretora
        nocional = VALOR_OPERACAO * ALAVANCAGEM
        quantidade = nocional / c["entrada"]
        limite = (datetime.fromisoformat(c["carimbo"])
                  + timedelta(days=DIAS_LIMITE)).strftime("%d/%m")

        avisar(
            f"{emoji} {c['direcao']} \u2014 {c['curto']}  |  NOTA {num(c['nota'], 1)}/10\n"
            f"Contrato: {c['simbolo']}  |  Setor: {c['setor']}\n\n"
            f"\u2500\u2500 ORDEM \u2500\u2500\n"
            f"Margem: US$ {num(VALOR_OPERACAO, 0)}  |  "
            f"Alavancagem: {ALAVANCAGEM:.0f}x\n"
            f"Quantidade: {num(quantidade, 3)} (arredonde p/ baixo)\n"
            f"Entrada: {num(c['entrada'], casas)}\n"
            f"SL: {num(c['preco_sl'], casas)}  ({num(c['sl_pct'])}%)\n"
            f"TP: {num(c['preco_tp'], casas)}  ({num(c['tp_pct'])}%)\n"
            f"Encerrar ate {limite} se nao andar\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
            f"Custo estimado ida+volta: {num(c['custo'])}%\n"
            f"Liquido: perde {num(c['perda_real'])}% / ganha {num(c['ganho_real'])}%\n\n"
            f"Risco: US$ {num(c['risco_usd'])} ({num(c['risco_banca'])}% da banca)\n"
            f"Tamanho p/ risco de {num(RISCO_ALVO_PCT, 0)}%: US$ {num(c['tamanho_1pct'], 0)}\n\n"
            f"Nota detalhada:\n"
            f"  tendencia 4h {num(p['tendencia'], 1)}/2\n"
            f"  tendencia diaria {num(p['tendencia_maior'], 1)}/1,5\n"
            f"  volatilidade {num(p['volatilidade'], 1)}/2,5\n"
            f"  rejeicao {num(p['rejeicao'], 1)}/1,5\n"
            f"  toque {num(p['toque'], 1)}/1\n"
            f"  extensao {num(p['extensao'], 2)}/0,5\n"
            f"  volume {num(p['volume'], 2)}/0,5\n"
            f"  liquidez {num(p['liquidez'], 2)}/0,5\n\n"
            f"EMA: {num(c['ema'], casas)} | ATR 4h {num(c['atr4'])}% "
            f"| ATR diario {num(c['atrd'])}%\n"
            f"Inclinacao: {num(c['inclinacao'])}%\n"
            f"Volume 24h: US$ {num(c['liq_24h'], 0)}{aviso_liq}\n"
            f"Candle: {c['carimbo']}"
        )

        por_ativo[c["simbolo"]] = c["carimbo"]
        ultimo_setor, ultima_empresa = c["setor"], c["empresa"]

    estado.update({"ultimo_setor": ultimo_setor, "ultima_empresa": ultima_empresa,
                   "dia_do_ultimo_setor": hoje, "retidos": retidos})
    ARQUIVO_ESTADO.write_text(json.dumps(estado, indent=2))

    if retidos:
        print("Retidos para a proxima rodada:", ", ".join(retidos))
    if falhas:
        print(f"\n{len(falhas)} simbolos falharam:")
        for f in falhas[:20]:
            print("  ", f)


if __name__ == "__main__":
    main()
