import datetime
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "painel"))
import swing  # noqa: E402

UTC = datetime.timezone.utc


def test_plano_deixa_caixa():
    p = swing.plano()
    assert p["caixa"] >= swing.BANCA * swing.CAIXA_MIN - 1
    assert abs(p["parcela"] * 3 * swing.VAGAS - swing.EXPOSICAO) < 1


def test_acao_1h_so_pregao():
    # terça 13/10/2026: pregão 9:30–16:00 NY = 13:30–20:00 UTC; candles de 15 min de 12h a 22h UTC
    idx = pd.date_range("2026-10-13 12:15", "2026-10-13 22:00", freq="15min", tz="UTC")
    df = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": np.arange(len(idx), dtype=float),
                       "volume": 1.0}, index=idx)
    h = swing.acao_1h(df)
    assert len(h) == 7
    assert h.index[0] == pd.Timestamp("2026-10-13 14:30", tz="UTC")
    assert h.index[-1] == pd.Timestamp("2026-10-13 20:00", tz="UTC")
    assert h["volume"].iloc[0] == 4 and h["volume"].iloc[-1] == 2      # último é meio candle


def test_hora_de_checar():
    assert swing.hora_de_checar("cripto", datetime.datetime(2026, 10, 14, 15, 1, tzinfo=UTC))
    assert not swing.hora_de_checar("cripto", datetime.datetime(2026, 10, 14, 15, 31, tzinfo=UTC))
    assert swing.hora_de_checar("acao", datetime.datetime(2026, 10, 14, 14, 31, tzinfo=UTC))   # 10:31 NY
    assert not swing.hora_de_checar("acao", datetime.datetime(2026, 10, 14, 14, 50, tzinfo=UTC))
    assert not swing.hora_de_checar("acao", datetime.datetime(2026, 10, 17, 14, 31, tzinfo=UTC))  # sábado


def test_prazo_acao_pula_fim_de_semana():
    t = datetime.datetime(2026, 10, 16, 20, 0, tzinfo=UTC)      # sexta, fechamento
    pz = swing.prazo("acao", t, 3)
    assert pz.astimezone(swing.NY).date() == datetime.date(2026, 10, 21)   # 3 pregões: seg, ter, qua
    assert swing.prazo("cripto", t, 3) == t + datetime.timedelta(hours=72)


def test_texto():
    x = {"ativo": "NVDA", "classe": "acao", "parcelas": [100.0, 99.5, 99.0], "stop": 97.0, "alvo": 106.0,
         "stop_pct": 2.51, "alvo_pct": 7.54, "prazo": "2026-10-21T20:00:00+00:00", "tempo": "x", "simbolo": "s"}
    t = swing.texto([x])
    assert "NVDA" in t and "Stop 97" in t and "Alvo 106" in t and "21/10 17:00" in t
