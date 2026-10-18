"""Acorda o robô (repositório privado robo-bingx) a cada 30 min.

Roda no loop do painel-scanner (de 15 em 15 min). O horário fixo do GitHub atrasa muito em repositório
novo, então quem garante os 30 min é este despertar; o robô tem um horário de reserva a cada 4h.
~48 despertares por dia ≈ 1.490 min/mês, dentro dos 2.000 grátis.
Segredo ROBO_TOKEN: token do GitHub só com "Actions: read and write" no robo-bingx. Sem ele, não faz nada.
"""
import datetime
import json
import os
import urllib.request

AQUI = os.path.dirname(__file__)
ESTADO = os.path.join(AQUI, "..", "coleta", "robo_acordado.json")
URL = "https://api.github.com/repos/pedroloopz/robo-bingx/actions/workflows/operar.yml/dispatches"
INTERVALO_MIN = 28                    # 30 min com folga para o relógio do loop


def main():
    token = os.environ.get("ROBO_TOKEN")
    if not token:
        return
    est = {}
    if os.path.exists(ESTADO):
        with open(ESTADO, encoding="utf-8") as f:
            est = json.load(f)
    agora = datetime.datetime.now(datetime.timezone.utc)
    ultimo = est.get("em")
    if ultimo and agora - datetime.datetime.fromisoformat(ultimo) < datetime.timedelta(minutes=INTERVALO_MIN):
        return
    req = urllib.request.Request(URL, method="POST", data=json.dumps({"ref": "main", "inputs": {"motivo": "30min"}}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
        est["em"] = agora.isoformat(timespec="seconds")
        print("robô acordado")
    except Exception as e:
        print(f"robô: não consegui acordar ({type(e).__name__})")
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    with open(ESTADO, "w", encoding="utf-8") as f:
        json.dump(est, f)


if __name__ == "__main__":
    main()
