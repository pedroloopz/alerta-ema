"""
FUTUROS (PERPS) - vigilancia das posicoes alavancadas.

Roda dentro do bot.py, no mesmo Telegram. Nao manda ordens de compra:
posicao alavancada quem abre e voce. O bot vigia o que pode te machucar.

  A cada hora:  distancia ate a liquidacao, perda da margem, alvo de realizacao
  Sexta-feira:  aviso de gap de fim de semana (o ativo nao negocia, o perp sim)
  Perto de evento: contagem regressiva e o gap que zera a margem
  18h15:        bloco dos futuros no fechamento

Comandos: /perps  /fechei EWZ
"""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import bot as nucleo

SP = ZoneInfo("America/Sao_Paulo")
ARQ, ARQ_ESTADO = "perps.json", "estado_perps.json"

# ================================================================ REGRAS
LIQ_ALERTAS = [15.0, 10.0, 5.0]        # % de distancia ate a liquidacao
ROE_PERDA = [-30.0, -50.0]             # % da margem perdida
ROE_GANHO = [30.0, 60.0]               # % da margem ganha
DIAS_EVENTO = [7, 3, 1, 0]             # contagem regressiva
MAX_POSICOES = 2                       # sempre 2 futuros, nem mais nem menos
MARGEM_PADRAO = 500.0                  # margem sugerida para uma vaga livre
MULT_STOP, MULT_ALVO = 1.5, 3.0        # em ATR de 20 dias
NOTA_MINIMA_VAGA = 7.0                 # nota para ocupar a vaga livre
NOTA_FECHAR = 3.0                      # grafico ruim: sugere liberar a vaga
DIAS_REAVALIAR = 5
HORA_VARREDURA = 19                    # 16h15 Brasilia, como o bot americano
HORA_FECHAMENTO = 21

num, pct = nucleo.num, nucleo.pct
usd = lambda x, c=2: "US$ " + num(x, c)
porc = lambda x: num(x) + "%"


def hoje():
    return str(datetime.now(SP).date())


def carregar():
    with open(ARQ, encoding="utf-8") as f:
        dados = json.load(f)
    est = {"avisados": {}, "fechamento_em": None}
    if os.path.exists(ARQ_ESTADO):
        with open(ARQ_ESTADO, encoding="utf-8") as f:
            est.update(json.load(f))
    return dados, est


def salvar(dados, est):
    with open(ARQ, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)
    with open(ARQ_ESTADO, "w", encoding="utf-8") as f:
        json.dump(est, f, indent=2, ensure_ascii=False, default=str)


def ja_avisou(est, t, chave):
    """Cada aviso sai uma vez por dia para cada posicao."""
    return est["avisados"].get(t, {}).get(chave) == hoje()


def marcar(est, t, chave):
    est["avisados"].setdefault(t, {})[chave] = hoje()


def situacao(pos, preco):
    """Numeros da posicao com o preco atual do ativo."""
    lado = 1 if pos["lado"] == "long" else -1
    pnl = pos["tamanho"] * (preco - pos["entrada"]) * lado
    roe = pnl / pos["margem"] * 100
    dist = (preco - pos["liquidacao"]) / preco * 100 * lado
    gap_zera = 100 / pos["alavancagem"]
    return {"preco": preco, "pnl": pnl, "roe": roe, "dist": dist, "gap_zera": gap_zera,
            "nocional": pos["tamanho"] * preco, "empate": pos["entrada"]}


def linha(t, pos, s):
    seta = "🟢" if s["roe"] >= 0 else "🔴"
    return (f"{seta} {t} {pos['lado']} {pos['alavancagem']}x | {usd(s['preco'])} | "
            f"PnL {usd(s['pnl'])} ({porc(s['roe'])})\n"
            f"   liquidacao {usd(pos['liquidacao'])} — a {porc(s['dist'])} de distancia | "
            f"margem {usd(pos['margem'])}")


def protecao(pos, ind):
    """Stop e alvo sugeridos pelo ATR, conferidos contra a liquidacao."""
    lado = 1 if pos["lado"] == "long" else -1
    stop = pos["entrada"] - lado * MULT_STOP * ind["atr"]
    alvo = pos["entrada"] + lado * MULT_ALVO * ind["atr"]
    perda = (stop - pos["entrada"]) / pos["entrada"] * 100 * lado * pos["alavancagem"]
    ganho = (alvo - pos["entrada"]) / pos["entrada"] * 100 * lado * pos["alavancagem"]
    seguro = (stop > pos["liquidacao"]) if lado == 1 else (stop < pos["liquidacao"])
    return stop, alvo, perda, ganho, seguro


# ================================================================ AVISOS
def vigiar(dados, est, precos, agora, indicadores=None, notas=None):
    avisos = []
    for t, pos in dados["posicoes"].items():
        if t not in precos:
            continue
        s = situacao(pos, precos[t])

        for nivel in LIQ_ALERTAS:
            if s["dist"] <= nivel and not ja_avisou(est, t, f"liq{nivel:.0f}"):
                marcar(est, t, f"liq{nivel:.0f}")
                urgencia = "🚨🚨" if nivel <= 5 else "🚨"
                avisos.append(
                    f"{urgencia} {t} {pos['alavancagem']}x: a liquidacao esta a "
                    f"{porc(s['dist'])} de distancia ({usd(pos['liquidacao'])}).\n"
                    f"   Se bater, voce perde a margem inteira: {usd(pos['margem'])}.\n"
                    f"   Para afastar: reduzir a posicao ou adicionar margem.")
                break

        for nivel in ROE_PERDA:
            if s["roe"] <= nivel and not ja_avisou(est, t, f"perda{abs(nivel):.0f}"):
                marcar(est, t, f"perda{abs(nivel):.0f}")
                avisos.append(f"🛑 {t}: perdeu {porc(abs(s['roe']))} da margem "
                              f"({usd(s['pnl'])}). Empata de volta em {usd(s['empate'])}.")
                break

        for nivel in sorted(ROE_GANHO, reverse=True):
            if s["roe"] >= nivel and not ja_avisou(est, t, f"ganho{nivel:.0f}"):
                marcar(est, t, f"ganho{nivel:.0f}")
                avisos.append(f"🎯 {t}: +{porc(s['roe'])} da margem ({usd(s['pnl'])}). "
                              f"Vale considerar realizar metade e tirar o risco da mesa.")
                break

        # protecao sugerida: uma vez por posicao
        ind = (indicadores or {}).get(t)
        if ind and not ja_avisou(est, t, "sltp"):
            marcar(est, t, "sltp")
            stop, alvo, perda, ganho, seguro = protecao(pos, ind)
            texto = (f"📌 {t}: protecao sugerida pelo ATR\n"
                     f"   STOP em {usd(stop)} → perde {porc(abs(perda))} da margem\n"
                     f"   ALVO em {usd(alvo)} → ganha {porc(ganho)} da margem\n"
                     f"   Coloque pelo botao TP/SL do app, na posicao")
            if not seguro:
                texto += (f"\n   ⚠️ Com {pos['alavancagem']}x, esse stop fica DEPOIS da "
                          f"liquidacao ({usd(pos['liquidacao'])}): a alavancagem nao deixa "
                          f"espaco para stop tecnico. Reduzir a posicao resolve.")
            avisos.append(texto)

        # grafico ruim: liberar a vaga
        nota = (notas or {}).get(t)
        if nota is not None and nota <= NOTA_FECHAR and not ja_avisou(est, t, "fechar"):
            marcar(est, t, "fechar")
            avisos.append(f"📉 {t}: grafico em {num(nota, 0)}/10 e PnL {usd(s['pnl'])}. "
                          f"Vale considerar fechar e liberar a vaga para outro ativo.")

        # sexta-feira: o ativo para, o perp nao
        if agora.weekday() == 4 and not ja_avisou(est, t, "fds"):
            marcar(est, t, "fds")
            avisos.append(f"🌙 {t} {pos['alavancagem']}x no fim de semana: o ativo nao negocia, "
                          f"mas o perp sim. Um gap de {porc(s['gap_zera'])} na segunda zera a "
                          f"margem; a liquidacao esta a {porc(s['dist'])}.")

    # eventos marcados (eleicoes)
    for nome, data in dados.get("eventos", {}).items():
        faltam = (pd.Timestamp(data) - pd.Timestamp(hoje())).days
        if faltam in DIAS_EVENTO and not ja_avisou(est, "evento", f"{nome}{faltam}"):
            marcar(est, "evento", f"{nome}{faltam}")
            quadro = []
            for t, pos in dados["posicoes"].items():
                if t in precos:
                    s = situacao(pos, precos[t])
                    quadro.append(f"   {t} {pos['alavancagem']}x: gap de "
                                  f"{porc(s['gap_zera'])} zera a margem | "
                                  f"liquidacao a {porc(s['dist'])}")
            quando = "HOJE" if faltam == 0 else f"em {faltam} dia{'s' if faltam > 1 else ''}"
            avisos.append(f"📅 {nome} {quando} ({pd.Timestamp(data):%d/%m}).\n" + "\n".join(quadro))
    return avisos


# ============================================================ COMANDOS
def processar_eventos(dados, est, eventos, precos):
    for ev in eventos:
        if ev[0] != "cmd":
            continue
        cmd, msg_id = ev[1], ev[2]
        nome = cmd[0].lower().split("@")[0]
        if nome == "/perps":
            nucleo.enviar(texto_status(dados, precos))
        elif nome == "/fechei" and len(cmd) == 2 and cmd[1].upper() in dados["posicoes"]:
            t = cmd[1].upper()
            pos = dados["posicoes"].pop(t)
            est["avisados"].pop(t, None)
            est["avisados"].pop("vaga", None)
            resultado = ""
            if t in precos:
                s = situacao(pos, precos[t])
                resultado = f" Resultado estimado: {usd(s['pnl'])} ({porc(s['roe'])} da margem)."
            nucleo.enviar(f"✅ Futuro {t} removido do acompanhamento.{resultado}")


def texto_status(dados, precos):
    L = ["📐 FUTUROS AGORA"]
    margem, pnl = 0.0, 0.0
    for t, pos in dados["posicoes"].items():
        if t not in precos:
            L.append(f"  {t}: sem preco")
            continue
        s = situacao(pos, precos[t])
        margem += pos["margem"]
        pnl += s["pnl"]
        L.append(linha(t, pos, s))
        if pos["alavancagem"] > dados.get("alavancagem_padrao", 2):
            L.append(f"   ⚡ acima do seu padrao de {dados['alavancagem_padrao']}x"
                     + (f" — {pos['tese']}" if pos.get("tese") else ""))
    if margem:
        L.append(f"\nMargem total {usd(margem)} | PnL {usd(pnl)} ({porc(pnl / margem * 100)})")
    return "\n".join(L)


def vaga_livre(dados, est, ocupados, agora):
    """Sempre 2 futuros: se sobrar vaga, procura candidato no universo."""
    faltam = MAX_POSICOES - len(dados["posicoes"])
    if faltam < 1 or ja_avisou(est, "vaga", "sugerida"):
        return []
    univ = nucleo.universo()
    brutos = nucleo.baixar(list(univ) + ["SPY"], periodo="1y")
    dados_ind = {t: nucleo.indicadores(df) for t, df in brutos.items()}
    spy_ok = "SPY" in dados_ind and dados_ind["SPY"]["preco"] > dados_ind["SPY"]["sma200"]
    pre = []
    for t, ind in dados_ind.items():
        if t in ocupados or t in ("SPY", "QQQ") or np.isnan(ind["sma200"]) or ind["liq"] < 50e6:
            continue
        esticada = (ind["ema20"] - ind["preco"]) / ind["atr"] if ind["atr"] else 0
        if esticada >= 2.0:
            n, _, _ = nucleo.nota_swing(ind, spy_ok, None)
            pre.append((n, t))
    melhores = sorted(pre, reverse=True)[:5]
    if not melhores or melhores[0][0] < NOTA_MINIMA_VAGA:
        if ja_avisou(est, "vaga", "vazia"):
            return []
        marcar(est, "vaga", "vazia")
        fila = ", ".join(f"{x[1]} {num(x[0], 1)}" for x in melhores[:3]) or "nenhum esticado"
        return [f"🎣 Vaga livre nos futuros ({len(dados['posicoes'])} de {MAX_POSICOES} "
                f"ocupadas): nenhum candidato com nota {num(NOTA_MINIMA_VAGA, 0)}+ hoje.\n"
                f"   Melhores da fila: {fila}"]
    n, t = melhores[0]
    marcar(est, "vaga", "sugerida")
    ind = dados_ind[t]
    entrada = ind["preco"]
    alav = dados.get("alavancagem_padrao", 2)
    fake = {"lado": "long", "entrada": entrada, "alavancagem": alav,
            "liquidacao": entrada * (1 - 1 / alav + 0.005)}
    stop, alvo, perda, ganho, seguro = protecao(fake, ind)
    outros = ", ".join(f"{x[1]} {num(x[0], 1)}" for x in melhores[1:4])
    return [f"🎣 VAGA LIVRE nos futuros ({len(dados['posicoes'])} de {MAX_POSICOES} ocupadas)\n"
            f"   Candidato: {t} | nota {num(n, 1)}/10\n"
            f"   Long {alav}x, margem {usd(MARGEM_PADRAO, 0)} → nocional "
            f"{usd(MARGEM_PADRAO * alav, 0)} (~{num(MARGEM_PADRAO * alav / entrada, 2)} un.)\n"
            f"   Entrada ~{usd(entrada)} | STOP {usd(stop)} | ALVO {usd(alvo)}\n"
            f"   Liquidacao a {alav}x ficaria perto de {usd(fake['liquidacao'])}\n"
            f"   Se entrar, me diga para eu por no perps.json"
            + (f"\n   Proximos da fila: {outros}" if outros else "")]


# ================================================================ RODAR
def rodar(eventos, modo, agora):
    if not os.path.exists(ARQ):
        return
    dados, est = carregar()
    tickers = list(dados["posicoes"])
    if not tickers:
        return
    brutos = nucleo.baixar(tickers + ["SPY"], periodo="1y")
    inds = {t: nucleo.indicadores(df) for t, df in brutos.items()}
    precos = {t: x["preco"] for t, x in inds.items() if t in tickers}
    rs = pd.Series({t: x["ret63"] for t, x in inds.items() if not np.isnan(x["ret63"])})
    perc = rs.rank(pct=True)
    notas = {t: nucleo.nota_tendencia(inds[t], perc.get(t, 0)) for t in tickers
             if t in inds and not np.isnan(inds[t]["sma200"])}

    processar_eventos(dados, est, eventos, precos)

    avisos = vigiar(dados, est, precos, datetime.now(SP), inds, notas)
    if modo in ("tudo", "varredura") or (modo == "auto" and agora.hour >= HORA_VARREDURA
                                         and est.get("varredura_em") != hoje()):
        ocupados = set(dados["posicoes"]) | set(json.load(open("carteira.json"))["ativos"]) \
            if os.path.exists("carteira.json") else set(dados["posicoes"])
        avisos += vaga_livre(dados, est, ocupados, agora)
        est["varredura_em"] = hoje()
    if avisos:
        nucleo.enviar("📐 FUTUROS\n\n" + "\n\n".join(avisos))

    if modo in ("tudo", "fechamento") or (modo == "auto" and agora.hour >= HORA_FECHAMENTO
                                          and est.get("fechamento_em") != hoje()):
        nucleo.enviar(texto_status(dados, precos))
        est["fechamento_em"] = hoje()

    salvar(dados, est)
