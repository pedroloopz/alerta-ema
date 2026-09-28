"""
PREVIA DA MANHA - 9h de Brasilia, dias uteis.

O fechamento das 18h15 conta o que ja aconteceu. Esta mensagem conta o que
olhar ANTES de abrir: agenda do dia, como estao os mercados la fora, quem
esta a um passo de um gatilho seu, e quantas vagas voce tem livres.

Nao repete o mapa nem a lista de vencedores: isso e do fechamento.
"""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import bot as nucleo

SP = ZoneInfo("America/Sao_Paulo")
PERTO_GATILHO = 5.0        # % que falta para realizar
PERTO_SAIDA = 3.0          # % que falta para o swing cruzar a EMA (B3)
PERTO_SAIDA_EUA = 3.0      # % acima da minima de 10 dias (saida do Donchian)
DIAS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]

REFERENCIAS = [("ES=F", "Futuros S&P"), ("^N225", "Nikkei"), ("^STOXX50E", "Stoxx 50"),
               ("USDBRL=X", "Dolar"), ("^BVSP", "Ibovespa")]

num, pct = nucleo.num, nucleo.pct
usd = lambda x, c=2: "US$ " + num(x, c)
brl = lambda x, c=2: "R$ " + num(x, c)


def ler(caminho, padrao=None):
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    return padrao if padrao is not None else {}


def resultado_proximo(ticker):
    """Divulgacao de resultado hoje ou amanha."""
    try:
        import yfinance as yf
        datas = yf.Ticker(ticker).get_earnings_dates(limit=8)
        if datas is None or datas.empty:
            return False
        idx = datas.index.tz_localize(None) if datas.index.tz is not None else datas.index
        hoje = pd.Timestamp.now().normalize()
        return bool(((idx >= hoje) & (idx <= hoje + pd.Timedelta(days=1))).any())
    except Exception:
        return False


def main():
    agora = datetime.now(SP)
    cart = ler("carteira.json")
    cart_b3 = ler("carteira_b3.json")
    est_b3 = ler("estado_b3.json", {"swings": {}, "vendas_mes": {}})
    perps = ler("perps.json", {"posicoes": {}, "eventos": {}})

    tokens = [t for t, a in cart.get("ativos", {}).items() if a.get("tipo") != "cripto"]
    acoes_b3 = list(cart_b3.get("ativos", {})) + list(est_b3.get("swings", {}))
    futuros = list(perps.get("posicoes", {}))

    alvo = tokens + futuros + [f"{t}.SA" for t in acoes_b3] + [r[0] for r in REFERENCIAS]
    brutos = nucleo.baixar(alvo, periodo="6mo")
    ind = {t.replace(".SA", ""): nucleo.indicadores(df) for t, df in brutos.items()}

    L = [f"☀️ PREVIA {agora:%d/%m} ({DIAS[agora.weekday()]})", ""]

    # ---------------- agenda
    agenda = []
    for nome, data in perps.get("eventos", {}).items():
        faltam = (pd.Timestamp(data) - pd.Timestamp(str(agora.date()))).days
        if 0 <= faltam <= 30:
            quando = "HOJE" if faltam == 0 else f"em {faltam} dia{'s' if faltam > 1 else ''}"
            agenda.append(f"  {nome} {quando} ({pd.Timestamp(data):%d/%m})")
    com_resultado = [t for t in (tokens + futuros)[:12] if resultado_proximo(t)]
    if com_resultado:
        agenda.append(f"  Resultado hoje ou amanha: {', '.join(com_resultado)}")
    if agenda:
        L += ["📅 AGENDA"] + agenda + [""]

    # ---------------- mercados
    linhas = []
    for t, nome in REFERENCIAS:
        d = ind.get(t)
        if not d:
            continue
        var = d["preco"] / d["ontem"] - 1
        valor = brl(d["preco"], 4) if t == "USDBRL=X" else num(d["preco"], 0)
        linhas.append(f"  {nome}: {valor} ({pct(var)})")
    if linhas:
        L += ["🌍 MERCADOS"] + linhas + [""]

    # ---------------- perto do gatilho
    gatilhos = []
    import donchian as dc
    for t, a in cart.get("ativos", {}).items():
        df = brutos.get(t)
        if a.get("tipo") == "cripto" or df is None or len(df) < 11:
            continue
        saida = dc.nivel_saida(df["Low"].values)
        falta = (ind[t]["preco"] / saida - 1) * 100
        if a.get("saida"):
            gatilhos.append(f"  {t} — VENDER NA ABERTURA (sinal de ontem)")
        elif falta <= PERTO_SAIDA_EUA:
            gatilhos.append(f"  {t} a {num(falta)}% da saida ({usd(saida)})")
    for t, a in cart_b3.get("ativos", {}).items():
        if t in ind:
            valor = a["qtd"] * ind[t]["preco"]
            falta = (a["base"] * 1.10 / valor - 1) * 100
            if 0 < falta <= PERTO_GATILHO:
                gatilhos.append(f"  {t} {brl(valor, 0)} — falta {num(falta)}% para realizar")
    for rotulo, swings in (("B3 ", est_b3.get("swings", {})),):
        for t, s in swings.items():
            if t in ind:
                falta = (ind[t]["ema20"] / ind[t]["preco"] - 1) * 100
                if 0 < falta <= PERTO_SAIDA:
                    gatilhos.append(f"  swing {rotulo}{t} — a {num(falta)}% da EMA 20 (saida)")
    if gatilhos:
        L += ["🎯 PERTO DO GATILHO"] + gatilhos + [""]

    # ---------------- futuros
    if futuros:
        try:
            import perps as mod
            linhas = []
            for t, pos in perps["posicoes"].items():
                if t in ind:
                    s = mod.situacao(pos, ind[t]["preco"])
                    linhas.append(f"  {t} {pos['alavancagem']}x: {num(s['roe'])}% da margem | "
                                  f"liquidacao a {num(s['dist'])}%")
            if linhas:
                L += ["📐 FUTUROS"] + linhas + [""]
        except Exception as e:
            print("futuros indisponiveis:", e)

    # ---------------- imposto e vagas
    v = est_b3.get("vendas_mes", {})
    if v.get("acoes") is not None:
        falta = 20000 - v.get("acoes", 0)
        L.append(f"🧾 B3: vendeu {brl(v.get('acoes', 0), 0)} no mes — "
                 + (f"faltam {brl(falta, 0)} para o limite" if falta > 0
                    else "ja passou da isencao"))
    L.append(f"🎣 VAGAS: EUA {len(cart.get('ativos', {}))}/7 (caixa {usd(cart.get('reserva_usdt', 0), 0)}) | "
             f"swing B3 {len(est_b3.get('swings', {}))}/2 | futuros {len(futuros)}/2")

    texto = "\n".join(L)
    print(texto)
    nucleo.enviar(texto)


if __name__ == "__main__":
    main()
