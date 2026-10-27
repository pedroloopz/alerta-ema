"""Cofre: guarda criptografados os arquivos com posições e capital (o repositório é público).

No repositório só ficam as versões `.enc`. O workflow abre o cofre antes de rodar o bot
(gera os arquivos normais, que o .gitignore não deixa subir) e fecha depois (regrava os `.enc`).

    python cofre.py abrir     # .enc -> arquivos normais (falha se a chave não abrir)
    python cofre.py fechar    # arquivos normais -> .enc (só regrava o que mudou)

Chave: derivada do segredo GDRIVE_SA_JSON (a mesma conta de serviço do painel); nunca vai para
o código nem para o log. Se um dia trocar a chave da conta de serviço, rode antes o workflow
`cofre-trocar` (abre com a chave antiga e fecha com a nova).
Criptografia: openssl AES-256-CBC com PBKDF2 (já vem no Ubuntu do GitHub; sem pip).
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys

AQUI = pathlib.Path(__file__).resolve().parent
MARCA = b"COFRE1\n"   # confere que a chave abriu certo (sem ela, lixo poderia passar por arquivo válido)

# Arquivos com posição, tamanho, margem, saldo ou resultado em dinheiro
SENSIVEIS = [
    "carteira.json", "carteira_b3.json", "perps.json",
    "estado_bot.json", "estado_b3.json", "estado_perps.json", "estado_rotacao.json",
    "carteira_antiga.json", "estado_bot_antigo.json",
    "estado.json", "sinais.csv",
    "backups/carteira_ate_06-10-2026.json", "backups/carteira_b3_ate_06-10-2026.json",
    "backups/estado_b3_ate_06-10-2026.json", "backups/estado_bot_ate_06-10-2026.json",
]


class SemChave(RuntimeError):
    pass


def senha(fonte=None):
    """Senha do cofre a partir do GDRIVE_SA_JSON (só a chave privada; o resto do JSON pode mudar de formato)."""
    bruto = fonte if fonte is not None else os.environ.get("GDRIVE_SA_JSON", "")
    if not bruto.strip():
        raise SemChave("GDRIVE_SA_JSON ausente: o cofre não abre nem fecha sem ele")
    try:
        base = json.loads(bruto)["private_key"]
    except Exception:
        raise SemChave("GDRIVE_SA_JSON sem private_key")
    return hashlib.sha256(b"alerta-ema/cofre/v1\n" + base.encode()).hexdigest()


def _openssl(args, dados, chave):
    env = {**os.environ, "COFRE_SENHA": chave}
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-md", "sha256",
                        "-pass", "env:COFRE_SENHA", *args], input=dados, capture_output=True, env=env)
    if r.returncode != 0:
        raise SemChave("openssl recusou (chave errada ou arquivo corrompido)")
    return r.stdout


def cifrar(dados: bytes, chave: str) -> bytes:
    return _openssl(["-salt"], MARCA + dados, chave)


def decifrar(cifrado: bytes, chave: str) -> bytes:
    claro = _openssl(["-d"], cifrado, chave)
    if not claro.startswith(MARCA):
        raise SemChave("a chave não confere com este arquivo")
    return claro[len(MARCA):]


def abrir(chave=None, raiz=AQUI):
    chave = chave or senha()
    n = 0
    for nome in SENSIVEIS:
        enc = raiz / (nome + ".enc")
        if enc.exists():
            alvo = raiz / nome
            alvo.parent.mkdir(parents=True, exist_ok=True)
            alvo.write_bytes(decifrar(enc.read_bytes(), chave))
            n += 1
    print(f"cofre: {n} arquivo(s) aberto(s)")
    return n


def fechar(chave=None, raiz=AQUI):
    chave = chave or senha()
    n = 0
    for nome in SENSIVEIS:
        claro, enc = raiz / nome, raiz / (nome + ".enc")
        if not claro.exists():
            continue
        dados = claro.read_bytes()
        if enc.exists():
            try:
                if decifrar(enc.read_bytes(), chave) == dados:
                    continue                      # nada mudou: não gera commit à toa
            except SemChave:
                pass
        enc.write_bytes(cifrar(dados, chave))
        n += 1
    print(f"cofre: {n} arquivo(s) regravado(s)")
    return n


def abrir_pasta(pasta, chave):
    """Branch central: troca cada *.enc da pasta (e de arquivos/) pela versão aberta."""
    pasta = pathlib.Path(pasta)
    for enc in list(pasta.glob("*.enc")) + list(pasta.glob("arquivos/*.enc")):
        enc.with_suffix("").write_bytes(decifrar(enc.read_bytes(), chave))
        enc.unlink()


def fechar_pasta(pasta, chave):
    """Branch central: troca saida.jsonl e os anexos de arquivos/ pelas versões *.enc."""
    pasta = pathlib.Path(pasta)
    for claro in [pasta / "saida.jsonl", *(pasta / "arquivos").glob("*")]:
        if claro.is_file() and claro.suffix != ".enc":
            claro.with_name(claro.name + ".enc").write_bytes(cifrar(claro.read_bytes(), chave))
            claro.unlink()


def trocar(antiga_fonte, raiz=AQUI):
    """Reabre com a chave antiga (GDRIVE_SA_JSON_ANTIGO) e fecha com a atual."""
    abrir(senha(antiga_fonte), raiz)
    for nome in SENSIVEIS:
        enc = raiz / (nome + ".enc")
        if enc.exists():
            enc.unlink()
    fechar(senha(), raiz)


if __name__ == "__main__":
    acao = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if acao == "abrir":
            abrir()
        elif acao == "fechar":
            fechar()
        elif acao == "trocar":
            trocar(os.environ.get("GDRIVE_SA_JSON_ANTIGO", ""))
        else:
            print(__doc__)
            sys.exit(2)
    except SemChave as e:
        print(f"cofre: {e}")
        sys.exit(1)
