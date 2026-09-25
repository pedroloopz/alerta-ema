"""
BOT DA CARTEIRA DO PEDRO

  7 ativos de US$ 1.000 + reserva em USDT + ate 2 swings.

  A cada hora do pregao:   realizacao de +10% e alertas de risco
  19h UTC (16h Brasilia):  varredura do universo -> swings e trocas
  21h UTC (18h Brasilia):  fechamento estilo Finviz

O bot ASSUME que voce executou cada ordem e atualiza a carteira sozinho.
Se nao executou, responda /nao no Telegram e ele desfaz a ultima mensagem.
Comandos EUA: /carteira  /nao  /recusar NVDA  /reserva 1200  /unidades CRWV 12.5
Comandos B3:  /nao_br  /reserva_br 2000  (os demais servem para os dois mercados)
"""

import copy
import io
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
MODO = os.environ.get("MODO", "auto")          # auto | tudo | monitor | varredura | fechamento
ARQ_CARTEIRA, ARQ_ESTADO, ARQ_PLACAR = "carteira.json", "estado_bot.json", "placar_notas.csv"
NY = ZoneInfo("America/New_York")

# ================================================================ REGRAS
REALIZA_ALTA = 0.10          # +10% sobre a base -> excesso vai para a reserva
MAX_SWINGS = 2
ESTICADA_MIN = 2.0           # ATR abaixo da EMA 20 para virar candidato
TAMANHO_SWING = [(8.0, 500.0), (6.0, 250.0)]   # (nota minima, tamanho total)
PRAZO_SWING = 20             # pregoes
DESASTRE = 0.20              # swing caiu 20% desde a entrada -> alerta
TROCA_NOTA_MIN = 7.0
TROCA_MARGEM = 2.0           # candidato precisa ter nota 2 pontos acima
TROCA_CARENCIA = 10          # pregoes minimos com o ativo antes de trocar
TROCAS_POR_SEMANA = 2
REVISAR_ABAIXO = 0.75        # posicao abaixo de 75% da base + grafico ruim
HORA_VARREDURA = 19
HORA_FECHAMENTO = 21
TOL_PRECO = 0.15
TAMANHO_MAPA = 64

SETORES_PT = {
    "Information Technology": "Tecnologia", "Health Care": "Saude",
    "Financials": "Financeiro", "Consumer Discretionary": "Consumo",
    "Communication Services": "Comunicacao", "Industrials": "Industria",
    "Consumer Staples": "Consumo basico", "Energy": "Energia",
    "Utilities": "Utilidades", "Real Estate": "Imobiliario", "Materials": "Materiais",
}
EXTRAS = {
    "Tecnologia": ["CRWV", "IREN", "NBIS", "CRDO", "AAOI", "AXTI", "SNDK", "LITE", "COHR",
                   "ARM", "TSM", "ASML", "PLTR", "APP", "NET", "DDOG", "SNOW", "IONQ",
                   "RGTI", "QBTS", "MVLL"],
    "Energia": ["BE", "ENPH", "OKLO", "NNE", "SMR", "USAR", "MP"],
    "Industria": ["ASTS", "RKLB"],
    "Financeiro": ["HOOD", "COIN", "MSTR", "SOFI", "CRCL"],
    "Consumo": ["BABA", "SHOP", "GME"],
    "Comunicacao": ["RDDT", "SPOT"],
    "Saude": ["HIMS", "NVO"],
}

TOTAL_USD = None            # preenchido no fechamento; a B3 usa no consolidado de sexta

num = lambda x, c=2: f"{x:,.{c}f}".replace(",", "X").replace(".", ",").replace("X", ".")
pct = lambda x: f"{x*100:+.1f}%".replace(".", ",")


# ============================================================== TELEGRAM
def tg(metodo, **dados):
    if not (TOKEN and CHAT_ID):
        return {}
    try:
        return requests.post(f"https://api.telegram.org/bot{TOKEN}/{metodo}",
                             json=dados, timeout=30).json()
    except Exception as e:
        print("telegram falhou:", e)
        return {}


def enviar(texto, botao=None, silencioso=False):
    """Envia e devolve o id da mensagem. botao = (rotulo, dado_do_callback).
    silencioso=True chega sem som (so para o que nao pede acao)."""
    print("\n>>> TELEGRAM" + (" [sem som]" if silencioso else "")
          + (f" [botao {botao[0]}]" if botao else "") + "\n" + texto)
    extra = {"disable_notification": True} if silencioso else {}
    if botao:
        extra["reply_markup"] = {"inline_keyboard": [[{"text": botao[0], "callback_data": botao[1]}]]}
    r = tg("sendMessage", chat_id=CHAT_ID, text=texto[:4000], **extra)
    return r.get("result", {}).get("message_id")


def apagar(msg_id):
    if msg_id:
        print(f">>> TELEGRAM apagar mensagem {msg_id}")
        tg("deleteMessage", chat_id=CHAT_ID, message_id=msg_id)


def enviar_foto(caminho, legenda, silencioso=True):
    print(f"\n>>> TELEGRAM FOTO {caminho}\n{legenda}")
    if TOKEN and CHAT_ID:
        with open(caminho, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                          data={"chat_id": CHAT_ID, "caption": legenda[:1000],
                                "disable_notification": str(silencioso).lower()},
                          files={"photo": f}, timeout=60)


def ler_comandos(estado):
    """Devolve eventos: ('cmd', partes, msg_id) ou ('botao', dado, msg_id)."""
    if not TOKEN:
        return []
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                         params={"offset": estado.get("offset", 0), "timeout": 0},
                         timeout=30).json()
    except Exception:
        return []
    eventos = []
    for u in r.get("result", []):
        estado["offset"] = u["update_id"] + 1
        if "callback_query" in u:
            cq = u["callback_query"]
            if str(cq.get("from", {}).get("id")) == str(CHAT_ID):
                tg("answerCallbackQuery", callback_query_id=cq["id"], text="Recusado")
                eventos.append(("botao", cq.get("data", ""), cq.get("message", {}).get("message_id")))
            continue
        msg = u.get("message", {})
        if str(msg.get("chat", {}).get("id")) == str(CHAT_ID) and msg.get("text", "").startswith("/"):
            eventos.append(("cmd", msg["text"].strip().split(), msg.get("message_id")))
    return eventos


# ================================================================= DADOS
def baixar(tickers, periodo="1y"):
    import yfinance as yf
    tickers = sorted(set(tickers))
    saida = {}
    for i in range(0, len(tickers), 150):
        lote = tickers[i:i + 150]
        try:
            bruto = yf.download(lote, period=periodo, interval="1d", auto_adjust=True,
                                group_by="ticker", progress=False, threads=True)
        except Exception as e:
            print("falha no download:", e)
            continue
        for t in lote:
            try:
                df = bruto[t] if isinstance(bruto.columns, pd.MultiIndex) else bruto
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if getattr(df.index, "tz", None) is not None:
                    df.index = df.index.tz_localize(None)
                if len(df) >= 60:
                    saida[t] = df
            except Exception:
                pass
    return saida


def universo():
    """S&P 500 + Nasdaq-100 (Wikipedia) + seus temas. Devolve {ticker: setor}."""
    setores = {}
    cab = {"User-Agent": "Mozilla/5.0 (bot da carteira)"}
    for url in ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                "https://en.wikipedia.org/wiki/Nasdaq-100"):
        try:
            html = requests.get(url, headers=cab, timeout=30).text
            for tab in pd.read_html(io.StringIO(html)):
                col = next((c for c in tab.columns if str(c) in ("Symbol", "Ticker")), None)
                if col is None or len(tab) < 90:
                    continue
                setor_col = next((c for c in tab.columns if "Sector" in str(c)), None)
                for _, linha in tab.iterrows():
                    t = str(linha[col]).replace(".", "-").strip()
                    s = SETORES_PT.get(str(linha[setor_col]), "Outros") if setor_col else "Outros"
                    setores.setdefault(t, s)
                break
        except Exception as e:
            print("wikipedia indisponivel:", e)
    for setor, lista in EXTRAS.items():
        for t in lista:
            setores.setdefault(t, setor)
    return setores


def indicadores(df):
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ant = c.shift(1)
    tr = pd.concat([h - l, (h - ant).abs(), (l - ant).abs()], axis=1).max(axis=1)
    return {
        "preco": float(c.iloc[-1]), "ontem": float(c.iloc[-2]),
        "abertura": float(df["Open"].iloc[-1]),
        "max": float(h.iloc[-1]), "min": float(l.iloc[-1]), "min_ontem": float(l.iloc[-2]),
        "ema20": float(c.ewm(span=20, adjust=False).mean().iloc[-1]),
        "atr": float(tr.ewm(alpha=1 / 20, adjust=False).mean().iloc[-1]),
        "sma50": float(c.rolling(50).mean().iloc[-1]),
        "sma50_antes": float(c.rolling(50).mean().iloc[-21]) if len(c) > 70 else np.nan,
        "sma200": float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else np.nan,
        "ret63": float(c.iloc[-1] / c.iloc[-64] - 1) if len(c) > 64 else np.nan,
        "max52": float(h.iloc[-252:].max()), "min52": float(l.iloc[-252:].min()),
        "vol_rel": float(v.iloc[-1] / v.iloc[-21:-1].mean()) if v.iloc[-21:-1].mean() > 0 else 1.0,
        "vol_rel3": float(v.iloc[-3:].max() / v.iloc[-23:-3].mean()) if v.iloc[-23:-3].mean() > 0 else 1.0,
        "vol_tend": float(v.iloc[-20:].mean() / v.iloc[-60:].mean()) if v.iloc[-60:].mean() > 0 else 1.0,
        "liq": float((c * v).iloc[-20:].mean()),
        "atr_pct": float(tr.ewm(alpha=1 / 20, adjust=False).mean().iloc[-1] / c.iloc[-1]),
        "data": df.index[-1],
    }


def teve_resultado(ticker, dias=3):
    """True/False se houve divulgacao de resultado nos ultimos dias; None se nao souber."""
    try:
        import yfinance as yf
        datas = yf.Ticker(ticker).get_earnings_dates(limit=8)
        if datas is None or datas.empty:
            return None
        idx = datas.index.tz_localize(None) if datas.index.tz is not None else datas.index
        hoje = pd.Timestamp.now().normalize()
        return bool(((idx <= hoje + pd.Timedelta(days=1)) &
                     (idx >= hoje - pd.Timedelta(days=dias + 2))).any())
    except Exception:
        return None


def proximo_resultado(ticker, pregoes=2):
    """Data do proximo balanco se cair nos proximos `pregoes` dias uteis; senao None."""
    try:
        import yfinance as yf
        datas = yf.Ticker(ticker).get_earnings_dates(limit=8)
        if datas is None or datas.empty:
            return None
        idx = datas.index.tz_localize(None) if datas.index.tz is not None else datas.index
        hoje = pd.Timestamp.now().normalize()
        fim = hoje + pd.offsets.BDay(pregoes)
        futuras = sorted(d.normalize() for d in idx if hoje < d.normalize() <= fim)
        return futuras[0] if futuras else None
    except Exception:
        return None


def aviso_balancos(tickers, sufixo=""):
    """Linhas de aviso para posicoes com balanco nos proximos 2 pregoes."""
    dias = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]
    L = []
    for t in sorted(tickers):
        d = proximo_resultado(f"{t}{sufixo}")
        if d is not None:
            L.append(f"  {t}: {dias[d.weekday()]} {d:%d/%m}")
    return (["", "📅 BALANCO NOS PROXIMOS 2 PREGOES"] + L) if L else []


def em_pausa(est):
    ate = est.get("pausa_ate")
    return bool(ate) and ate >= hoje_ny()


# ================================================================= NOTAS
def nota_swing(ind, spy_ok, resultado):
    """Prioridade de compra na queda (0-10). Ordena candidatos; nao preve lucro."""
    p = {}
    esticada = (ind["ema20"] - ind["preco"]) / ind["atr"] if ind["atr"] else 0
    p["esticada"] = 2.0 if esticada >= 3 else 1.0 if esticada >= ESTICADA_MIN else 0.0
    p["tendencia longa"] = 2.0 if ind["preco"] > ind["sma200"] else 0.0
    p["sem noticia"] = 1.0 if resultado is None else (0.0 if resultado else 2.0)
    fechou_em_cima = (ind["preco"] - ind["min"]) / (ind["max"] - ind["min"]) >= 0.66 \
        if ind["max"] > ind["min"] else False
    p["confirmacao"] = 1.5 if (ind["min"] > ind["min_ontem"] or fechou_em_cima) else 0.0
    p["capitulacao"] = 1.0 if ind["vol_rel3"] >= 2 else 0.0
    p["tese viva"] = 1.0 if spy_ok else 0.0
    p["liquidez"] = 0.5 if ind["liq"] >= 50e6 else 0.25 if ind["liq"] >= 10e6 else 0.0
    return round(sum(p.values()), 1), p, esticada


def nota_tendencia(ind, percentil_rs):
    """Qualidade do grafico para troca (0-10)."""
    n = 0.0
    n += 3 if percentil_rs >= 0.90 else 2 if percentil_rs >= 0.75 else 1 if percentil_rs >= 0.60 else 0
    if ind["preco"] > ind["sma50"] > ind["sma200"]:
        n += 2
    elif ind["preco"] > ind["sma200"]:
        n += 1
    n += 2 if ind["preco"] >= 0.9 * ind["max52"] else 1 if ind["preco"] >= 0.8 * ind["max52"] else 0
    n += 1 if ind["sma50"] > ind["sma50_antes"] else 0
    n += 1 if ind["vol_tend"] >= 1.2 else 0
    n += 1 if ind["liq"] >= 50e6 else 0
    return float(n)


def tamanho_por_nota(nota):
    for minimo, valor in TAMANHO_SWING:
        if nota >= minimo:
            return valor
    return 0.0


# ================================================================ ESTADO
def carregar():
    with open(ARQ_CARTEIRA, encoding="utf-8") as f:
        cart = json.load(f)
    est = {"swings": {}, "offset": 0, "ultima": None, "validado": False,
           "revisar": {}, "trocas": [], "desastre": {}, "pendentes": {}, "recusados": {}}
    if os.path.exists(ARQ_ESTADO):
        with open(ARQ_ESTADO, encoding="utf-8") as f:
            est.update(json.load(f))
    return cart, est


def salvar(cart, est):
    with open(ARQ_CARTEIRA, "w", encoding="utf-8") as f:
        json.dump(cart, f, indent=2, ensure_ascii=False)
    with open(ARQ_ESTADO, "w", encoding="utf-8") as f:
        json.dump(est, f, indent=2, ensure_ascii=False, default=str)


def hoje_ny():
    return str(datetime.now(NY).date())


def silenciado(est, chave):
    """Ordem desfeita com /nao nao se repete no mesmo dia."""
    sil = est.get("silencio", {})
    return sil.get("data") == hoje_ny() and chave in sil.get("chaves", [])


def pregoes_desde(data_str, pregoes):
    """Quantos pregoes aconteceram depois da data (calendario real do SPY)."""
    if pregoes is None:
        return 0
    return int((pregoes > pd.Timestamp(data_str)).sum())


def valor_total(cart, est, precos):
    ativos = sum(a["unidades"] * precos.get(t, 0) for t, a in cart["ativos"].items())
    swings = sum(s["unidades"] * precos.get(t, s["preco_medio"]) for t, s in est["swings"].items())
    return ativos, swings, cart["reserva_usdt"]


def registrar_placar(linhas):
    novo = not os.path.exists(ARQ_PLACAR)
    pd.DataFrame(linhas).to_csv(ARQ_PLACAR, mode="a", header=novo, index=False)


# ============================================================ COMANDOS
DIAS_SILENCIO_RECUSA = 5


def recusar(cart, est, chave):
    """Desfaz UMA entrada sugerida, apaga a mensagem e silencia o ativo."""
    pend = est.get("pendentes", {}).pop(chave, None)
    if not pend:
        return False
    tipo, t = pend["tipo"], pend.get("ticker")
    ate = str((pd.Timestamp(hoje_ny()) + pd.Timedelta(days=DIAS_SILENCIO_RECUSA)).date())
    if tipo == "swing" and t in est["swings"]:
        s = est["swings"].pop(t)
        cart["reserva_usdt"] += s["meia"] * s["metades"]
        pend_metade = est["pendentes"].pop(f"metade:{t}", None)
        if pend_metade:
            apagar(pend_metade.get("msg_id"))
        est.setdefault("recusados", {})[t] = ate
    elif tipo == "metade" and t in est["swings"] and est["swings"][t]["metades"] == 2:
        s = est["swings"][t]
        s["unidades"] -= pend["un"]
        s["preco_medio"] = pend["pm_antes"]
        s["metades"] = 1
        cart["reserva_usdt"] += pend["meia"]
        sil = est.setdefault("silencio", {"data": hoje_ny(), "chaves": []})
        if sil.get("data") != hoje_ny():
            est["silencio"] = sil = {"data": hoje_ny(), "chaves": []}
        sil["chaves"].append(chave)
    elif tipo == "troca":
        cart["ativos"].pop(pend["novo"], None)
        cart["ativos"][pend["velho"]] = pend["antigo"]
        est.setdefault("recusados", {})[pend["novo"]] = ate
    apagar(pend.get("msg_id"))
    return True


def recusado(est, ticker):
    ate = est.get("recusados", {}).get(ticker)
    return bool(ate) and ate >= hoje_ny()


def processar_comandos(cart, est, precos, eventos):
    """Trata o que e dos EUA e devolve o resto para a B3."""
    respostas, restantes = [], []
    for evento in eventos:
        if evento[0] == "botao":
            if evento[1].startswith("rec|"):
                recusar(cart, est, evento[1][4:])
            else:
                restantes.append(evento)
            continue
        cmd, msg_id = evento[1], evento[2]
        nome = cmd[0].lower().split("@")[0]
        if nome == "/recusar" and len(cmd) == 2:
            alvo = cmd[1].upper()
            chaves = [k for k, v in est.get("pendentes", {}).items()
                      if alvo in (v.get("ticker"), v.get("novo"))]
            if chaves:
                for k in chaves:
                    recusar(cart, est, k)
                apagar(msg_id)
            else:
                restantes.append(evento)
        elif nome == "/nao" and est.get("ultima"):
            antes = est["ultima"]
            cart.clear(); cart.update(antes["carteira"])
            est["swings"] = antes["swings"]
            est["silencio"] = {"data": hoje_ny(), "chaves": antes.get("chaves", [])}
            est["ultima"] = None
            respostas.append("↩️ EUA: desfeito. Essas ordens nao serao repetidas hoje.")
        elif nome == "/reserva" and len(cmd) == 2:
            cart["reserva_usdt"] = float(cmd[1].replace(",", "."))
            respostas.append(f"✅ Reserva EUA ajustada para US$ {num(cart['reserva_usdt'])}")
        elif nome == "/unidades" and len(cmd) == 3 and cmd[1].upper() in cart["ativos"]:
            cart["ativos"][cmd[1].upper()]["unidades"] = float(cmd[2].replace(",", "."))
            respostas.append(f"✅ {cmd[1].upper()}: {cmd[2]} unidades")
        elif nome == "/carteira":
            respostas.append(texto_carteira(cart, est, precos))
            restantes.append(evento)                      # a B3 responde tambem
        elif nome == "/pausa" and len(cmd) == 2 and cmd[1].isdigit():
            dias = int(cmd[1])
            if dias == 0:
                est.pop("pausa_ate", None)
                respostas.append("▶️ Pausa encerrada: swings e trocas voltam a aparecer.")
            else:
                ate = str((pd.Timestamp(hoje_ny()) + pd.Timedelta(days=dias)).date())
                est["pausa_ate"] = ate
                respostas.append(f"⏸️ Sem swings nem trocas novas ate {pd.Timestamp(ate):%d/%m}. "
                                 f"Realizacoes, saidas e alertas continuam. /pausa 0 encerra.")
            restantes.append(evento)                      # a B3 pausa junto
        else:
            restantes.append(evento)
    return respostas, restantes


def texto_carteira(cart, est, precos):
    base = cart["base_por_ativo"]
    L = ["📋 CARTEIRA AGORA"]
    for t, a in cart["ativos"].items():
        v = a["unidades"] * precos.get(t, 0)
        L.append(f"  {t}: US$ {num(v)} ({pct(v / base - 1)} da base)")
    for t, s in est["swings"].items():
        v = s["unidades"] * precos.get(t, s["preco_medio"])
        L.append(f"  swing {t}: US$ {num(v)} ({pct(precos.get(t, s['preco_medio']) / s['preco_medio'] - 1)})")
    L.append(f"  reserva: US$ {num(cart['reserva_usdt'])}")
    a, s, r = valor_total(cart, est, precos)
    L.append(f"  TOTAL: US$ {num(a + s + r)}")
    return "\n".join(L)


# ============================================================== MONITOR
def monitor(cart, est, dados, ordens):
    """Toda hora: realizacao de +10% e alerta de desastre nos swings."""
    base = cart["base_por_ativo"]
    for t, a in cart["ativos"].items():
        if t not in dados:
            continue
        preco = dados[t]["preco"]
        valor = a["unidades"] * preco
        if valor >= base * (1 + REALIZA_ALTA) and not silenciado(est, f"realiza:{t}"):
            excesso = valor - base
            a["unidades"] -= excesso / preco
            cart["reserva_usdt"] += excesso
            ordens.append((f"realiza:{t}", f"💰 {t} bateu {pct(valor / base - 1)}: VENDA US$ {num(excesso)} "
                          f"de {a['token']} (~{num(excesso / preco, 4)} un.) → reserva"))
    for t, s in est["swings"].items():
        if t in dados and dados[t]["preco"] <= s["preco_medio"] * (1 - DESASTRE) \
                and not est["desastre"].get(t):
            est["desastre"][t] = True
            ordens.append((f"desastre:{t}", f"🚨 Swing {t} caiu {pct(dados[t]['preco'] / s['preco_medio'] - 1)} "
                          f"desde a entrada. Decida: segurar ou vender."))


# ============================================================ VARREDURA
def varredura(cart, est, univ, dados, spy_ok, ordens, pregoes):
    base = cart["base_por_ativo"]

    # --- swings abertos: saida, prazo, segunda metade
    for t in list(est["swings"]):
        s, ind = est["swings"][t], dados.get(t)
        if not ind:
            continue
        preco = ind["preco"]
        dias = s.get("pregoes", 0) + 1
        s["pregoes"] = dias
        motivo = "cruzou a EMA 20 para cima" if preco >= ind["ema20"] else \
                 f"prazo de {PRAZO_SWING} pregoes" if dias >= PRAZO_SWING else None
        if motivo and not silenciado(est, f"saida:{t}"):
            valor = s["unidades"] * preco
            cart["reserva_usdt"] += valor
            ordens.append((f"saida:{t}", f"🎯 SWING {t}: {motivo}. VENDA tudo (~US$ {num(valor)}, "
                          f"{pct(preco / s['preco_medio'] - 1)}) → reserva"))
            del est["swings"][t]
            est["desastre"].pop(t, None)
            continue
        confirmou = ind["min"] > ind["min_ontem"] or \
            (ind["max"] > ind["min"] and (preco - ind["min"]) / (ind["max"] - ind["min"]) >= 0.66)
        if s["metades"] == 1 and confirmou and cart["reserva_usdt"] >= s["meia"] \
                and not silenciado(est, f"metade:{t}"):
            pm_antes = s["preco_medio"]
            s["unidades"] += s["meia"] / preco
            s["preco_medio"] = (s["meia"] * 2) / s["unidades"]
            s["metades"] = 2
            cart["reserva_usdt"] -= s["meia"]
            ordens.append((f"metade:{t}", f"✅ SWING {t} confirmou: COMPRE a 2ª metade, "
                                          f"US$ {num(s['meia'])}",
                           {"tipo": "metade", "ticker": t, "meia": s["meia"],
                            "un": s["meia"] / preco, "pm_antes": pm_antes}))

    # --- candidatos a swing
    ocupados = set(cart["ativos"]) | set(est["swings"])
    if len(est["swings"]) < MAX_SWINGS and cart["reserva_usdt"] >= 125 and not em_pausa(est):
        pre = []
        for t, ind in dados.items():
            if t in ocupados or t in ("SPY", "QQQ") or np.isnan(ind["sma200"]) \
                    or silenciado(est, f"swing:{t}") or recusado(est, t):
                continue
            esticada = (ind["ema20"] - ind["preco"]) / ind["atr"] if ind["atr"] else 0
            if esticada >= ESTICADA_MIN:
                n, _, _ = nota_swing(ind, spy_ok, None)
                pre.append((n, t))
        melhores = []
        for _, t in sorted(pre, reverse=True)[:8]:
            n, partes, esticada = nota_swing(dados[t], spy_ok, teve_resultado(t))
            melhores.append((n, t, partes, esticada))
        melhores.sort(key=lambda x: -x[0])
        registrar_placar([{"data": str(dados[t]["data"].date()), "ticker": t, "nota": n,
                           "preco": dados[t]["preco"], "ret5": "", "ret20": ""}
                          for n, t, _, _ in melhores])
        if melhores and melhores[0][0] >= TAMANHO_SWING[-1][0]:
            n, t, partes, esticada = melhores[0]
            total = min(tamanho_por_nota(n), cart["reserva_usdt"])
            meia = total / 2
            preco = dados[t]["preco"]
            est["swings"][t] = {"unidades": meia / preco, "preco_medio": preco, "meia": meia,
                                "metades": 1, "pregoes": 0,
                                "entrada": str(dados[t]["data"].date())}
            cart["reserva_usdt"] -= meia
            outros = ", ".join(f"{x[1]} {num(x[0], 1)}" for x in melhores[1:4])
            ordens.append((f"swing:{t}",
                f"🎣 SWING: {t} | nota {num(n, 1)}/10\n"
                f"   COMPRE 1ª metade: US$ {num(meia)} (~{num(meia / preco, 4)} un. a US$ {num(preco)})\n"
                f"   2ª metade (US$ {num(meia)}) so quando confirmar\n"
                f"   Saida: quando cruzar a EMA 20 (hoje em US$ {num(dados[t]['ema20'])})\n"
                f"   ⚠️ Confirme se existe {t}on ou {t}B no Binance Wallet"
                + (f"\n   📅 Balanco em {bal:%d/%m}: risco de gap" if (bal := proximo_resultado(t)) else "")
                + (f"\n   Proximos da fila: {outros}" if outros else ""),
                {"tipo": "swing", "ticker": t}))

    # --- trocas
    semana = pd.Timestamp.now(tz=NY).strftime("%G-%V")
    trocas_semana = sum(1 for x in est["trocas"] if x.get("semana") == semana)
    rs = pd.Series({t: d["ret63"] for t, d in dados.items() if not np.isnan(d["ret63"])})
    percentil = rs.rank(pct=True)
    notas = {t: nota_tendencia(d, percentil.get(t, 0)) for t, d in dados.items()
             if t not in ("SPY", "QQQ") and not np.isnan(d["sma200"])}
    if trocas_semana < TROCAS_POR_SEMANA and pregoes is not None and not em_pausa(est):
        elegiveis = []
        for t, a in cart["ativos"].items():
            if t not in dados or t not in notas:
                continue
            valor = a["unidades"] * dados[t]["preco"]
            if valor > base and pregoes_desde(a["desde"], pregoes) >= TROCA_CARENCIA \
                    and not silenciado(est, f"troca:{t}"):
                elegiveis.append((notas[t], t, valor))
        livres = sorted(((n, t) for t, n in notas.items()
                         if t not in ocupados and not recusado(est, t)), reverse=True)
        if elegiveis and livres:
            nota_velho, velho, valor = min(elegiveis)
            nota_novo, novo = livres[0]
            if nota_novo >= max(TROCA_NOTA_MIN, nota_velho + TROCA_MARGEM):
                preco = dados[novo]["preco"]
                antigo = cart["ativos"].pop(velho)
                cart["ativos"][novo] = {"token": f"{novo}on/{novo}B", "unidades": valor / preco,
                                        "preco_print": preco,
                                        "desde": str(dados[novo]["data"].date())}
                est["trocas"].append({"semana": semana, "de": velho, "para": novo})
                ordens.append((f"troca:{velho}",
                    f"🔄 TROCA: VENDA tudo de {antigo['token']} (US$ {num(valor)}, "
                    f"grafico {num(nota_velho, 0)}/10)\n"
                    f"   COMPRE US$ {num(valor)} de {novo} (grafico {num(nota_novo, 0)}/10)\n"
                    f"   ⚠️ Confirme se existe {novo}on ou {novo}B no Binance Wallet",
                    {"tipo": "troca", "velho": velho, "antigo": antigo, "novo": novo,
                     "ticker": novo}))

    # --- revisao
    hoje = str(pd.Timestamp.now().date())
    for t, a in cart["ativos"].items():
        if t in dados and t in notas:
            valor = a["unidades"] * dados[t]["preco"]
            ultimo = est["revisar"].get(t)
            if valor < base * REVISAR_ABAIXO and notas[t] <= 3 and \
                    (not ultimo or (pd.Timestamp(hoje) - pd.Timestamp(ultimo)).days >= 5):
                est["revisar"][t] = hoje
                ordens.append((f"revisar:{t}", f"🔎 REVISAR {t}: US$ {num(valor)} "
                              f"({pct(valor / base - 1)}) e grafico {num(notas[t], 0)}/10. "
                              f"O bot nao vende; so pede sua atencao."))
    return notas


# ============================================================ FECHAMENTO
def fechamento(cart, est, univ, dados, notas, spy_ok, brutos):
    datas_ok = {t: d for t, d in dados.items() if t not in ("SPY", "QQQ")}
    var = {t: d["preco"] / d["ontem"] - 1 for t, d in datas_ok.items()}
    meus = set(cart["ativos"]) | set(est["swings"])

    # ---------- mapa estilo Finviz: variacao do dia
    topo = sorted(datas_ok, key=lambda t: -datas_ok[t]["liq"])[:TAMANHO_MAPA]
    lista = list(dict.fromkeys(list(meus & set(datas_ok)) + topo))
    df = pd.DataFrame({"t": lista, "var": [var[t] for t in lista],
                       "setor": [univ.get(t, "Outros") for t in lista]})
    ordem_setor = df.groupby("setor").size().sort_values(ascending=False).index.tolist()
    df["o"] = df["setor"].map(ordem_setor.index)
    df = df.sort_values(["o", "var"], ascending=[True, False]).reset_index(drop=True)

    COL = 8
    linhas_setor, grade, lin = [], [], 0
    for setor in ordem_setor:
        bloco = df[df["setor"] == setor]
        linhas_setor.append((lin, setor))
        for k, (_, r) in enumerate(bloco.iterrows()):
            grade.append((lin + k // COL, k % COL, r))
        lin += int(np.ceil(len(bloco) / COL)) + 0.5
    fig, ax = plt.subplots(figsize=(COL * 1.3, lin * 0.95 + 1.4))
    fig.patch.set_facecolor("#0d1117"); ax.set_facecolor("#0d1117")
    mapa = plt.get_cmap("RdYlGn")
    for y, x, r in grade:
        v = np.clip(r["var"] / 0.05, -1, 1)
        ax.add_patch(plt.Rectangle((x, -y - 1), 0.94, 0.9, facecolor=mapa((v + 1) / 2)))
        if r["t"] in meus:
            ax.add_patch(plt.Rectangle((x, -y - 1), 0.94, 0.9, fill=False,
                                       edgecolor="white", linewidth=3))
        cor = "#111" if abs(v) < 0.55 else "white"
        ax.text(x + 0.47, -y - 0.42, r["t"], ha="center", fontsize=8.5, weight="bold", color=cor)
        ax.text(x + 0.47, -y - 0.75, pct(r["var"]), ha="center", fontsize=7.5, color=cor)
    for y, setor in linhas_setor:
        ax.text(0, -y + 0.15, setor.upper(), fontsize=9, weight="bold", color="#8b949e")
    ax.set_xlim(-0.1, COL); ax.set_ylim(-lin, 1.3); ax.axis("off")
    dia = max(d["data"] for d in datas_ok.values())
    ax.text(0, 0.95, f"Mapa do dia  -  {dia:%d/%m/%Y}", fontsize=13, weight="bold", color="white")
    ax.text(0, 0.6, "borda branca = sua carteira e seus swings", fontsize=8.5, color="#8b949e")
    plt.tight_layout(); plt.savefig("mapa_dia.png", dpi=110, facecolor=fig.get_facecolor())
    plt.close()

    # ---------- vencedores e perdedores que valem olhar
    def destaque(sinal):
        c = []
        for t, d in datas_ok.items():
            forca = abs(var[t]) / d["atr_pct"] if d["atr_pct"] else 0
            if np.sign(var[t]) == sinal and forca >= 2 and d["vol_rel"] >= 1.5 and d["liq"] >= 5e6:
                c.append((forca, t))
        saida = []
        for forca, t in sorted(c, reverse=True)[:5]:
            d = datas_ok[t]
            tags = [f"{num(forca, 1)}x o normal", f"vol {num(d['vol_rel'], 1)}x"]
            if teve_resultado(t, 2):
                tags.append("📰 resultado")
            if d["preco"] >= 0.98 * d["max52"]:
                tags.append("🔝 max 52 sem")
            if d["preco"] <= 1.02 * d["min52"]:
                tags.append("🕳️ min 52 sem")
            gap = d["abertura"] / d["ontem"] - 1
            if abs(gap) >= 0.03:
                tags.append(f"gap {pct(gap)}")
            if t in notas:
                tags.append(f"grafico {num(notas[t], 0)}/10")
            saida.append(f"  {t} {pct(var[t])} | " + " | ".join(tags))
        return saida or ["  nenhum movimento fora do normal"]

    precos = {t: d["preco"] for t, d in dados.items()}
    a, s, r = valor_total(cart, est, precos)
    total = a + s + r
    inicial = cart.get("capital_inicial", cart["base_por_ativo"] * len(cart["ativos"]) + 1000)
    L = [f"📊 FECHAMENTO {dia:%d/%m}", "",
         "🟢 VENCEDORES QUE VALEM OLHAR"] + destaque(1) + \
        ["", "🔴 PERDEDORES QUE VALEM OLHAR"] + destaque(-1) + ["", "💼 SUA CARTEIRA HOJE"]
    for t in cart["ativos"]:
        if t in dados:
            v = cart["ativos"][t]["unidades"] * dados[t]["preco"]
            L.append(f"  {t} {pct(var.get(t, 0))} | US$ {num(v, 0)}")
    for t, sw in est["swings"].items():
        if t in dados:
            L.append(f"  swing {t} {pct(dados[t]['preco'] / sw['preco_medio'] - 1)} desde a entrada")
    L += [f"  reserva US$ {num(r, 0)} | TOTAL US$ {num(total, 0)} ({pct(total / inicial - 1)} desde o inicio)"]
    if "SPY" in dados:
        L.append(f"  S&P 500 hoje {pct(dados['SPY']['preco'] / dados['SPY']['ontem'] - 1)} | "
                 f"tese {'✅ viva' if spy_ok else '⚠️ contrariada (abaixo da media de 200 dias)'}")

    if em_pausa(est):
        L.append(f"  ⏸️ em pausa ate {pd.Timestamp(est['pausa_ate']):%d/%m}")
    L += aviso_balancos(set(cart["ativos"]) | set(est["swings"]))

    # ---------- placar da nota (sextas)
    if pd.Timestamp.now(tz=NY).weekday() == 4 and os.path.exists(ARQ_PLACAR):
        L += ["", placar_da_nota(brutos)]

    global TOTAL_USD
    TOTAL_USD = total
    enviar_foto("mapa_dia.png", f"Mapa do dia {dia:%d/%m} - cor = variacao do dia")
    enviar("\n".join(L), silencioso=True)


def placar_da_nota(brutos):
    """Retorno exato no 5o e no 20o pregao depois de cada nota registrada."""
    p = pd.read_csv(ARQ_PLACAR)
    for col in ("ret5", "ret20"):
        p[col] = pd.to_numeric(p[col], errors="coerce")
    for i, linha in p.iterrows():
        df = brutos.get(linha["ticker"])
        if df is None:
            continue
        depois = df.index[df.index > pd.Timestamp(linha["data"])]
        for n, col in ((5, "ret5"), (20, "ret20")):
            if pd.isna(p.at[i, col]) and len(depois) >= n:
                p.at[i, col] = float(df.loc[depois[n - 1], "Close"] / linha["preco"] - 1)
    p.to_csv(ARQ_PLACAR, index=False)
    L = ["🧪 PLACAR DA NOTA (retorno 5 pregoes depois)"]
    for rot, filtro in (("nota 8+", p["nota"] >= 8),
                        ("nota 6-7,9", (p["nota"] >= 6) & (p["nota"] < 8)),
                        ("nota < 6", p["nota"] < 6)):
        sub = p.loc[filtro, "ret5"].dropna()
        if len(sub):
            L.append(f"  {rot}: {pct(sub.mean())} em media | acerto "
                     f"{(sub > 0).mean()*100:.0f}% | n={len(sub)}")
    if len(L) == 1:
        L.append("  ainda sem dados suficientes")
    return "\n".join(L)


# ================================================================== MAIN
def main():
    agora = datetime.now(timezone.utc)
    cart, est = carregar()
    hoje_str = str(datetime.now(NY).date())
    # Faz no maximo uma vez por dia, a partir da hora marcada. Se o GitHub
    # atrasar ou pular uma execucao, a seguinte cobre.
    fazer_varredura = MODO in ("tudo", "varredura") or (
        MODO == "auto" and agora.hour >= HORA_VARREDURA and est.get("varredura_em") != hoje_str)
    fazer_fechamento = MODO in ("tudo", "fechamento") or (
        MODO == "auto" and agora.hour >= HORA_FECHAMENTO and est.get("fechamento_em") != hoje_str)

    univ = universo() if (fazer_varredura or fazer_fechamento) else {}
    alvo = set(cart["ativos"]) | set(est["swings"]) | {"SPY", "QQQ"} | set(univ)
    brutos = baixar(alvo)
    dados = {t: indicadores(df) for t, df in brutos.items()}
    pregoes = brutos["SPY"].index if "SPY" in brutos else None
    precos = {t: d["preco"] for t, d in dados.items()}

    # pregao de hoje aconteceu?
    data_ny = pd.Timestamp(datetime.now(NY).date())
    pregao_hoje = "SPY" in dados and pd.Timestamp(dados["SPY"]["data"]).normalize() == data_ny
    spy_ok = "SPY" in dados and dados["SPY"]["preco"] > dados["SPY"]["sma200"]

    limite = str((pd.Timestamp(hoje_ny()) - pd.Timedelta(days=2)).date())
    est["pendentes"] = {k: v for k, v in est.get("pendentes", {}).items() if v["data"] >= limite}
    eventos = ler_comandos(est)
    respostas, restantes = processar_comandos(cart, est, precos, eventos)
    for resp in respostas:
        enviar(resp, silencioso=True)

    if not est.get("validado"):
        avisos = []
        for t, a in cart["ativos"].items():
            if t not in dados:
                avisos.append(f"  {t}: sem dados no Yahoo - confirme o ticker")
            elif abs(dados[t]["preco"] / a["preco_print"] - 1) > TOL_PRECO:
                avisos.append(f"  {t}: preco US$ {num(dados[t]['preco'])} x print US$ "
                              f"{num(a['preco_print'])} - ticker pode estar errado")
        enviar("🤖 Bot ligado.\n" + ("⚠️ Confira:\n" + "\n".join(avisos) if avisos
                                    else "✅ Os 7 ativos bateram com o seu print."))
        est["validado"] = True

    antes = {"carteira": copy.deepcopy(cart), "swings": copy.deepcopy(est["swings"])}
    ordens = []
    if pregao_hoje or MODO != "auto":
        monitor(cart, est, dados, ordens)
        notas = {}
        if fazer_varredura:
            notas = varredura(cart, est, univ, dados, spy_ok, ordens, pregoes)
            est["varredura_em"] = hoje_str
        if ordens:
            ordens = [o if len(o) == 3 else (o[0], o[1], None) for o in ordens]
            est["ultima"] = {**antes, "chaves": [o[0] for o in ordens]}
            normais = [txt for _, txt, x in ordens if x is None]
            if normais:
                enviar("\n\n".join(normais) + "\n\nNao executou? Responda /nao que eu desfaco.")
            for chave, txt, x in ordens:
                if x is not None:
                    mid = enviar(txt, botao=("❌ Recusar", f"rec|{chave}"))
                    est.setdefault("pendentes", {})[chave] = {**x, "msg_id": mid,
                                                              "data": hoje_ny()}
        if fazer_fechamento:
            if not notas:
                rs = pd.Series({t: d["ret63"] for t, d in dados.items() if not np.isnan(d["ret63"])})
                pc = rs.rank(pct=True)
                notas = {t: nota_tendencia(d, pc.get(t, 0)) for t, d in dados.items()
                         if t not in ("SPY", "QQQ") and not np.isnan(d["sma200"])}
            fechamento(cart, est, univ, dados, notas, spy_ok, brutos)
            est["fechamento_em"] = hoje_str
    else:
        print("EUA sem pregao hoje.")

    salvar(cart, est)

    # ---------------- B3: mesmo esquema, feriados e horarios proprios
    if os.path.exists("carteira_b3.json"):
        try:
            import b3
            b3.rodar(restantes, MODO, agora, total_usd=TOTAL_USD)
        except Exception as e:
            enviar(f"⚠️ A parte da B3 falhou nesta execucao: {e}")
            raise


if __name__ == "__main__":
    main()
