"""Ajuste à mão (26/10/2026): Pedro fechou o EWZ long; sai do vigia de futuros."""
import json

with open("perps.json", encoding="utf-8") as f:
    p = json.load(f)
saiu = p.get("posicoes", {}).pop("EWZ", None)
with open("perps.json", "w", encoding="utf-8") as f:
    json.dump(p, f, indent=2, ensure_ascii=False)
try:
    with open("estado_perps.json", encoding="utf-8") as f:
        e = json.load(f)
    e.get("avisados", {}).pop("EWZ", None)
    with open("estado_perps.json", "w", encoding="utf-8") as f:
        json.dump(e, f, indent=2, ensure_ascii=False)
except FileNotFoundError:
    pass
print("EWZ removido" if saiu else "EWZ já não estava no perps.json")
