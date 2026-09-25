"""
B3 - O MESMO ESQUEMA DO BOT AMERICANO, PARA AS ACOES NO BRB.

Roda dentro do bot.py (mesmo Telegram), com carteira e reserva proprias em R$.

  A cada hora do pregao:   realizacao de +10% sobre o custo e alertas
  18h UTC (15h Brasilia):  varredura -> swings e trocas (antes do fechamento)
  21h UTC (18h Brasilia):  fechamento estilo Finviz da B3

Diferencas em relacao aos EUA:
  - base de cada posicao = o que voce pagou (preco medio x quantidade)
  - quantidades inteiras; abaixo de 100 acoes, fracionario (ticker + F)
  - contador de vendas do mes: isencao de R$ 20 mil para acoes
  - BDR (final 31 a 39) nao tem isencao: 15% sobre o lucro de qualquer venda

Comandos: /nao_br  /reserva_br 2000  /recusar PETR4  /unidades LREN3 400  /carteira
"""

import base64
import copy
import json
import math
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

import bot as nucleo

SP = ZoneInfo("America/Sao_Paulo")
ARQ_CARTEIRA, ARQ_ESTADO, ARQ_PLACAR = "carteira_b3.json", "estado_b3.json", "placar_notas_b3.csv"

# ================================================================ REGRAS
REALIZA_ALTA = 0.10
MAX_SWINGS = 2
ESTICADA_MIN = 2.0
TAMANHO_SWING = [(8.0, 2000.0), (6.0, 1000.0)]       # R$
PRAZO_SWING = 20
DESASTRE = 0.20
TROCA_NOTA_MIN, TROCA_MARGEM, TROCA_CARENCIA, TROCAS_POR_SEMANA = 7.0, 2.0, 10, 2
REVISAR_ABAIXO = 0.75
DIAS_SILENCIO = 5
HORA_VARREDURA = 18      # UTC -> 15h15 Brasilia, antes do fechamento
HORA_FECHAMENTO = 21     # UTC -> 18h15 Brasilia, depois do fechamento
ISENCAO, AVISO_ISENCAO, ALIQUOTA = 20000.0, 15000.0, 0.15
INDICE = "^BVSP"
TAMANHO_MAPA = 64

# Universo de reserva (usado se a B3 nao responder). Tickers antigos ou que
# mudaram de nome sao descartados sozinhos quando o Yahoo nao tem dados.
UNIVERSO_RESERVA = {
    "Financeiro": ["ITUB4", "ITUB3", "BBDC4", "BBDC3", "BBAS3", "SANB11", "BPAC11", "ITSA4",
                   "B3SA3", "BBSE3", "CXSE3", "PSSA3", "IRBR3", "ABCB4", "BMGB4", "BPAN4",
                   "BRSR6", "BRBI11", "CSUD3", "WIZC3", "INBR32", "ROXO34", "XPBR31", "STOC31"],
    "Petroleo": ["PETR4", "PETR3", "PRIO3", "RECV3", "BRAV3", "RRRP3", "CSAN3", "UGPA3",
                 "VBBR3", "RAIZ4", "OPCT3", "BRKM5"],
    "Mineracao": ["VALE3", "CMIN3", "CSNA3", "GGBR4", "GOAU4", "USIM5", "BRAP4", "CBAV3", "FESA4"],
    "Eletricas": ["ELET3", "ELET6", "AXIA3", "AXIA6", "EQTL3", "CMIG4", "CPLE6", "CPLE3", "EGIE3",
                  "ENGI11", "TAEE11", "CPFE3", "ENEV3", "ALUP11", "AURE3", "ISAE4", "SRNA3",
                  "LIGT3", "SBSP3", "SAPR11", "CSMG3", "AMBP3"],
    "Varejo": ["LREN3", "MGLU3", "BHIA3", "AZZA3", "CEAB3", "GUAR3", "AMAR3", "SBFG3", "PETZ3",
               "AUAU3", "ASAI3", "PCAR3", "GMAT3", "RADL3", "PGMN3", "PNVL3", "VIVA3", "ALPA4",
               "GRND3", "VULC3", "LJQQ3", "ESPA3", "SEQL3", "NATU3", "ABEV3"],
    "Construcao": ["CYRE3", "EZTC3", "MRVE3", "TEND3", "DIRR3", "CURY3", "EVEN3", "JHSF3",
                   "LAVV3", "MTRE3", "PLPL3", "TRIS3", "MDNE3", "GFSA3", "HBRE3",
                   "MULT3", "ALOS3", "IGTI11", "LOGG3"],
    "Saude": ["RDOR3", "HAPV3", "FLRY3", "QUAL3", "ODPV3", "HYPE3", "BLAU3", "ONCO3", "MATD3",
              "DASA3", "VVEO3", "AALR3"],
    "Educacao": ["YDUQ3", "COGN3", "ANIM3", "CSED3", "SEER3"],
    "Transporte": ["RAIL3", "MOTV3", "CCRO3", "ECOR3", "STBP3", "JSLG3", "TGMA3", "HBSA3",
                   "LOGN3", "PORT3", "AZUL4", "EMBR3", "RENT3", "MOVI3", "VAMO3", "SIMH3", "ARML3"],
    "Agro": ["JBSS32", "MBRF3", "BRFS3", "MRFG3", "BEEF3", "SMTO3", "SLCE3", "AGRO3", "TTEN3",
             "SOJA3", "CAML3", "MDIA3", "JALL3"],
    "Tecnologia": ["TOTS3", "LWSA3", "CASH3", "INTB3", "POSI3", "MLAS3", "VIVT3", "TIMS3", "DESK3"],
    "Industria": ["WEGE3", "TUPY3", "RAPT4", "POMO4", "FRAS3", "LEVE3", "MYPK3", "SHUL4", "KEPL3",
                  "ROMI3", "DXCO3", "EUCA4", "UNIP6", "SUZB3", "KLBN11", "RANI3"],
}
SETOR_DE = {t: s for s, lista in UNIVERSO_RESERVA.items() for t in lista}

num, pct = nucleo.num, nucleo.pct
brl = lambda x, c=2: "R$ " + num(x, c)


def hoje():
    return str(datetime.now(SP).date())


def eh_bdr(t):
    return len(t) >= 6 and t[-2:].isdigit() and 31 <= int(t[-2:]) <= 39


def lote(t, n):
    """Como executar n acoes: lote padrao de 100 + fracionario."""
    if n < 100:
        return f"{n} acoes no fracionario ({t}F)"
    if n % 100 == 0:
        return f"{n} acoes ({n // 100} lote{'s' if n >= 200 else ''})"
    return f"{n} acoes ({n // 100 * 100} no lote padrao + {n % 100} no fracionario {t}F)"


# ============================================================ UNIVERSO
def indice_b3(codigo):
    carga = json.dumps({"language": "pt-br", "pageNumber": 1, "pageSize": 200,
                        "index": codigo, "segment": "1"}, separators=(",", ":"))
    url = ("https://sistemaswebb3-listados.b3.com.br/indexProxy/indexCall/GetPortfolioDay/"
           + base64.b64encode(carga.encode()).decode())
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).json()
    return [x["cod"].strip() for x in r.get("results", []) if x.get("cod")]


def universo(carteira):
    """IBrX-100 + Small Caps, direto da B3. Se falhar, a lista de reserva."""
    tickers, fonte = set(), "B3 (IBrX-100 + Small Caps)"
    for codigo in ("IBXX", "SMLL"):
        try:
            tickers |= set(indice_b3(codigo))
        except Exception as e:
            print(f"B3 {codigo} indisponivel: {e}")
    if len(tickers) < 80:
        tickers, fonte = set(SETOR_DE), "lista de reserva"
    tickers |= set(carteira["ativos"])
    return {t: SETOR_DE.get(t, "Outros") for t in tickers}, fonte


# ================================================================ ESTADO
def carregar():
    with open(ARQ_CARTEIRA, encoding="utf-8") as f:
        cart = json.load(f)
    est = {"swings": {}, "ultima": None, "validado": False, "revisar": {}, "trocas": [],
           "desastre": {}, "pendentes": {}, "recusados": {}, "silencio": {},
           "vendas_mes": {"mes": hoje()[:7], "acoes": 0.0, "lucro_acoes": 0.0,
                          "bdr": 0.0, "lucro_bdr": 0.0}}
    if os.path.exists(ARQ_ESTADO):
        with open(ARQ_ESTADO, encoding="utf-8") as f:
            est.update(json.load(f))
    if est["vendas_mes"]["mes"] != hoje()[:7]:          # virou o mes: zera o contador
        fechar_mes_darf(est, est["vendas_mes"])
        est["vendas_mes"] = {"mes": hoje()[:7], "acoes": 0.0, "lucro_acoes": 0.0,
                             "bdr": 0.0, "lucro_bdr": 0.0}
    return cart, est


# ================================================================ DARF
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def ir_do_mes(v):
    """IR estimado do mes: acoes so se passou da isencao; BDR sempre. Nao desconta
    prejuizo de meses anteriores - e uma estimativa, confira antes de pagar."""
    ir = max(v["lucro_bdr"], 0) * ALIQUOTA
    if v["acoes"] > ISENCAO:
        ir += max(v["lucro_acoes"], 0) * ALIQUOTA
    return ir


def fechar_mes_darf(est, v):
    """Na virada do mes, guarda o DARF do mes que fechou (vence no ultimo dia util
    do mes seguinte, ou seja, o mes atual)."""
    ir = ir_do_mes(v)
    if ir < 1:
        return
    vence = (pd.Timestamp(hoje()) + pd.offsets.BMonthEnd(0)).date()
    est["darf"] = {"mes": v["mes"], "ir": round(ir, 2), "vence": str(vence),
                   "avisado": False, "lembrado": False}


def avisos_darf(est):
    """Aviso na virada do mes e lembrete 5 dias uteis antes. Some depois de vencer."""
    dv = est.get("darf")
    if not dv:
        return []
    vence, h = pd.Timestamp(dv["vence"]), pd.Timestamp(hoje())
    if h > vence:
        est.pop("darf", None)
        return []
    ano, mes = dv["mes"].split("-")
    rot = f"{MESES[int(mes) - 1]}/{ano[2:]}"
    saida = []
    if not dv["avisado"]:
        dv["avisado"] = True
        saida.append(f"🧾 DARF de {rot}: IR estimado {brl(dv['ir'])}, vence {vence:%d/%m}. "
                     f"Estimativa do bot (nao desconta prejuizo de meses anteriores).")
    elif not dv["lembrado"] and len(pd.bdate_range(h, vence)) <= 5:
        dv["lembrado"] = True
        saida.append(f"⏰ Lembrete: DARF de {rot} ({brl(dv['ir'])}) vence {vence:%d/%m}.")
    return saida


def salvar(cart, est):
    with open(ARQ_CARTEIRA, "w", encoding="utf-8") as f:
        json.dump(cart, f, indent=2, ensure_ascii=False)
    with open(ARQ_ESTADO, "w", encoding="utf-8") as f:
        json.dump(est, f, indent=2, ensure_ascii=False, default=str)


def silenciado(est, chave):
    sil = est.get("silencio", {})
    return sil.get("data") == hoje() and chave in sil.get("chaves", [])


def recusado(est, t):
    ate = est.get("recusados", {}).get(t)
    return bool(ate) and ate >= hoje()


def em_pausa(est):
    ate = est.get("pausa_ate")
    return bool(ate) and ate >= hoje()


# ============================================================== IMPOSTO
def registrar_venda(est, t, qtd, preco, pm):
    """Soma a venda no contador do mes e devolve o aviso de imposto."""
    valor, lucro = qtd * preco, qtd * (preco - pm)
    v = est["vendas_mes"]
    if eh_bdr(t):
        v["bdr"] += valor
        v["lucro_bdr"] += lucro
        ir = max(lucro, 0) * ALIQUOTA
        return (f"   🧾 BDR nao tem isencao: lucro {brl(lucro)} → IR ~{brl(ir)} "
                f"(DARF ate o ultimo dia util do mes seguinte)")
    antes = v["acoes"]
    v["acoes"] += valor
    v["lucro_acoes"] += lucro
    if v["acoes"] > ISENCAO >= antes:
        return (f"   🧾 ATENCAO: esta venda passa de R$ 20 mil em vendas de acoes no mes "
                f"({brl(v['acoes'])}). TODO o lucro do mes fica tributavel: "
                f"IR ~{brl(max(v['lucro_acoes'], 0) * ALIQUOTA)}")
    if v["acoes"] > ISENCAO:
        return (f"   🧾 Vendas de acoes no mes: {brl(v['acoes'])} (acima da isencao). "
                f"IR do mes ~{brl(max(v['lucro_acoes'], 0) * ALIQUOTA)}")
    if v["acoes"] >= AVISO_ISENCAO:
        return (f"   🧾 Vendas de acoes no mes: {brl(v['acoes'])}. Faltam "
                f"{brl(ISENCAO - v['acoes'])} para o limite da isencao")
    return ""


def cruzaria_isencao(est, t, valor):
    """True se esta venda de ACOES levaria o mes de ate R$ 20 mil para acima disso."""
    if eh_bdr(t):
        return False
    atual = est["vendas_mes"]["acoes"]
    return atual <= ISENCAO < atual + valor


def aviso_adiada(est, t, valor, o_que):
    """Mensagem de adiamento, uma vez por mes para cada ativo."""
    chave = f"{o_que}:{t}"
    if est.setdefault("adiadas", {}).get(chave) == hoje()[:7]:
        return None
    est["adiadas"][chave] = hoje()[:7]
    lucro = est["vendas_mes"]["lucro_acoes"]
    return (f"⏸️ {t}: {o_que} ADIADA para o proximo mes. Esta venda de ~{brl(valor)} passaria "
            f"de R$ 20 mil em vendas de acoes no mes e tornaria tributavel todo o lucro "
            f"do mes (IR ~{brl(max(lucro, 0) * ALIQUOTA)}). No mes que vem o contador zera.")


def desfazer_venda(est, t, qtd, preco, pm):
    v = est["vendas_mes"]
    campo = "bdr" if eh_bdr(t) else "acoes"
    v[campo] -= qtd * preco
    v["lucro_" + campo] -= qtd * (preco - pm)


# ============================================================ COMANDOS
def recusar(cart, est, chave):
    pend = est["pendentes"].pop(chave, None)
    if not pend:
        return False
    tipo, t = pend["tipo"], pend.get("ticker")
    ate = str((pd.Timestamp(hoje()) + pd.Timedelta(days=DIAS_SILENCIO)).date())
    if tipo == "swing" and t in est["swings"]:
        s = est["swings"].pop(t)
        cart["reserva_brl"] += s["qtd"] * s["pm"]
        outra = est["pendentes"].pop(f"metade:{t}", None)
        if outra:
            nucleo.apagar(outra.get("msg_id"))
        est["recusados"][t] = ate
    elif tipo == "metade" and t in est["swings"] and est["swings"][t]["metades"] == 2:
        s = est["swings"][t]
        s["qtd"] -= pend["qtd"]
        s["pm"] = pend["pm_antes"]
        s["metades"] = 1
        cart["reserva_brl"] += pend["qtd"] * pend["preco"]
        if est["silencio"].get("data") != hoje():
            est["silencio"] = {"data": hoje(), "chaves": []}
        est["silencio"]["chaves"].append(chave)
    elif tipo == "troca":
        cart["ativos"].pop(pend["novo"], None)
        cart["ativos"][pend["velho"]] = pend["antigo"]
        cart["reserva_brl"] = pend["reserva_antes"]
        desfazer_venda(est, pend["velho"], pend["antigo"]["qtd"], pend["preco_venda"],
                       pend["antigo"]["pm"])
        est["recusados"][pend["novo"]] = ate
    nucleo.apagar(pend.get("msg_id"))
    return True


def processar_eventos(cart, est, eventos, precos):
    for ev in eventos:
        if ev[0] == "botao":
            if ev[1].startswith("rb3|"):
                recusar(cart, est, ev[1][4:])
            continue
        cmd, msg_id = ev[1], ev[2]
        nome = cmd[0].lower().split("@")[0]
        if nome == "/nao_br" and est.get("ultima"):
            antes = est["ultima"]
            cart.clear(); cart.update(antes["carteira"])
            est["swings"], est["vendas_mes"] = antes["swings"], antes["vendas_mes"]
            est["adiadas"] = antes.get("adiadas", {})
            est["silencio"] = {"data": hoje(), "chaves": antes.get("chaves", [])}
            est["ultima"] = None
            nucleo.enviar("↩️ B3: desfeito. Essas ordens nao serao repetidas hoje.", silencioso=True)
        elif nome == "/reserva_br" and len(cmd) == 2:
            novo = float(cmd[1].replace(".", "").replace(",", "."))
            aporte = novo - cart["reserva_brl"]
            cart["reserva_brl"] = novo
            cart["capital_inicial"] = cart.get("capital_inicial", 0) + aporte
            nucleo.enviar(f"✅ Reserva B3: {brl(novo)} (conta como aporte, nao como lucro)", silencioso=True)
        elif nome == "/unidades" and len(cmd) == 3 and cmd[1].upper() in cart["ativos"]:
            cart["ativos"][cmd[1].upper()]["qtd"] = int(float(cmd[2].replace(",", ".")))
            nucleo.enviar(f"✅ {cmd[1].upper()}: {cmd[2]} acoes", silencioso=True)
        elif nome == "/recusar" and len(cmd) == 2:
            alvo = cmd[1].upper()
            chaves = [k for k, v in est["pendentes"].items() if alvo in (v.get("ticker"), v.get("novo"))]
            if chaves:
                for k in chaves:
                    recusar(cart, est, k)
                nucleo.apagar(msg_id)
        elif nome == "/carteira":
            nucleo.enviar(texto_carteira(cart, est, precos), silencioso=True)
        elif nome == "/pausa" and len(cmd) == 2 and cmd[1].isdigit():
            dias = int(cmd[1])
            if dias == 0:
                est.pop("pausa_ate", None)
            else:
                est["pausa_ate"] = str((pd.Timestamp(hoje()) + pd.Timedelta(days=dias)).date())


def texto_carteira(cart, est, precos):
    L = ["📋 CARTEIRA B3 AGORA"]
    total = 0.0
    for t, a in cart["ativos"].items():
        v = a["qtd"] * precos.get(t, a["preco_print"])
        total += v
        L.append(f"  {t}: {brl(v)} ({pct(v / a['base'] - 1)} sobre o custo)")
    for t, s in est["swings"].items():
        v = s["qtd"] * precos.get(t, s["pm"])
        total += v
        L.append(f"  swing {t}: {brl(v)} ({pct(precos.get(t, s['pm']) / s['pm'] - 1)})")
    total += cart["reserva_brl"]
    L += [f"  reserva: {brl(cart['reserva_brl'])}", f"  TOTAL: {brl(total)}"]
    return "\n".join(L)


# ============================================================== MONITOR
def monitor(cart, est, dados, ordens):
    for t, a in cart["ativos"].items():
        if t not in dados or silenciado(est, f"realiza:{t}"):
            continue
        preco = dados[t]["preco"]
        valor = a["qtd"] * preco
        if valor >= a["base"] * (1 + REALIZA_ALTA):
            n = math.floor((valor - a["base"]) / preco)
            if n < 1:
                continue
            if cruzaria_isencao(est, t, n * preco):
                msg = aviso_adiada(est, t, n * preco, "realizacao")
                if msg:
                    ordens.append((f"adiada:{t}", msg))
                continue
            a["qtd"] -= n
            cart["reserva_brl"] += n * preco
            aviso = registrar_venda(est, t, n, preco, a["pm"])
            ordens.append((f"realiza:{t}",
                           f"💰 {t} bateu {pct(valor / a['base'] - 1)} sobre o custo: VENDA "
                           f"{lote(t, n)} a ~{brl(preco)} = {brl(n * preco)} → reserva"
                           + (f"\n{aviso}" if aviso else "")))
    for t, s in est["swings"].items():
        if t in dados and dados[t]["preco"] <= s["pm"] * (1 - DESASTRE) and not est["desastre"].get(t):
            est["desastre"][t] = True
            ordens.append((f"desastre:{t}", f"🚨 Swing {t} caiu "
                           f"{pct(dados[t]['preco'] / s['pm'] - 1)} desde a entrada. "
                           f"Decida: segurar ou vender."))


# ============================================================ VARREDURA
def varredura(cart, est, univ, dados, indice_ok, ordens, pregoes):
    # --- swings abertos
    for t in list(est["swings"]):
        s, ind = est["swings"][t], dados.get(t)
        if not ind:
            continue
        preco = ind["preco"]
        s["pregoes"] = s.get("pregoes", 0) + 1
        motivo = "cruzou a EMA 20 para cima" if preco >= ind["ema20"] else \
            f"prazo de {PRAZO_SWING} pregoes" if s["pregoes"] >= PRAZO_SWING else None
        if motivo and not silenciado(est, f"saida:{t}"):
            aviso = registrar_venda(est, t, s["qtd"], preco, s["pm"])
            cart["reserva_brl"] += s["qtd"] * preco
            ordens.append((f"saida:{t}", f"🎯 SWING {t}: {motivo}. VENDA {lote(t, s['qtd'])} "
                           f"(~{brl(s['qtd'] * preco)}, {pct(preco / s['pm'] - 1)}) → reserva"
                           + (f"\n{aviso}" if aviso else "")))
            del est["swings"][t]
            est["desastre"].pop(t, None)
            continue
        confirmou = ind["min"] > ind["min_ontem"] or \
            (ind["max"] > ind["min"] and (preco - ind["min"]) / (ind["max"] - ind["min"]) >= 0.66)
        n = math.floor(s["meia"] / preco)
        if s["metades"] == 1 and confirmou and n >= 1 and cart["reserva_brl"] >= n * preco \
                and not silenciado(est, f"metade:{t}"):
            pm_antes = s["pm"]
            s["pm"] = (s["qtd"] * s["pm"] + n * preco) / (s["qtd"] + n)
            s["qtd"] += n
            s["metades"] = 2
            cart["reserva_brl"] -= n * preco
            ordens.append((f"metade:{t}", f"✅ SWING {t} confirmou: COMPRE a 2ª metade, "
                           f"{lote(t, n)} a ~{brl(preco)} = {brl(n * preco)}",
                           {"tipo": "metade", "ticker": t, "qtd": n, "preco": preco,
                            "pm_antes": pm_antes}))

    # --- candidatos a swing
    ocupados = set(cart["ativos"]) | set(est["swings"])
    menor = TAMANHO_SWING[-1][1] / 2
    if len(est["swings"]) < MAX_SWINGS and cart["reserva_brl"] >= menor and not em_pausa(est):
        pre = []
        for t, ind in dados.items():
            if t in ocupados or t == INDICE or np.isnan(ind["sma200"]) or recusado(est, t) \
                    or silenciado(est, f"swing:{t}"):
                continue
            esticada = (ind["ema20"] - ind["preco"]) / ind["atr"] if ind["atr"] else 0
            if esticada >= ESTICADA_MIN and ind["liq"] >= 2e6:
                n, _, _ = nucleo.nota_swing(ind, indice_ok, None)
                pre.append((n, t))
        melhores = []
        for _, t in sorted(pre, reverse=True)[:8]:
            n, _, _ = nucleo.nota_swing(dados[t], indice_ok, nucleo.teve_resultado(f"{t}.SA"))
            melhores.append((n, t))
        melhores.sort(key=lambda x: -x[0])
        registrar_placar([{"data": str(dados[t]["data"].date()), "ticker": t, "nota": n,
                           "preco": dados[t]["preco"], "ret5": "", "ret20": ""} for n, t in melhores])
        if melhores and melhores[0][0] >= TAMANHO_SWING[-1][0]:
            n, t = melhores[0]
            total = min(_tamanho(n), cart["reserva_brl"] * 2)
            meia = total / 2
            preco = dados[t]["preco"]
            qtd = math.floor(min(meia, cart["reserva_brl"]) / preco)
            if qtd >= 1:
                est["swings"][t] = {"qtd": qtd, "pm": preco, "meia": meia, "metades": 1,
                                    "pregoes": 0, "entrada": str(dados[t]["data"].date())}
                cart["reserva_brl"] -= qtd * preco
                outros = ", ".join(f"{x[1]} {num(x[0], 1)}" for x in melhores[1:4])
                ordens.append((f"swing:{t}",
                               f"🎣 SWING B3: {t} | nota {num(n, 1)}/10\n"
                               f"   COMPRE 1ª metade: {lote(t, qtd)} a ~{brl(preco)} = {brl(qtd * preco)}\n"
                               f"   2ª metade (~{brl(meia)}) so quando confirmar\n"
                               f"   Saida: quando cruzar a EMA 20 (hoje em {brl(dados[t]['ema20'])})"
                               + ("\n   🧾 BDR: venda futura paga 15% sobre o lucro" if eh_bdr(t) else "")
                               + (f"\n   📅 Balanco em {bal:%d/%m}: risco de gap"
                                  if (bal := nucleo.proximo_resultado(f"{t}.SA")) else "")
                               + (f"\n   Proximos da fila: {outros}" if outros else ""),
                               {"tipo": "swing", "ticker": t}))

    # --- trocas: so em posicao no lucro
    semana = pd.Timestamp.now(tz=SP).strftime("%G-%V")
    rs = pd.Series({t: d["ret63"] for t, d in dados.items() if not np.isnan(d["ret63"])})
    percentil = rs.rank(pct=True)
    notas = {t: nucleo.nota_tendencia(d, percentil.get(t, 0)) for t, d in dados.items()
             if t != INDICE and not np.isnan(d["sma200"])}
    if sum(1 for x in est["trocas"] if x.get("semana") == semana) < TROCAS_POR_SEMANA and pregoes is not None \
            and not em_pausa(est):
        elegiveis = []
        for t, a in cart["ativos"].items():
            if t in dados and t in notas and not silenciado(est, f"troca:{t}"):
                valor = a["qtd"] * dados[t]["preco"]
                if valor > a["base"] and nucleo.pregoes_desde(a["desde"], pregoes) >= TROCA_CARENCIA:
                    elegiveis.append((notas[t], t, valor))
        livres = sorted(((n, t) for t, n in notas.items()
                         if t not in ocupados and not recusado(est, t) and dados[t]["liq"] >= 2e6),
                        reverse=True)
        if elegiveis and livres:
            nota_velho, velho, valor = min(elegiveis)
            nota_novo, novo = livres[0]
            if nota_novo >= max(TROCA_NOTA_MIN, nota_velho + TROCA_MARGEM) and \
                    cruzaria_isencao(est, velho, valor):
                msg = aviso_adiada(est, velho, valor, "troca")
                if msg:
                    ordens.append((f"adiada:{velho}", msg))
            elif nota_novo >= max(TROCA_NOTA_MIN, nota_velho + TROCA_MARGEM):
                p_venda, p_compra = dados[velho]["preco"], dados[novo]["preco"]
                antigo = cart["ativos"].pop(velho)
                reserva_antes = cart["reserva_brl"]
                aviso = registrar_venda(est, velho, antigo["qtd"], p_venda, antigo["pm"])
                qtd_nova = math.floor(valor / p_compra)
                cart["reserva_brl"] += valor - qtd_nova * p_compra     # troco vai para a reserva
                cart["ativos"][novo] = {"qtd": qtd_nova, "pm": p_compra, "base": qtd_nova * p_compra,
                                        "preco_print": p_compra, "desde": str(dados[novo]["data"].date())}
                est["trocas"].append({"semana": semana, "de": velho, "para": novo})
                ordens.append((f"troca:{velho}",
                               f"🔄 TROCA B3: VENDA {lote(velho, antigo['qtd'])} de {velho} "
                               f"(~{brl(valor)}, grafico {num(nota_velho, 0)}/10)\n"
                               f"   COMPRE {lote(novo, qtd_nova)} de {novo} (grafico {num(nota_novo, 0)}/10)"
                               + (f"\n{aviso}" if aviso else ""),
                               {"tipo": "troca", "velho": velho, "antigo": antigo, "novo": novo,
                                "ticker": novo, "preco_venda": p_venda,
                                "reserva_antes": reserva_antes}))

    # --- revisao
    for t, a in cart["ativos"].items():
        if t in dados and t in notas:
            valor = a["qtd"] * dados[t]["preco"]
            ultimo = est["revisar"].get(t)
            if valor < a["base"] * REVISAR_ABAIXO and notas[t] <= 3 and \
                    (not ultimo or (pd.Timestamp(hoje()) - pd.Timestamp(ultimo)).days >= 5):
                est["revisar"][t] = hoje()
                ordens.append((f"revisar:{t}", f"🔎 REVISAR {t}: {brl(valor)} "
                               f"({pct(valor / a['base'] - 1)} sobre o custo) e grafico "
                               f"{num(notas[t], 0)}/10. O bot nao vende; so pede sua atencao."))
    return notas


def _tamanho(nota):
    for minimo, valor in TAMANHO_SWING:
        if nota >= minimo:
            return valor
    return 0.0


def registrar_placar(linhas):
    if linhas:
        novo = not os.path.exists(ARQ_PLACAR)
        pd.DataFrame(linhas).to_csv(ARQ_PLACAR, mode="a", header=novo, index=False)


# ============================================================ FECHAMENTO
def fechamento(cart, est, univ, dados, notas, indice_ok, fonte, total_usd=None):
    acoes = {t: d for t, d in dados.items() if t != INDICE}
    var = {t: d["preco"] / d["ontem"] - 1 for t, d in acoes.items()}
    meus = set(cart["ativos"]) | set(est["swings"])

    topo = sorted(acoes, key=lambda t: -acoes[t]["liq"])[:TAMANHO_MAPA]
    lista = list(dict.fromkeys([t for t in meus if t in acoes] + topo))
    df = pd.DataFrame({"t": lista, "var": [var[t] for t in lista],
                       "setor": [univ.get(t, "Outros") for t in lista]})
    ordem = df.groupby("setor").size().sort_values(ascending=False).index.tolist()
    df["o"] = df["setor"].map(ordem.index)
    df = df.sort_values(["o", "var"], ascending=[True, False])
    COL, grade, rotulos, lin = 8, [], [], 0
    for setor in ordem:
        bloco = df[df["setor"] == setor]
        rotulos.append((lin, setor))
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
            ax.add_patch(plt.Rectangle((x, -y - 1), 0.94, 0.9, fill=False, edgecolor="white", linewidth=3))
        cor = "#111" if abs(v) < 0.55 else "white"
        ax.text(x + 0.47, -y - 0.42, r["t"], ha="center", fontsize=8.5, weight="bold", color=cor)
        ax.text(x + 0.47, -y - 0.75, pct(r["var"]), ha="center", fontsize=7.5, color=cor)
    for y, setor in rotulos:
        ax.text(0, -y + 0.15, setor.upper(), fontsize=9, weight="bold", color="#8b949e")
    ax.set_xlim(-0.1, COL); ax.set_ylim(-lin, 1.3); ax.axis("off")
    dia = max(d["data"] for d in acoes.values())
    ax.text(0, 0.95, f"Mapa B3  -  {dia:%d/%m/%Y}", fontsize=13, weight="bold", color="white")
    ax.text(0, 0.6, "borda branca = sua carteira e seus swings", fontsize=8.5, color="#8b949e")
    plt.tight_layout(); plt.savefig("mapa_b3.png", dpi=110, facecolor=fig.get_facecolor()); plt.close()

    def destaque(sinal):
        c = sorted(((abs(var[t]) / d["atr_pct"], t) for t, d in acoes.items()
                    if d["atr_pct"] and np.sign(var[t]) == sinal and abs(var[t]) / d["atr_pct"] >= 2
                    and d["vol_rel"] >= 1.5 and d["liq"] >= 5e6), reverse=True)[:5]
        saida = []
        for forca, t in c:
            d = acoes[t]
            tags = [f"{num(forca, 1)}x o normal", f"vol {num(d['vol_rel'], 1)}x"]
            if d["preco"] >= 0.98 * d["max52"]:
                tags.append("🔝 max 52 sem")
            if d["preco"] <= 1.02 * d["min52"]:
                tags.append("🕳️ min 52 sem")
            gap = d["abertura"] / d["ontem"] - 1
            if abs(gap) >= 0.03:
                tags.append(f"gap {pct(gap)}")
            if t in notas:
                tags.append(f"grafico {num(notas[t], 0)}/10")
            if eh_bdr(t):
                tags.append("BDR")
            saida.append(f"  {t} {pct(var[t])} | " + " | ".join(tags))
        return saida or ["  nenhum movimento fora do normal"]

    total = cart["reserva_brl"]
    L = [f"🇧🇷 FECHAMENTO B3 {dia:%d/%m}", "", "🟢 VENCEDORES QUE VALEM OLHAR"] + destaque(1) + \
        ["", "🔴 PERDEDORES QUE VALEM OLHAR"] + destaque(-1) + ["", "💼 SUA CARTEIRA B3"]
    for t, a in cart["ativos"].items():
        if t in dados:
            v = a["qtd"] * dados[t]["preco"]
            total += v
            L.append(f"  {t} {pct(var.get(t, 0))} | {brl(v, 0)} ({pct(v / a['base'] - 1)} s/ custo)")
    for t, s in est["swings"].items():
        if t in dados:
            total += s["qtd"] * dados[t]["preco"]
            L.append(f"  swing {t} {pct(dados[t]['preco'] / s['pm'] - 1)} desde a entrada")
    ini = cart.get("capital_inicial", total)
    L.append(f"  reserva {brl(cart['reserva_brl'], 0)} | TOTAL {brl(total, 0)} ({pct(total / ini - 1)})")
    if INDICE in dados:
        i = dados[INDICE]
        L.append(f"  Ibovespa hoje {pct(i['preco'] / i['ontem'] - 1)} | "
                 f"{'✅ acima' if indice_ok else '⚠️ abaixo'} da media de 200 dias")
    v = est["vendas_mes"]
    L.append(f"  🧾 vendas de acoes no mes: {brl(v['acoes'], 0)} de {brl(ISENCAO, 0)} isentos"
             + (f" | BDR: {brl(v['bdr'], 0)}" if v["bdr"] else ""))
    if est.get("darf"):
        L.append(f"  🧾 DARF pendente: {brl(est['darf']['ir'])} ate "
                 f"{pd.Timestamp(est['darf']['vence']):%d/%m}")
    if em_pausa(est):
        L.append(f"  ⏸️ em pausa ate {pd.Timestamp(est['pausa_ate']):%d/%m}")
    L.append(f"  universo: {len(acoes)} acoes ({fonte})")
    L += nucleo.aviso_balancos(set(cart["ativos"]) | set(est["swings"]), ".SA")
    if datetime.now(SP).weekday() == 4:
        L += consolidado(total, total_usd)
    nucleo.enviar_foto("mapa_b3.png", f"Mapa B3 {dia:%d/%m} - cor = variacao do dia")
    nucleo.enviar("\n".join(L), silencioso=True)


def consolidado(total_brl, total_usd):
    """Sextas: patrimonio somado em R$ (EUA convertido pelo dolar do dia)."""
    if not total_usd:
        return []
    cambio = nucleo.baixar(["BRL=X"], periodo="6mo").get("BRL=X")
    if cambio is None:
        return []
    dolar = float(cambio["Close"].iloc[-1])
    eua = total_usd * dolar
    return ["", "🌎 PATRIMONIO DA SEMANA",
            f"  EUA {brl(eua, 0)} (US$ {num(total_usd, 0)} x {brl(dolar)})",
            f"  B3 {brl(total_brl, 0)}",
            f"  TOTAL {brl(eua + total_brl, 0)}"]


# ================================================================== RODAR
def rodar(eventos, modo, agora, total_usd=None):
    cart, est = carregar()
    d = hoje()
    for aviso in avisos_darf(est):
        nucleo.enviar(aviso)
    fazer_var = modo in ("tudo", "varredura") or (
        modo == "auto" and agora.hour >= HORA_VARREDURA and est.get("varredura_em") != d)
    fazer_fech = modo in ("tudo", "fechamento") or (
        modo == "auto" and agora.hour >= HORA_FECHAMENTO and est.get("fechamento_em") != d)

    univ, fonte = universo(cart) if (fazer_var or fazer_fech) else ({}, "")
    alvo = set(cart["ativos"]) | set(est["swings"]) | set(univ)
    brutos_sa = nucleo.baixar([f"{t}.SA" for t in alvo] + [INDICE])
    brutos = {k.replace(".SA", ""): v for k, v in brutos_sa.items()}
    dados = {t: nucleo.indicadores(df) for t, df in brutos.items()}
    precos = {t: x["preco"] for t, x in dados.items()}
    pregoes = brutos[INDICE].index if INDICE in brutos else None
    pregao_hoje = INDICE in dados and pd.Timestamp(dados[INDICE]["data"]).normalize() == pd.Timestamp(d)
    indice_ok = INDICE in dados and dados[INDICE]["preco"] > dados[INDICE]["sma200"]

    limite = str((pd.Timestamp(d) - pd.Timedelta(days=2)).date())
    est["pendentes"] = {k: v for k, v in est["pendentes"].items() if v["data"] >= limite}
    processar_eventos(cart, est, eventos, precos)

    if not est.get("validado"):
        avisos = [f"  {t}: sem dados - confirme o ticker" if t not in dados else
                  f"  {t}: {brl(dados[t]['preco'])} x print {brl(a['preco_print'])}"
                  for t, a in cart["ativos"].items()
                  if t not in dados or abs(dados[t]["preco"] / a["preco_print"] - 1) > 0.15]
        nucleo.enviar("🇧🇷 Parte B3 ligada.\n" + ("⚠️ Confira:\n" + "\n".join(avisos) if avisos else
                      f"✅ As {len(cart['ativos'])} acoes bateram com o print.")
                      + ("\n💡 Reserva B3 em R$ 0: mande /reserva_br VALOR para liberar swings"
                         if cart["reserva_brl"] <= 0 else ""))
        est["validado"] = True

    if pregao_hoje or modo != "auto":
        antes = {"carteira": copy.deepcopy(cart), "swings": copy.deepcopy(est["swings"]),
                 "vendas_mes": copy.deepcopy(est["vendas_mes"]),
                 "adiadas": copy.deepcopy(est.get("adiadas", {}))}
        ordens, notas = [], {}
        # Realizacao so com a bolsa aberta: depois do fechamento voce so
        # conseguiria vender no dia seguinte, a outro preco.
        em_pregao = modo != "auto" or 10 <= datetime.now(SP).hour <= 17
        if em_pregao:
            monitor(cart, est, dados, ordens)
        if fazer_var:
            notas = varredura(cart, est, univ, dados, indice_ok, ordens, pregoes)
            est["varredura_em"] = d
        if ordens:
            ordens = [o if len(o) == 3 else (o[0], o[1], None) for o in ordens]
            est["ultima"] = {**antes, "chaves": [o[0] for o in ordens]}
            normais = [txt for _, txt, x in ordens if x is None]
            if normais:
                nucleo.enviar("🇧🇷 B3\n" + "\n\n".join(normais) +
                              "\n\nNao executou? Responda /nao_br que eu desfaco.")
            for chave, txt, x in ordens:
                if x is not None:
                    mid = nucleo.enviar(txt, botao=("❌ Recusar", f"rb3|{chave}"))
                    est["pendentes"][chave] = {**x, "msg_id": mid, "data": d}
        if fazer_fech:
            if not notas:
                rs = pd.Series({t: x["ret63"] for t, x in dados.items() if not np.isnan(x["ret63"])})
                pc = rs.rank(pct=True)
                notas = {t: nucleo.nota_tendencia(x, pc.get(t, 0)) for t, x in dados.items()
                         if t != INDICE and not np.isnan(x["sma200"])}
            fechamento(cart, est, univ, dados, notas, indice_ok, fonte, total_usd)
            est["fechamento_em"] = d
    else:
        print("B3 sem pregao hoje.")
    salvar(cart, est)
