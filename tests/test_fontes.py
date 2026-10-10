"""Fontes com reserva (FASE 2): falha da principal cai na reserva, sem quebrar."""
import pandas as pd

import fontes


class Resp:
    def __init__(self, dados, status=200):
        self.dados, self.status_code = dados, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self.dados


def test_cripto_ccxt_falha_usa_rest(monkeypatch, capsys):
    def quebra():
        raise RuntimeError("451 restricted location")
    monkeypatch.setattr(fontes, "_binance_ccxt", quebra)
    linha = [1791590400000, "0.70", "0.72", "0.70", "0.71", "100", 0]
    monkeypatch.setattr(fontes.requests, "get", lambda *a, **k: Resp([linha]))
    df = fontes.cripto("ASTERUSDT", "1d", 5)
    assert float(df["Close"].iloc[-1]) == 0.71
    assert "usando REST direto" in capsys.readouterr().out


def test_cripto_ccxt_ok(monkeypatch):
    class Ex:
        def fetch_ohlcv(self, simbolo, tf, since=None, limit=None):
            assert simbolo == "ASTER/USDT"
            return [[1791590400000, 1, 2, 0.5, 1.5, 10]]
    monkeypatch.setattr(fontes, "_binance_ccxt", lambda: Ex())
    assert float(fontes.cripto("ASTERUSDT")["Close"].iloc[-1]) == 1.5


def test_acoes_reserva_yahoo_direto(monkeypatch, capsys):
    idx = pd.date_range("2026-01-01", periods=80)
    df = pd.DataFrame({c: 1.0 for c in fontes.COLUNAS}, index=idx)
    monkeypatch.setattr(fontes, "yahoo_direto", lambda t, p: df if t == "NFLX" else (_ for _ in ()).throw(KeyError(t)))
    saida = fontes.completar_acoes({"SPY": df}, ["SPY", "NFLX", "XXXX"])
    assert set(saida) == {"SPY", "NFLX"}
    assert "recuperou 1" in capsys.readouterr().out
