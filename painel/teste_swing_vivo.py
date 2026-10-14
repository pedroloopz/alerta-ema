"""Temporario: roda o swing no universo inteiro como o scanner faria, ignorando o horario."""
import collections
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import backtest_swing as bs  # noqa: E402
import swing  # noqa: E402

agora = datetime.datetime.now(datetime.timezone.utc)
uni = json.load(open(os.path.join(os.path.dirname(__file__), "stats.json")))["universo"]
swing.hora_de_checar = lambda c, t: True
swing.JANELA_MIN = 60 * 24 * 7
cont, falhas, exemplo = collections.Counter(), [0], []


def um(a):
    try:
        df = swing.cripto_1h(a["simbolo"]) if a["classe"] == "cripto" else swing.acao_1h_bx(a["simbolo"])
    except Exception as e:
        falhas[0] += 1
        print("falhou", a["simbolo"], e)
        return
    if df is None or len(df) < 60:
        cont[a["classe"] + " curto"] += 1
        return
    cont[a["classe"] + " ok"] += 1
    atr, sig = bs.preparar(df)
    cont[a["classe"] + " sinais"] += len(sig)
    if not exemplo and a["classe"] == "acao" and len(sig):
        exemplo.append((a, df.iloc[: sig[-1] + 1]))


t0 = time.time()
swing.dados.em_paralelo(um, uni, n=8)
print(f"{len(uni)} ativos em {time.time()-t0:.0f}s; falhas {falhas[0]}; {dict(cont)}")
s = swing.varrer(uni, agora)
print("sinais no ultimo candle:", len(s))
if exemplo:
    a, corte = exemplo[0]
    swing.acao_1h_bx = lambda simb, c=corte: c
    x = swing.avaliar(a, corte.index[-1].to_pydatetime())
    print(swing.texto([x]) if x else "exemplo nao gerou sinal")
print(json.dumps(swing.plano()))
