import json

import pytest

import cofre


def chave(x):
    return cofre.senha(json.dumps({"private_key": x}))


def test_ida_e_volta_e_chave_errada(tmp_path):
    (tmp_path / "carteira.json").write_text('{"capital_inicial": 7087.88}', encoding="utf-8")
    assert cofre.fechar(chave("a"), tmp_path) == 1
    assert b"7087" not in (tmp_path / "carteira.json.enc").read_bytes()
    assert cofre.fechar(chave("a"), tmp_path) == 0          # sem mudança, sem regravar
    (tmp_path / "carteira.json").unlink()
    assert cofre.abrir(chave("a"), tmp_path) == 1
    assert json.loads((tmp_path / "carteira.json").read_text())["capital_inicial"] == 7087.88
    with pytest.raises(cofre.SemChave):
        cofre.abrir(chave("b"), tmp_path)


def test_sem_segredo_nao_abre(monkeypatch):
    monkeypatch.delenv("GDRIVE_SA_JSON", raising=False)
    with pytest.raises(cofre.SemChave):
        cofre.senha()
