"""Rotacao por forca relativa (M3, FASE 3) - MODO SIMULACAO.

Escolhido pelo Pedro em 11/10/2026 depois do backtest (backtest_fase3.py, relatorio_fase3*.txt).
Roda depois do fechamento de NY, junto com o fechamento do bot, sem tocar na carteira real:
  - a cada 21 pregoes: as 7 acoes do universo da Ondo com maior alta entre 126 e 21 pregoes
    atras (liquidez de 60 pregoes >= US$ 20 mi); caixa se o SPY fechar abaixo da media de 200;
  - regra dos 10%: posicao que vale 10% acima do valor de entrada vende o excesso -> caixa;
  - ordens executadas (simuladas) na abertura seguinte, com 0,25% de custo por lado.
Mensagens marcadas "SIMULADO", so com a nota (sem criterios), botao Recusar que apaga a
mensagem e silencia o ativo ate o proximo rebalanceamento. Resumo semanal na sexta.
"""
import json
import math
import os
from datetime import date, timedelta

import pandas as pd

ARQ = "estado_rotacao.json"
CAPITAL = 7000.0           # simulado: 7 vagas de US$ 1.000
VAGAS = 7
LOOKBACK, PULA, CICLO = 126, 21, 21
CUSTO = 0.0025             # por lado (taxa + spread estimados do token)
REALIZA = 0.10
LIQ_MIN = 20e6
DIAS_SIMULACAO = 14


def _usd(x):
    return "US$ " + f"{x:,.0f}".replace(",", ".")


def _pct(x):
    return f"{x * 100:+.1f}%".replace(".", ",")


def carregar():
    if os.path.exists(ARQ):
        with open(ARQ, encoding="utf-8") as f:
            return json.load(f)
    return None


def salvar(est):
    with open(ARQ, "w", encoding="utf-8") as f:
        json.dump(est, f, ensure_ascii=False, indent=1)


def novo(hoje):
    return {"modo": "simulado", "inicio": hoje,
            "fim_simulacao": str(date.fromisoformat(hoje) + timedelta(days=DIAS_SIMULACAO)),
            "caixa": CAPITAL, "posicoes": {}, "pendentes": [], "pregoes": 0, "ultimo_dia": None,
            "recusados": {}, "sinais": [], "trades": [], "valores": [], "resumo_em": None,
            "fim_avisado": False}


def valor(est, precos):
    return est["caixa"] + sum(p["n"] * precos.get(t, p["ult"]) for t, p in est["posicoes"].items())


def nota(score, todos):
    """Nota de 0 a 10 (1 casa) pela posicao no ranking de forca relativa (sem expor o criterio)."""
    if not todos:
        return "5,0"
    return f"{10 * sum(1 for s in todos if s <= score) / len(todos):.1f}".replace(".", ",")


def ranking(dados, univ):
    """[(score, ticker)] do universo de acoes, maior primeiro."""
    out = []
    for t, i in univ.items():
        df = dados.get(t)
        if i.get("tipo") != "acao" or df is None or len(df) < LOOKBACK + 1:
            continue
        c = df["Close"]
        liq = float((c * df["Volume"]).iloc[-60:].mean())
        if liq < LIQ_MIN:
            continue
        s = float(c.iloc[-1 - PULA] / c.iloc[-1 - LOOKBACK] - 1)
        if not math.isnan(s):
            out.append((s, t))
    return sorted(out, reverse=True)


def rodar(dados, univ, hoje, enviar, token_de=lambda t: t, gravar=True):
    """Chamado uma vez por pregao, depois do fechamento. enviar(texto, botao, silencioso) -> msg_id.
    gravar=False (MODO=teste): calcula e mostra, mas nao grava o estado."""
    est = carregar() or novo(hoje)
    if est["ultimo_dia"] == hoje:
        return est
    abre = {t: float(df["Open"].iloc[-1]) for t, df in dados.items()
            if df is not None and len(df) and pd.Timestamp(df.index[-1]).normalize() == pd.Timestamp(hoje)}
    fecha = {t: float(df["Close"].iloc[-1]) for t, df in dados.items() if df is not None and len(df)}

    # 1) ordens de ontem: executa na abertura de hoje (vendas antes das compras)
    for o in sorted(est["pendentes"], key=lambda o: o["tipo"] != "venda"):
        t, px = o["t"], abre.get(o["t"])
        if px is None or px <= 0:
            continue
        if o["tipo"] == "venda" and t in est["posicoes"]:
            p = est["posicoes"].pop(t)
            est["caixa"] += p["n"] * px * (1 - CUSTO)
            est["trades"].append({"t": t, "entrada": p["pe"], "saida": px, "data": hoje,
                                  "ret": px * (1 - CUSTO) / (p["pe"] * (1 + CUSTO)) - 1})
        elif o["tipo"] == "compra" and t not in est["posicoes"]:
            v = min(o["valor"], est["caixa"])
            if v > 1:
                n = v / (px * (1 + CUSTO))
                est["caixa"] -= v
                est["posicoes"][t] = {"n": n, "pe": px, "base": n * px, "data": hoje, "ult": px}
    est["pendentes"] = []

    # 2) marca no fechamento e aplica a regra dos 10%
    for t, p in est["posicoes"].items():
        p["ult"] = fecha.get(t, p["ult"])
        v = p["n"] * p["ult"]
        if v >= p["base"] * (1 + REALIZA):
            excesso = v - p["base"]
            p["n"] -= excesso / p["ult"]
            est["caixa"] += excesso * (1 - CUSTO)
            mid = enviar(f"🧪 SIMULADO · ROTACAO EUA\n💰 {t} ({token_de(t)}) subiu {_pct(v / p['base'] - 1)}: "
                         f"realize ~{_usd(excesso)} → reserva", None, True)
            est["sinais"].append({"data": hoje, "t": t, "tipo": "realiza", "preco": p["ult"], "msg": mid})

    # 3) rebalanceamento a cada 21 pregoes (o primeiro dia ja rebalanceia)
    if est["pregoes"] % CICLO == 0:
        spy = dados.get("SPY")
        regime = spy is not None and len(spy) >= 200 and \
            float(spy["Close"].iloc[-1]) > float(spy["Close"].iloc[-200:].mean())
        rk = ranking(dados, univ) if regime else []
        silenciados = {t for t, ate in est["recusados"].items() if ate >= hoje}
        alvo = [t for _, t in rk if t not in silenciados][:VAGAS]
        notas = {t: nota(s, [x for x, _ in rk]) for s, t in rk}
        total = valor(est, fecha)
        for t in [t for t in est["posicoes"] if t not in alvo]:
            mid = enviar(f"🧪 SIMULADO · ROTACAO EUA\n🔴 Venda {t} ({token_de(t)}) na abertura"
                         + ("" if regime else " · mercado em queda: rotacao vai para o caixa"), None, True)
            est["pendentes"].append({"t": t, "tipo": "venda"})
            est["sinais"].append({"data": hoje, "t": t, "tipo": "venda", "preco": fecha.get(t), "msg": mid})
        for t in [t for t in alvo if t not in est["posicoes"]]:
            v = total / VAGAS
            mid = enviar(f"🧪 SIMULADO · ROTACAO EUA\n🟢 Compre {t} ({token_de(t)}) · nota {notas.get(t, "5,0")}/10\n"
                         f"~{_usd(v)} na abertura", ("❌ Recusar", f"rot|{t}"), True)   # simulado: sem som
            est["pendentes"].append({"t": t, "tipo": "compra", "valor": v, "msg": mid})
            est["sinais"].append({"data": hoje, "t": t, "tipo": "compra", "preco": fecha.get(t), "msg": mid})
        est["recusados"] = {t: d for t, d in est["recusados"].items() if d >= hoje}

    est["pregoes"] += 1
    est["ultimo_dia"] = hoje
    v = valor(est, fecha)
    spy_px = fecha.get("SPY")
    est["valores"].append({"data": hoje, "valor": round(v, 2), "spy": spy_px})

    # 4) resumo semanal (sexta) e fim das 2 semanas
    if date.fromisoformat(hoje).weekday() == 4 and est["resumo_em"] != hoje \
            and date.fromisoformat(hoje) - date.fromisoformat(est["inicio"]) >= timedelta(days=3):
        enviar(resumo(est, hoje), None, True)
        est["resumo_em"] = hoje
    if hoje >= est["fim_simulacao"] and not est["fim_avisado"]:
        enviar("🧪 Fim das 2 semanas de simulacao da rotacao.\n" + resumo(est, hoje, semanal=False)
               + "\nA simulacao continua ate voce decidir ligar no real.", None, False)
        est["fim_avisado"] = True
    if gravar:
        salvar(est)
    return est


def resumo(est, hoje, semanal=True):
    ini = date.fromisoformat(hoje) - timedelta(days=7) if semanal else date.fromisoformat(est["inicio"])
    sinais = [s for s in est["sinais"] if s["data"] > str(ini) or not semanal]
    fechados = [x for x in est["trades"] if x["data"] > str(ini) or not semanal]
    abertas = [(t, p["ult"] / p["pe"] - 1) for t, p in est["posicoes"].items()]
    certos = sum(1 for x in fechados if x["ret"] > 0) + sum(1 for _, r in abertas if r > 0)
    vals = est["valores"]
    v0 = vals[0]["valor"] if vals else CAPITAL
    v1 = vals[-1]["valor"] if vals else CAPITAL
    spy0 = next((x["spy"] for x in vals if x.get("spy")), None)
    spy1 = vals[-1].get("spy") if vals else None
    spy = f" | SPY no periodo {_pct(spy1 / spy0 - 1)}" if spy0 and spy1 else ""
    compras = sum(1 for s in sinais if s["tipo"] == "compra")
    vendas = sum(1 for s in sinais if s["tipo"] == "venda")
    real = sum(1 for s in sinais if s["tipo"] == "realiza")
    titulo = "🧪 RESUMO SEMANAL DA ROTACAO (simulado)" if semanal else "Resultado da simulacao"
    return (f"{titulo}\n"
            f"Sinais: {len(sinais)} ({compras} compras, {vendas} vendas, {real} realizacoes)\n"
            f"Deram certo: {certos} de {len(fechados) + len(abertas)} (fechadas no lucro + abertas acima da entrada)\n"
            f"Resultado simulado: {_usd(v1)} ({_pct(v1 / v0 - 1)} desde {est['inicio'][8:10]}/{est['inicio'][5:7]}){spy}")


def recusar(chave, msg_id, apagar):
    """Botao Recusar: tira a compra pendente, apaga a mensagem e silencia o ativo ate o
    proximo rebalanceamento (21 pregoes ~ 30 dias)."""
    est = carregar()
    if not est:
        return
    est["pendentes"] = [o for o in est["pendentes"] if not (o["tipo"] == "compra" and o["t"] == chave)]
    p = est["posicoes"].get(chave)
    if p and p["data"] >= str(date.today() - timedelta(days=3)):   # ja executada: desfaz a compra
        est["caixa"] += p["n"] * p["pe"] * (1 + CUSTO)
        del est["posicoes"][chave]
    est["recusados"][chave] = str(date.today() + timedelta(days=30))
    est["sinais"] = [s for s in est["sinais"] if not (s["tipo"] == "compra" and s["t"] == chave
                                                       and s.get("msg") == msg_id)]
    apagar(msg_id)
    salvar(est)
