"""Acorda o robô (repositório privado robo-bingx) quando aparece um long novo do modelo de fluxo.

Roda no loop do painel-scanner, depois de gravar a coleta (o robô lê o histórico público dela).
No máximo 8 despertares por dia (horário de Brasília), para o robô caber nos 2.000 minutos grátis;
o resto dos sinais é pego pelo horário fixo do robô (a cada 30 min).
Segredo ROBO_TOKEN: token do GitHub só com "Actions: read and write" no robo-bingx. Sem ele, não faz nada.
"""
import csv
import datetime
import json
import os
import urllib.request

AQUI = os.path.dirname(__file__)
COLETA = os.path.join(AQUI, "..", "coleta")
HIST = os.path.join(COLETA, "historico_8.csv")
ESTADO = os.path.join(COLETA, "robo_acordado.json")
URL = "https://api.github.com/repos/pedroloopz/robo-bingx/actions/workflows/operar.yml/dispatches"
MAX_DIA = 8


def main():
    token = os.environ.get("ROBO_TOKEN")
    if not token or not os.path.exists(HIST):
        return
    with open(HIST, encoding="utf-8") as f:
        sinais = [r for r in csv.DictReader(f) if r.get("rotulo") == "Modelo de fluxo" and r.get("lado") == "LONG"]
    if not sinais:
        return
    est = {}
    if os.path.exists(ESTADO):
        with open(ESTADO, encoding="utf-8") as f:
            est = json.load(f)
    hoje = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)).date().isoformat()
    if est.get("dia") != hoje:
        est = {"dia": hoje, "n": 0, "ultimo": est.get("ultimo")}
    ultimo = max(s["tempo"] for s in sinais)
    if ultimo == est.get("ultimo") or est["n"] >= MAX_DIA:
        return
    req = urllib.request.Request(URL, method="POST", data=json.dumps({"ref": "main", "inputs": {"motivo": "sinal"}}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
        est.update(n=est["n"] + 1, ultimo=ultimo)
        print(f"robô acordado ({est['n']}/{MAX_DIA} hoje)")
    except Exception as e:
        print(f"robô: não consegui acordar ({type(e).__name__})")
    with open(ESTADO, "w", encoding="utf-8") as f:
        json.dump(est, f)


if __name__ == "__main__":
    main()
