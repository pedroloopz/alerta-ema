"""Scanner de 15 min do painel de day trade.

Lê painel/stats.json (calibração diária), varre o universo no candle de 15 min que acabou
de fechar, dá nota 1–10 a cada sinal e grava painel/painel.json. Se os segredos do Google
existirem, atualiza o arquivo no Drive que o painel do Claude lê.

Ajustes ao vivo (não calibrados, só para pior): funding muito alto contra o lado do trade
-3 pontos percentuais; long/short ratio de contas muito lotado do mesmo lado -2 p.p.

Uso:
    python painel/scanner.py             # varre e grava
    python painel/scanner.py --max 30    # ensaio rápido
"""
import argparse
import datetime
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import dados  # noqa: E402
import nucleo as nc  # noqa: E402

AQUI = os.path.dirname(__file__)
VALIDADE_MIN = 45          # sinal vale até 45 min depois do candle
DESLIZE_MAX = 0.5          # preço andou mais de 0,5 ATR desde o sinal -> perdeu a entrada


def br(dt):
    return dt.astimezone(datetime.timezone(datetime.timedelta(hours=-3)))


def avaliar(ativo, stats, fundings):
    try:
        df = dados.klines(ativo["simbolo"], limite=400)
        if df is None or len(df) < 250:
            return []
        df = nc.preparar(df)
        sig = nc.sinais(df)
        sig = sig[sig["i"] >= len(df) - 2]          # só os 2 últimos candles fechados
        out = []
        ultimo = float(df["close"].iloc[-1])
        for s in sig.itertuples():
            saida = stats["saidas"].get(f"{ativo['classe']}|{s.setup}")
            if not saida:
                continue
            e = nc.estimar(stats, ativo["classe"], ativo["simbolo"], s.setup, s.lado, s.alinhado, s.tempo.hour)
            if not e:
                continue
            ent, a = float(df["close"].iloc[s.i]), float(df["atr"].iloc[s.i])
            if abs(ultimo - ent) > DESLIZE_MAX * a:
                continue
            ajustes = []
            p = e["p"]
            f = fundings.get(ativo["simbolo"])
            if f is not None and s.lado * f > 0.0005:
                p -= 0.03; ajustes.append("funding contra")
            ls = None
            if ativo["classe"] == "cripto":
                ls = dados.long_short(ativo["nome"])
                if ls and ((s.lado == 1 and ls["ls"] > 2.5) or (s.lado == -1 and ls["ls"] < 0.7)):
                    p -= 0.02; ajustes.append("lado lotado")
            sl_pct = saida["sl"] * a / ent
            tp_pct = saida["tp"] * a / ent
            casas = max(2, -int(np.floor(np.log10(ent))) + 4)
            out.append({
                "ativo": ativo["nome"], "simbolo": ativo["simbolo"], "classe": ativo["classe"],
                "rank": ativo.get("rank"), "setup": s.setup, "lado": "LONG" if s.lado == 1 else "SHORT",
                "alinhado": bool(s.alinhado), "tempo": s.tempo.isoformat(),
                "nota": nc.nota_de(p, e["ev_r"], e["n_base"]),
                "p_tp": round(p, 3), "ev_r": round(e["ev_r"], 3), "n_ativo": e["n_ativo"],
                "entrada": round(ent, casas), "preco": round(ultimo, casas),
                "sl": round(ent - s.lado * saida["sl"] * a, casas),
                "tp": round(ent + s.lado * saida["tp"] * a, casas),
                "sl_pct": round(sl_pct * 100, 2), "tp_pct": round(tp_pct * 100, 2),
                "alav": nc.alavancagem(sl_pct), "funding": f, "ls": ls, "ajustes": ajustes,
            })
        return out
    except Exception as e:
        print(f"falhou {ativo['simbolo']}: {e}", flush=True)
        return []


def subir_drive(caminho):
    info, fid = os.environ.get("GDRIVE_SA_JSON"), os.environ.get("GDRIVE_FILE_ID")
    if not info or not fid:
        print("Drive: segredos ausentes, não enviei")
        return
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    cred = service_account.Credentials.from_service_account_info(
        json.loads(info), scopes=["https://www.googleapis.com/auth/drive"])
    with open(caminho, "rb") as f:
        r = AuthorizedSession(cred).patch(
            f"https://www.googleapis.com/upload/drive/v3/files/{fid}?uploadType=media",
            data=f.read(), headers={"Content-Type": "application/json"}, timeout=60)
    r.raise_for_status()
    print("Drive: atualizado")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    a = ap.parse_args()
    t0 = time.time()
    agora = datetime.datetime.now(datetime.timezone.utc)
    with open(os.path.join(AQUI, "stats.json")) as f:
        stats = json.load(f)
    uni = stats["universo"][: a.max or None]
    fundings = dados.funding()
    sinais = [x for lst in dados.em_paralelo(lambda at: avaliar(at, stats, fundings), uni, n=8) for x in lst]
    sinais.sort(key=lambda x: (-x["nota"], -x["p_tp"], -x["ev_r"]))

    sessao = stats.get("sessao_acao", {}).get(f"{agora.weekday()}|{agora.hour}")
    base = []
    for k, v in stats["base"].items():
        classe, setup, lado, alin = k.split("|")
        if v["n"] >= 50:
            base.append({"classe": classe, "setup": setup, "lado": "LONG" if lado == "1" else "SHORT",
                         "alinhado": alin == "1", "n": v["n"], "acerto": round(v["g"] / v["n"], 3),
                         "r_medio": round(v["r"] / v["n"], 3)})
    base.sort(key=lambda x: -x["r_medio"])
    painel = {
        "gerado_em": agora.isoformat(timespec="seconds"),
        "gerado_em_brt": br(agora).strftime("%d/%m %H:%M"),
        "validade_min": VALIDADE_MIN,
        "ativos_varridos": len(uni),
        "sessao_acoes": sessao,
        "calibrado_em": stats["gerado_em"],
        "periodo": stats["periodo"],
        "validacao": stats["validacao"],
        "saidas": stats["saidas"],
        "setups_melhores": base[:5],
        "setups_piores": base[-5:][::-1],
        "sinais": sinais,
        "segundos": round(time.time() - t0),
    }
    caminho = os.path.join(AQUI, "painel.json")
    with open(caminho, "w") as f:
        json.dump(painel, f, ensure_ascii=False, separators=(",", ":"))
    n8 = sum(1 for x in sinais if x["nota"] >= 8)
    print(f"{len(sinais)} sinais ({n8} com nota 8+) em {len(uni)} ativos, {painel['segundos']}s")
    for x in sinais[:10]:
        print(f"  {x['nota']}/10 {x['lado']:5} {x['ativo']:10} {x['setup']} p={x['p_tp']} ev={x['ev_r']}R alav {x['alav']}x")
    subir_drive(caminho)


if __name__ == "__main__":
    main()
