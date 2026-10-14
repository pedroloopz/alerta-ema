"""Temporario: roda o swing no universo inteiro ignorando o horario, para conferir dados e sinais."""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import dados  # noqa: E402
import swing  # noqa: E402

agora = datetime.datetime.now(datetime.timezone.utc)
uni = json.load(open(os.path.join(os.path.dirname(__file__), "stats.json")))["universo"]
print("universo:", len(uni), "acoes:", sum(a["classe"] == "acao" for a in uni))
swing.hora_de_checar = lambda c, t: True
swing.JANELA_MIN = 60 * 24 * 7          # aceita o ultimo candle de 1h mesmo que antigo
ok = {"acao": 0, "cripto": 0}
for a in uni[:400]:
    try:
        df = swing.cripto_1h(a["simbolo"]) if a["classe"] == "cripto" else swing.acao_1h(dados.klines(a["simbolo"], dias=21))
        if df is not None and len(df) >= 60:
            ok[a["classe"]] += 1
            if ok[a["classe"]] <= 2:
                print(a["simbolo"], len(df), df.index[-1], df.tail(2).to_dict("records"))
    except Exception as e:
        print("falhou", a["simbolo"], e)
print("com dados suficientes:", ok)
# sinais no ultimo candle de cada ativo (normalmente poucos ou nenhum)
s = swing.varrer(uni, agora)
print("sinais agora:", len(s))
# mostra a mensagem com o primeiro sinal real das ultimas 300 horas de um ativo, para ver o texto
import backtest_swing as bs  # noqa: E402
for a in uni:
    if a["classe"] != "acao":
        continue
    df = swing.acao_1h(dados.klines(a["simbolo"], dias=21))
    if df is None or len(df) < 60:
        continue
    atr, sig = bs.preparar(df)
    if len(sig):
        corte = df.iloc[: sig[-1] + 1]
        swing.cripto_1h = lambda s, c=corte: c
        a2 = dict(a, classe="cripto")
        x = swing.avaliar(a2, corte.index[-1].to_pydatetime())
        if x:
            x["classe"] = "acao"
            print(swing.texto([x]))
            break
print(json.dumps(swing.plano()))
