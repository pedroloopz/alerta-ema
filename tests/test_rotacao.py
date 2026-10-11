"""Rotacao por forca relativa (M3) em simulacao."""
import numpy as np
import pandas as pd
import pytest

import rotacao


def serie(dias, inicio, passo, vol=5e6):
    c = inicio * np.cumprod(np.full(len(dias), 1 + passo))
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": vol}, index=dias)


@pytest.fixture
def mundo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dias = pd.bdate_range(end="2026-10-08", periods=260)
    dados = {f"T{i}": serie(dias, 100, 0.0002 * i) for i in range(12)}   # T11 sobe mais
    dados["SPY"] = serie(dias, 500, 0.0005, vol=1e8)
    univ = {t: {"tipo": "acao"} for t in dados if t != "SPY"}
    msgs = []

    def enviar(texto, botao, silencioso):
        msgs.append((texto, botao, silencioso))
        return len(msgs)
    return dados, univ, msgs, enviar


def avancar(dados, dia, mult=1.0):
    """Acrescenta um pregao; mult mexe no preco de abertura/fechamento do dia."""
    out = {}
    for t, df in dados.items():
        ult = float(df["Close"].iloc[-1]) * mult
        out[t] = pd.concat([df, pd.DataFrame({"Open": ult, "High": ult, "Low": ult, "Close": ult,
                                              "Volume": df["Volume"].iloc[-1]}, index=[pd.Timestamp(dia)])])
    return out


def test_rebalanceia_executa_e_recusa(mundo):
    dados, univ, msgs, enviar = mundo
    est = rotacao.rodar(dados, univ, "2026-10-08", enviar)
    compras = [o["t"] for o in est["pendentes"] if o["tipo"] == "compra"]
    assert len(compras) == 7 and "T11" in compras and "T0" not in compras
    m = msgs[0]
    assert "SIMULADO" in m[0] and "nota" in m[0] and m[1][1].startswith("rot|")
    assert "126" not in m[0] and "forca" not in m[0].lower()        # so a nota, sem criterio
    apagadas = []
    rotacao.recusar(compras[0], 1, apagadas.append)                 # botao Recusar
    est = rotacao.carregar()
    assert compras[0] not in [o["t"] for o in est["pendentes"]] and apagadas == [1]
    est = rotacao.rodar(avancar(dados, "2026-10-09"), univ, "2026-10-09", enviar)
    assert len(est["posicoes"]) == 6 and compras[0] not in est["posicoes"]
    assert est["caixa"] == pytest.approx(rotacao.CAPITAL / 7, rel=0.01)


def test_regra_dos_10_porcento(mundo):
    dados, univ, msgs, enviar = mundo
    rotacao.rodar(dados, univ, "2026-10-08", enviar)
    d2 = avancar(dados, "2026-10-09")
    est = rotacao.rodar(d2, univ, "2026-10-09", enviar)
    caixa = est["caixa"]
    msgs.clear()
    est = rotacao.rodar(avancar(d2, "2026-10-12", mult=1.15), univ, "2026-10-12", enviar)
    assert est["caixa"] > caixa + 500                               # excesso dos 7 foi para o caixa
    assert sum("realize" in m[0] for m in msgs) == 7
    for p in est["posicoes"].values():
        assert p["n"] * p["ult"] == pytest.approx(p["base"], rel=1e-6)


def test_mercado_em_queda_fica_em_caixa(mundo):
    dados, univ, msgs, enviar = mundo
    spy = dados["SPY"]
    spy["Close"] = spy["Close"].values[::-1]                        # SPY abaixo da media de 200
    est = rotacao.rodar(dados, univ, "2026-10-08", enviar)
    assert est["pendentes"] == [] and est["caixa"] == rotacao.CAPITAL


def test_resumo_semanal_na_sexta(mundo):
    dados, univ, msgs, enviar = mundo
    rotacao.rodar(dados, univ, "2026-10-08", enviar)               # quinta (inicio)
    d = avancar(dados, "2026-10-09")
    rotacao.rodar(d, univ, "2026-10-09", enviar)                   # sexta seguinte: cedo demais
    assert not [m for m in msgs if "RESUMO SEMANAL" in m[0]]
    for dia in ("2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"):
        d = avancar(d, dia)
        rotacao.rodar(d, univ, dia, enviar)
    resumo = [m for m in msgs if "RESUMO SEMANAL" in m[0]]
    assert len(resumo) == 1 and resumo[0][2] is True and "Resultado simulado" in resumo[0][0]


def test_nota_tem_casa_decimal_e_simulado_sem_som(mundo):
    dados, univ, msgs, enviar = mundo
    rotacao.rodar(dados, univ, "2026-10-08", enviar)
    notas = [m[0].split("nota ")[1].split("/")[0] for m in msgs if "nota" in m[0]]
    assert all("," in n for n in notas) and len(set(notas)) > 1
    assert all(m[2] is True for m in msgs)
