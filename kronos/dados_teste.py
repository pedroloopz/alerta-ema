"""Base de dados para testar regras sobre o Kronos-small (agentes de teste). Só leitura, sem chave.

Para cada ativo do universo do prever.py: ~1 ano de partidas, uma a cada 46h (passa por todas as horas do dia),
8 caminhos por partida. Grava, por partida, o resumo dos caminhos (o mesmo do robô), as faixas dos caminhos hora
a hora e as 24 velas reais seguintes — tudo em % do último fechamento. Assim qualquer regra de entrada/saída pode
ser testada depois sem rodar o modelo de novo.

  python kronos/dados_teste.py lista <feitos.txt>   -> saida/universo.json só com os ativos que faltam
  python kronos/dados_teste.py rodar <i> <n>        -> saida/dados/<simbolo>.json.gz (ativos i, i+n, ...)

Workflow: kronos-dados.yml (grava na branch kronos-dados; retoma de onde parou).
"""
import gzip
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prever as P  # noqa: E402

PASSO = int(os.environ.get("PASSO", 46))     # horas entre partidas (os 5 primeiros ativos saíram com 23)
DIAS = int(os.environ.get("DIAS", 365))
N = int(os.environ.get("N_TESTE", 8))
ORCAMENTO_MIN = float(os.environ.get("ORCAMENTO_MIN", 330))   # não começa ativo novo se não der tempo
BLOCO = 40                                   # partidas por passada do modelo
QS = (0.1, 0.25, 0.5, 0.75, 0.9)


def lista(feitos_txt):
    P.lista()
    feitos = set(open(feitos_txt).read().split()) if os.path.exists(feitos_txt) else set()
    p = os.path.join(P.SAIDA, "universo.json")
    ativos = [a for a in json.load(open(p)) if a["simbolo"] not in feitos]
    json.dump(ativos, open(p, "w"), ensure_ascii=False)
    P.log(f"faltam {len(ativos)} ativos (já feitos: {len(feitos)})")


def _r(x, c=5):
    return np.round(np.asarray(x, dtype=float), c).tolist()


def partida(cam, fut, ult, t, hist):
    fe, mi, ma = cam[:, :, 3] / ult - 1, cam[:, :, 2] / ult - 1, cam[:, :, 1] / ult - 1
    rz = P.resumo_caminhos(cam, ult)
    return {"t": int(t), "ult": float(ult),
            "p_alta": round(rz["p_alta"], 4), "ret_mediano": round(rz["ret_mediano"], 5),
            "ret_q": {k: round(v, 5) for k, v in rz["ret_q"].items()},
            "queda_q15": round(rz["queda_q15"], 5), "alta_q15": round(rz["alta_q15"], 5),
            "recuo": {l: {k: round(v, 5) for k, v in d.items()} for l, d in rz["recuo"].items()},
            "corrida": {l: {k: round(v, 4) for k, v in d.items()} for l, d in rz["corrida"].items()},
            "faixa_fech": [_r(np.quantile(fe, q, axis=0)) for q in QS],     # 5 x 24 (p10..p90 por hora)
            "faixa_min": _r(np.quantile(mi, 0.25, axis=0)), "faixa_max": _r(np.quantile(ma, 0.75, axis=0)),
            "reais": [_r(v / ult - 1) for v in fut],                          # 24 x [abre, máx, mín, fecha]
            "atr_1h": round(float((hist["high"] - hist["low"]).tail(48).mean() / ult), 5),
            "ret_24h_antes": round(float(ult / hist["close"].iloc[-25] - 1), 5),
            "vol_24h_usd": round(float((hist["volume"] * hist["close"]).tail(24).sum()), 0)}


def rodar(i, n_partes):
    t_ini = time.time()
    ativos = json.load(open(os.path.join(P.SAIDA, "universo.json")))[i::n_partes]
    destino = os.path.join(P.SAIDA, "dados")
    os.makedirs(destino, exist_ok=True)
    motor, ultimo_custo = None, 0.0
    for a in ativos:
        gasto = (time.time() - t_ini) / 60
        if gasto + 1.1 * ultimo_custo > ORCAMENTO_MIN:
            P.log(f"sem tempo para {a['ativo']} ({gasto:.0f} min usados); fica para a próxima rodada")
            break
        t0 = time.time()
        try:
            df = P.velas(a["simbolo"], P.CTX + DIAS * 24 + P.H)
        except Exception as e:
            P.log(f"{a['ativo']}: sem dados ({e})")
            continue
        origens = list(range(len(df) - P.H, P.CTX - 1, -PASSO))[: DIAS * 24 // PASSO][::-1]
        saida = []
        if len(origens) < 30:
            P.log(f"{a['ativo']}: só {len(origens)} partidas, pulo")
            origens = []                     # grava vazio para não tentar de novo a cada rodada
        elif motor is None:
            motor = P.carregar_modelo()
        for k in range(0, len(origens), BLOCO):
            bl = origens[k:k + BLOCO]
            sa = P.amostrar(motor, [df.iloc[o - P.CTX:o].reset_index(drop=True) for o in bl], N)
            for o, cam in zip(bl, sa):
                ult = float(df["close"].iloc[o - 1])
                fut = df.iloc[o:o + P.H][["open", "high", "low", "close"]].values
                saida.append(partida(cam, fut, ult, df["t"].iloc[o - 1], df.iloc[o - 48:o]))
        nome = os.path.join(destino, a["simbolo"] + ".json.gz")
        with gzip.open(nome, "wt") as f:
            json.dump({**a, "passo_h": PASSO, "n_caminhos": N, "custo": P.CUSTO, "partidas": saida}, f)
        ultimo_custo = (time.time() - t0) / 60
        P.log(f"{a['ativo']}: {len(saida)} partidas em {ultimo_custo:.0f} min")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "lista":
        lista(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "rodar":
        rodar(int(sys.argv[2]), int(sys.argv[3]))
