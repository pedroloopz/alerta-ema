"""Sobe um arquivo para o Google Drive (atualiza um arquivo que já existe e está compartilhado com a conta de serviço).

  GDRIVE_SA_JSON=... python kronos/subir_drive.py <arquivo> <id do arquivo no Drive>
"""
import json
import os
import sys


def subir(caminho, fid):
    info = os.environ.get("GDRIVE_SA_JSON")
    if not info:
        print("Drive: segredo ausente, não enviei")
        return
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    cred = service_account.Credentials.from_service_account_info(json.loads(info), scopes=["https://www.googleapis.com/auth/drive"])
    with open(caminho, "rb") as f:
        r = AuthorizedSession(cred).patch(f"https://www.googleapis.com/upload/drive/v3/files/{fid}?uploadType=media",
                                         data=f.read(), headers={"Content-Type": "application/json"}, timeout=120)
    r.raise_for_status()
    print(f"Drive: {os.path.basename(caminho)} atualizado ({os.path.getsize(caminho) // 1024} KB)")


if __name__ == "__main__":
    subir(sys.argv[1], sys.argv[2])
