import datetime
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kronos"))
import bt4h as B  # noqa: E402

SEG = int(datetime.datetime(2026, 10, 26, 0, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)   # segunda


def partida(t, p_alta=1.0, reais=None, recuo=0.01, q15=0.02):
    return {"t": t, "ult": 100.0, "p_alta": p_alta, "ret_mediano": 0.02 if p_alta >= 0.5 else -0.02,
            "queda_q15": q15, "alta_q15": q15, "recuo": {"LONG": {"q50": recuo}, "SHORT": {"q50": recuo}},
            "reais": reais or [[0, 0.001, -0.012, -0.005]] + [[0, 0.01, -0.005, 0.01]] * 23}


def ativo(tipo="cripto", partidas=()):
    return {"ativo": "BTC", "simbolo": "BTC-USDT", "tipo": tipo, "partidas": list(partidas),
            "_f_ts": [], "_f_ac": [0.0]}


def test_long_entra_no_recuo_e_sai_no_prazo():
    d = ativo(partidas=[partida(SEG)])
    x = B.trade(d, d["partidas"][0], B.PRINCIPAL)
    sl = 0.02 * 1.5
    assert x["como"] == "prazo" and abs(x["r"] - ((1.01 / 0.99 - 1) - B.CUSTO) / sl) < 1e-3


def test_stop_e_filtro_de_100():
    queda = [[0, 0.001, -0.012, -0.005]] + [[0, 0.0, -0.05, -0.04]] + [[0, 0.01, -0.005, 0.01]] * 22
    d = ativo(partidas=[partida(SEG, reais=queda)])
    x = B.trade(d, d["partidas"][0], B.PRINCIPAL)
    assert x["como"] == "stop" and abs(x["r"] + 1 + B.CUSTO / 0.03) < 1e-3
    assert B.trade(d, partida(SEG, p_alta=0.95), B.PRINCIPAL) is None            # 95% fica de fora no principal
    assert B.trade(d, partida(SEG, p_alta=0.95), dict(B.PRINCIPAL, p_min=0.9))


def test_acao_sai_antes_do_fim_de_semana_e_nao_entra_perto():
    qui = SEG + 3 * 86400_000                                                    # quinta 00h UTC
    d = ativo("ação", [partida(qui)])
    x = B.trade(d, d["partidas"][0], B.PRINCIPAL)
    assert x["como"] == "fds" and x["fim"] <= B.corte_fds(qui)
    sex = SEG + 4 * 86400_000                                                    # sexta 00h: a menos de 24h do corte
    assert B.trade(d, partida(sex), B.PRINCIPAL) is None
    assert B.trade(ativo("cripto"), partida(sex), B.PRINCIPAL)                   # cripto atravessa


def test_um_por_ativo_e_relatorio(tmp_path):
    ps = [partida(SEG + i * 52 * 3600_000) for i in range(12)]
    os.makedirs(tmp_path / "dados")
    with gzip.open(tmp_path / "dados" / "BTC-USDT.json.gz", "wt") as f:
        json.dump({k: v for k, v in ativo(partidas=ps).items() if not k.startswith("_")}, f)
    B.principal(str(tmp_path), str(tmp_path / "out.json"))
    out = json.load(open(tmp_path / "out.json"))
    assert out["principal"]["n"] == 6                                            # 96h > 52h: pula um a cada dois
    assert out["previsiveis"] == ["BTC"] and len(out["variantes"]) == 16
