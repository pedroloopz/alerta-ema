"""Testes do bot EUA com dados sinteticos (sem internet, sem Telegram)."""
import json
import os

import numpy as np
import pandas as pd
import pytest

import bot


# ------------------------------------------------ montagem do cenario
def _serie(dias, base, tendencia=0.0, choque=None):
    n = len(dias)
    c = base * (1 + tendencia) ** np.arange(n)
    if choque:
        for i, fator in choque.items():
            c[i:] = c[i:] * fator
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * 1.005
    l = np.minimum(o, c) * 0.995
    return o, h, l, c


def cenario(tmp_path, monkeypatch, series, carteira, cripto=None):
    """series: {ticker: (o, h, l, c)} sobre os pregoes de 2025-06 a 2026-09-25."""
    n = len(next(iter(series.values()))[3])
    dias = pd.bdate_range("2025-06-02", periods=n)
    dados = tmp_path / "dados"
    dados.mkdir()
    for k, pos in (("open", 0), ("high", 1), ("low", 2), ("close", 3)):
        df = pd.DataFrame({t: s[pos] for t, s in series.items()}, index=dias)
        df.to_csv(dados / f"yahoo_{k}.csv.gz")
    pd.DataFrame({t: 5e6 for t in series}, index=dias).to_csv(dados / "yahoo_volume.csv.gz")
    dias_c = pd.date_range("2025-06-01", "2026-09-27")
    for par, s in (cripto or {}).items():
        o, h, l, c = s
        pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1.0},
                     index=dias_c).to_csv(dados / f"binance_{par}.csv")
    univ = {"ativos": [{"ticker": t, "tipo": "acao", "token": f"{t}on", "token_alternativo": f"{t}B"}
                       for t in series if t not in ("SPY", "QQQ")]}
    if cripto and "ASTERUSDT" in cripto:
        univ["ativos"].append({"ticker": "ASTER", "tipo": "cripto", "token": "ASTER", "par": "ASTERUSDT",
                               "passo_quantidade": 0.01, "ordem_minima_usd": 5.0})
    (tmp_path / "universo_ondo.json").write_text(json.dumps(univ))
    (tmp_path / "carteira.json").write_text(json.dumps(carteira))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bot, "DADOS_LOCAIS", str(dados))
    bot._cache_local.clear()
    monkeypatch.setenv("TESTE_GRAVAR", "1")
    enviados = []
    monkeypatch.setattr(bot, "enviar", lambda txt, botao=None, silencioso=False: enviados.append((txt, botao, silencioso)) or 1)
    monkeypatch.setattr(bot, "enviar_foto", lambda c, leg, silencioso=False: enviados.append(("FOTO " + leg, None, silencioso)))
    monkeypatch.setattr(bot, "apagar", lambda mid: enviados.append((f"APAGAR {mid}", None, True)))
    return enviados, dias


def cart(ativos, caixa):
    return {"versao": 2, "inicio": "2026-09-01", "capital_inicial": 7000.0, "reserva_usdt": caixa,
            "aportes": [], "ativos": ativos}


def pos(t, un=10.0, px=100.0, tipo="acao", origem="legado"):
    return {"token": f"{t}on", "tipo": tipo, "unidades": un, "preco_entrada": px,
            "data_entrada": "2026-09-01", "origem": origem}


def rodar(quando, fases=(), comandos=""):
    os.environ["TESTE_COMANDOS"] = comandos
    agora = pd.Timestamp(quando).tz_localize("UTC").to_pydatetime()
    try:
        return bot.rodar_eua(agora, forcar=fases, auto=not fases)
    finally:
        os.environ["TESTE_COMANDOS"] = ""


def ler():
    return json.load(open("carteira.json")), json.load(open("estado_bot.json"))


def base_series(dias, extra=None):
    s = {"SPY": _serie(dias, 500, 0.001), "QQQ": _serie(dias, 400, 0.001)}
    for k in range(6):
        s[f"P{k}"] = _serie(dias, 50, 0.0)
    s.update(extra or {})
    return s


# ------------------------------------------------ migracao
def test_migracao_carteira(tmp_path, monkeypatch):
    antiga = {"inicio": "2026-09-25", "base_por_ativo": 1000, "reserva_usdt": 0, "capital_inicial": 7087.88,
              "ativos": {"UNH": {"token": "UNHon", "unidades": 2.63575, "preco_print": 377.07, "desde": "2026-09-25"},
                         "BE": {"token": "BEB", "unidades": 3.6663, "preco_print": 289.02, "desde": "2026-09-25"}}}
    (tmp_path / "carteira.json").write_text(json.dumps(antiga))
    (tmp_path / "estado_bot.json").write_text(json.dumps({"offset": 42, "swings": {"O": {}}}))
    (tmp_path / "universo_ondo.json").write_text(json.dumps({"ativos": []}))
    monkeypatch.chdir(tmp_path)
    bot.migrar()
    nova, est = ler()
    assert nova["versao"] == 2 and nova["reserva_usdt"] == 0 and nova["capital_inicial"] == 7087.88
    assert nova["ativos"]["UNH"] == {"token": "UNHon", "tipo": "acao", "unidades": 2.63575,
                                     "preco_entrada": 377.07, "data_entrada": "2026-09-25", "origem": "legado"}
    assert nova["ativos"]["BE"]["token"] == "BEB"
    assert json.load(open("carteira_antiga.json")) == antiga               # backup intacto
    assert est["offset"] == 42 and "swings" not in est                      # mantem o Telegram
    bot.migrar()                                                            # rodar de novo nao estraga
    assert ler()[0] == nova


# ------------------------------------------------ entrada
def _rompe(dias):
    o, h, l, c = _serie(dias, 50, 0.0)
    c[-1], h[-1] = 60.0, 60.5                                               # rompe no ultimo pregao
    return o, h, l, c


def test_entrada_no_rompimento(tmp_path, monkeypatch):
    series = None
    dias = pd.bdate_range("2025-06-02", "2026-09-25")
    series = base_series(dias, {"X": _rompe(dias)})
    ativos = {f"P{k}": pos(f"P{k}", 20, 50) for k in range(6)}
    env, _ = cenario(tmp_path, monkeypatch, series, cart(ativos, 1500.0))
    rodar("2026-09-25T15:15")                                               # pregao (11h15 NY)
    c, est = ler()
    assert "X" in c["ativos"] and c["ativos"]["X"]["origem"] == "donchian"
    x = c["ativos"]["X"]
    patr = 6 * 20 * 50 + 1500
    assert x["unidades"] == pytest.approx(round(patr / 7 / 60 - 0.00005, 4), abs=1e-4)
    assert x["unidades"] * 60 <= patr / 7 + 1e-6
    assert c["reserva_usdt"] == pytest.approx(1500 - x["unidades"] * 60, abs=0.01)
    ordem = [e for e in env if e[1]]
    assert len(ordem) == 1 and "COMPRAR X" in ordem[0][0] and ordem[0][1][0] == "❌ Recusar"
    assert "Xon (ou XB)" in ordem[0][0] and not ordem[0][2]                  # com som
    rodar("2026-09-25T16:15")                                               # mesma hora seguinte
    assert len([e for e in env if e[1]]) == 1                               # nao repete no dia


def test_sem_vaga_nao_entra(tmp_path, monkeypatch):
    dias = pd.bdate_range("2025-06-02", "2026-09-25")
    series = base_series(dias, {"X": _rompe(dias), "P6": _serie(dias, 50)})
    ativos = {f"P{k}": pos(f"P{k}", 20, 50) for k in range(7)}
    env, _ = cenario(tmp_path, monkeypatch, series, cart(ativos, 5000.0))
    rodar("2026-09-25T15:15")
    assert "X" not in ler()[0]["ativos"] and not env


def test_filtro_spy_bloqueia(tmp_path, monkeypatch):
    dias = pd.bdate_range("2025-06-02", "2026-09-25")
    series = base_series(dias, {"X": _rompe(dias)})
    series["SPY"] = _serie(dias, 500, -0.001)                               # SPY abaixo da MMA 200
    env, _ = cenario(tmp_path, monkeypatch, series, cart({}, 5000.0))
    rodar("2026-09-25T15:15")
    assert "X" not in ler()[0]["ativos"]


def test_ordem_minima_50(tmp_path, monkeypatch):
    dias = pd.bdate_range("2025-06-02", "2026-09-25")
    series = base_series(dias, {"X": _rompe(dias)})
    env, _ = cenario(tmp_path, monkeypatch, series, cart({}, 40.0))
    rodar("2026-09-25T15:15")
    assert "X" not in ler()[0]["ativos"] and not [e for e in env if e[1]]


def test_recusar_desfaz_e_silencia(tmp_path, monkeypatch):
    dias = pd.bdate_range("2025-06-02", "2026-09-25")
    series = base_series(dias, {"X": _rompe(dias)})
    env, _ = cenario(tmp_path, monkeypatch, series, cart({}, 7000.0))
    rodar("2026-09-25T15:15")
    c, est = ler()
    assert "X" in c["ativos"]
    rodar("2026-09-25T16:15", comandos="/recusar X")
    c, est = ler()
    assert "X" not in c["ativos"] and c["reserva_usdt"] == pytest.approx(7000.0)
    assert est["recusados"]["X"] == "2026-09-30"                             # 5 dias
    assert any(e[0].startswith("APAGAR") for e in env)


def test_pausa_bloqueia_entradas(tmp_path, monkeypatch):
    dias = pd.bdate_range("2025-06-02", "2026-09-25")
    series = base_series(dias, {"X": _rompe(dias)})
    env, _ = cenario(tmp_path, monkeypatch, series, cart({}, 7000.0))
    rodar("2026-09-25T15:15", comandos="/pausa 3")
    assert "X" not in ler()[0]["ativos"]


# ------------------------------------------------ saida
def test_saida_depois_do_fechamento_e_venda_na_abertura(tmp_path, monkeypatch):
    dias = pd.bdate_range("2025-06-02", "2026-09-28")
    o, h, l, c = _serie(dias, 50, 0.0)
    c[-2], l[-2] = 45.0, 44.8                                               # 25/09 fecha abaixo da minima
    o[-1] = 44.0                                                            # 28/09 abre a 44
    series = base_series(dias, {"Y": (o, h, l, c)})
    env, _ = cenario(tmp_path, monkeypatch, series, cart({"Y": pos("Y", 10, 50)}, 0.0))
    rodar("2026-09-25T20:15")                                               # depois do fechamento
    cart1, est = ler()
    assert cart1["ativos"]["Y"]["saida"]["data"] == "2026-09-25"
    msg = [e for e in env if "VENDER NA ABERTURA: Y" in e[0]]
    assert msg and "Vender 100% = 10 Yon" in msg[0][0] and not msg[0][2]
    assert any(e[0].startswith("FOTO") and e[2] for e in env)               # heatmap sem som
    rodar("2026-09-28T14:15")                                               # pregao seguinte
    cart2, est = ler()
    assert "Y" not in cart2["ativos"] and cart2["reserva_usdt"] == pytest.approx(440.0)
    assert est["historico"][-1]["preco_saida"] == 44.0
    rodar("2026-09-28T15:15", comandos="/vendi Y 10 43,50")                  # preco real
    cart3, est = ler()
    assert cart3["reserva_usdt"] == pytest.approx(435.0) and est["historico"][-1]["confirmado"]


def test_legado_so_sai_pela_regra(tmp_path, monkeypatch):
    dias = pd.bdate_range("2025-06-02", "2026-09-25")
    series = base_series(dias)
    ativos = {f"P{k}": pos(f"P{k}", 20, 80) for k in range(6)}             # todas no prejuizo
    env, _ = cenario(tmp_path, monkeypatch, series, cart(ativos, 0.0))
    rodar("2026-09-25T20:15")
    assert all("saida" not in a for a in ler()[0]["ativos"].values())
    assert not [e for e in env if "VENDER" in e[0]]


# ------------------------------------------------ comandos
def test_comprei_reserva_e_nao(tmp_path, monkeypatch):
    dias = pd.bdate_range("2025-06-02", "2026-09-25")
    env, _ = cenario(tmp_path, monkeypatch, base_series(dias), cart({}, 1000.0))
    rodar("2026-09-25T15:15", comandos="/reserva 1.500,00;/comprei P1 10 50,5")
    c, _ = ler()
    assert c["aportes"][-1]["valor"] == 500.0
    assert c["ativos"]["P1"]["unidades"] == 10 and c["reserva_usdt"] == pytest.approx(995.0)


# ------------------------------------------------ feriado e horario
def test_feriado_nao_opera(tmp_path, monkeypatch):
    dias = pd.bdate_range("2025-06-02", "2026-09-25")
    series = base_series(dias, {"X": _rompe(dias)})
    env, _ = cenario(tmp_path, monkeypatch, series, cart({}, 5000.0))
    rodar("2026-11-26T16:15")                                               # Acao de Gracas
    assert "X" not in ler()[0]["ativos"] and not env


def test_antes_da_abertura_nao_opera(tmp_path, monkeypatch):
    dias = pd.bdate_range("2025-06-02", "2026-09-25")
    series = base_series(dias, {"X": _rompe(dias)})
    env, _ = cenario(tmp_path, monkeypatch, series, cart({}, 5000.0))
    rodar("2026-09-25T13:15")                                               # 9h15 NY
    assert "X" not in ler()[0]["ativos"]


# ------------------------------------------------ ASTER
def _cripto_rompe(n):
    o, h, l, c = _serie(pd.RangeIndex(n), 1.0, 0.0)
    c[-2:], h[-2:], o[-2:], l[-2:] = 1.3, 1.31, 1.3, 1.29                   # fecha acima em 26/09
    return o, h, l, c


def test_aster_entra_uma_so_com_btc_ok(tmp_path, monkeypatch):
    dias = pd.bdate_range("2025-06-02", "2026-09-25")
    n = len(pd.date_range("2025-06-01", "2026-09-27"))
    cr = {"ASTERUSDT": _cripto_rompe(n), "BTCUSDT": _serie(pd.RangeIndex(n), 60000, 0.001)}
    env, _ = cenario(tmp_path, monkeypatch, base_series(dias), cart({}, 7000.0), cr)
    rodar("2026-09-27T00:05", fases=("cripto",))                            # 21h05 de 26/09 em Brasilia
    c, est = ler()
    a = c["ativos"]["ASTER"]
    assert a["tipo"] == "cripto" and a["unidades"] == pytest.approx(round(1000 / 1.3 - 0.005, 2))
    assert est["cripto_em"] == "2026-09-26"


def test_aster_sem_vaga_cripto(tmp_path, monkeypatch):
    dias = pd.bdate_range("2025-06-02", "2026-09-25")
    n = len(pd.date_range("2025-06-01", "2026-09-27"))
    cr = {"ASTERUSDT": _cripto_rompe(n), "BTCUSDT": _serie(pd.RangeIndex(n), 60000, -0.001)}
    env, _ = cenario(tmp_path, monkeypatch, base_series(dias), cart({}, 7000.0), cr)
    rodar("2026-09-27T00:05", fases=("cripto",))                            # BTC abaixo da MMA 200
    assert "ASTER" not in ler()[0]["ativos"]
