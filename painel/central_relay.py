"""Junta o que os bots gravaram (branch `central` de cada repositório) e manda para o Drive,
de onde a página Central no Claude lê.

- Feed: central-feed.json (últimos 3 dias, imagens embutidas e reduzidas).
- Áudios: os 12 mais recentes vão para os arquivos audio-01..12.mp3 da pasta "Central (bots)".
Roda no workflow painel-central a cada 15 min. Usa a mesma conta de serviço do painel.
"""
import base64
import datetime
import io
import json
import os
import pathlib
import shutil
import subprocess

REPOS = ["pedroloopz/alerta-ema", "pedroloopz/Jornal-DE-JP", "pedroloopz/diario-cultural"]
FEED_ID = "12EmX3rQd0CZg1c0NQ0GgUVvu5k2ByoGa"
AUDIOS = ["14AhIxyM6oUB33O9L-rkE8rkDI_Gbup9h", "1BC1Jpd28VcJJKvzsdsdvV0Rg93QnZ3kJ",
          "1vpFRaxplUL3Ke31nHwPlF6yaxDYhiIMK", "1Yz1KJEwZLlMY8vxa_4h1i39lEiqRu2KD",
          "1y_H-6MNeFRBpGVM7P82gCYOXIWMuC1fJ", "1feZVHhYmLWYrWMcRefh4qa6tCPKtJeO_",
          "1jsFO2QdFmQy0wvC88OSm7YnoTnhZ72MD", "1cEb4lHtimQbxn_sErMtk730wl3OrY27y",
          "157eMKTBqM8_ACvCAIoka9ep_CIpqU5gM", "1NYvm0pRYPT2zs65J84Yz_AoZr1EMsz8B",
          "16tdaEFm5ARDGICS6qZEOGEhPavcJwT-t", "1fXZ5WjOPGaNX_8lpeCh0O8WUEOc1VfEQ"]
DIAS = 3
TMP = pathlib.Path("/tmp/central_relay")


def sessao():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    cred = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["GDRIVE_SA_JSON"]), scopes=["https://www.googleapis.com/auth/drive"])
    return AuthorizedSession(cred)


def subir(s, fid, dados, tipo):
    r = s.patch(f"https://www.googleapis.com/upload/drive/v3/files/{fid}?uploadType=media",
                data=dados, headers={"Content-Type": tipo}, timeout=120)
    r.raise_for_status()


def baixar(s, fid):
    r = s.get(f"https://www.googleapis.com/drive/v3/files/{fid}?alt=media", timeout=60)
    r.raise_for_status()
    return r.content


def miniatura(caminho):
    from PIL import Image
    im = Image.open(caminho).convert("RGB")
    im.thumbnail((720, 720))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=72)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True)
    itens, pastas, diag = [], {}, {}
    # o alerta-ema é privado: usa a credencial que o actions/checkout deixou no repositório
    cab = subprocess.run(["git", "config", "--get", "http.https://github.com/.extraheader"],
                         capture_output=True, text=True).stdout.strip()
    auth = ["-c", f"http.https://github.com/.extraheader={cab}"] if cab else []
    for repo in REPOS:
        destino = TMP / repo.split("/")[1]
        proprio = repo == os.environ.get("GITHUB_REPOSITORY")
        r = subprocess.run(["git", *(auth if proprio else []), "clone", "-q", "--depth", "1", "-b", "central",
                            f"https://github.com/{repo}", str(destino)], capture_output=True, text=True)
        if r.returncode != 0 or not (destino / "saida.jsonl").exists():
            print(f"{repo}: ainda sem branch central")
            diag[repo] = (r.stderr or "sem saida.jsonl").strip()[-200:]
            continue
        diag[repo] = "ok"
        for linha in (destino / "saida.jsonl").read_text(encoding="utf-8").splitlines():
            if linha.strip():
                i = json.loads(linha)
                i["repo"] = repo.split("/")[1]
                itens.append(i)
        pastas[repo.split("/")[1]] = destino / "arquivos"
    corte = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DIAS)).isoformat()
    itens = sorted((i for i in itens if i["t"] >= corte), key=lambda i: i["t"], reverse=True)[:200]

    s = sessao()
    try:
        antigo = json.loads(baixar(s, FEED_ID))
    except Exception:
        antigo = {}
    slots = antigo.get("slots", {})          # arquivo de áudio -> índice do slot no Drive
    audios = [(i, a) for i in itens for a in i.get("anexos", []) if a["arquivo"].endswith(".mp3")][:len(AUDIOS)]
    queridos = {a["arquivo"] for _, a in audios}
    slots = {k: v for k, v in slots.items() if k in queridos}
    livres = [n for n in range(len(AUDIOS)) if n not in slots.values()]
    for i, a in audios:
        if a["arquivo"] not in slots:
            n = livres.pop(0)
            subir(s, AUDIOS[n], (pastas[i["repo"]] / a["arquivo"]).read_bytes(), "audio/mpeg")
            slots[a["arquivo"]] = n
            print(f"áudio {a['arquivo']} -> slot {n + 1}")
    for i in itens:
        for a in i.get("anexos", []):
            nome = a["arquivo"]
            if nome.endswith(".mp3"):
                a["drive_id"] = AUDIOS[slots[nome]] if nome in slots else None
            elif nome.rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "webp"):
                try:
                    a["imagem"] = miniatura(pastas[i["repo"]] / nome)
                except Exception as e:
                    print(f"imagem {nome} falhou: {e}")
    feed = {"gerado_em": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "itens": itens, "slots": slots, "diag": diag}
    subir(s, FEED_ID, json.dumps(feed, ensure_ascii=False).encode(), "application/json")
    print(f"feed: {len(itens)} itens, {len(slots)} áudios")


if __name__ == "__main__":
    main()
