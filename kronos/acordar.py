"""Dispara o workflow kronos (rodada rápida) e o kronos-conferir a cada 4h, logo depois do fechamento da vela de 4h
(00h, 04h, 08h, 12h, 16h e 20h UTC). 29/10/2026: antes era de hora em hora (velas de 1h).

Roda no loop do painel-scanner (de 15 em 15 min), como o acordar_robo: o horário fixo (cron) desses
workflows não está disparando. Usa o GITHUB_TOKEN da própria execução (KRONOS_TOKEN). Sem ele, não faz nada.
"""
import datetime
import json
import os
import urllib.request

AQUI = os.path.dirname(__file__)
ESTADO = os.path.join(AQUI, "..", "coleta", "kronos_acordado.json")
BASE = "https://api.github.com/repos/pedroloopz/alerta-ema/actions/workflows/{}/dispatches"


def disparar(token, arquivo, inputs):
    req = urllib.request.Request(BASE.format(arquivo), method="POST",
                                 data=json.dumps({"ref": "main", "inputs": inputs}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    urllib.request.urlopen(req, timeout=15).read()


def main():
    token = os.environ.get("KRONOS_TOKEN")
    if not token:
        return
    est = json.load(open(ESTADO)) if os.path.exists(ESTADO) else {}
    agora = datetime.datetime.now(datetime.timezone.utc)
    bloco = agora.replace(hour=agora.hour - agora.hour % 4, minute=0, second=0, microsecond=0)
    if agora - bloco < datetime.timedelta(minutes=5):  # espera a vela de 4h fechar na BingX
        return
    if est.get("em") and datetime.datetime.fromisoformat(est["em"]) >= bloco:
        return
    try:
        disparar(token, "kronos.yml", {"rapida": "true"})
        disparar(token, "kronos-conferir.yml", {})
        est["em"] = agora.isoformat(timespec="seconds")
        print("kronos disparado")
    except Exception as e:
        print(f"kronos: não consegui disparar ({type(e).__name__})")
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    json.dump(est, open(ESTADO, "w"))


if __name__ == "__main__":
    main()
