import carteiras as c

H = c.HORA


def f(t, side, sz, px, antes, pnl=0.0):
    return {"time": t, "side": side, "sz": str(sz), "px": str(px), "startPosition": str(antes),
            "closedPnl": str(pnl), "fee": "0", "crossed": True}


def test_long_simples_com_saida_parcial():
    ts = c.reconstruir([f(0, "B", 2, 100, 0), f(H, "A", 1, 110, 2, 10), f(2 * H, "A", 1, 120, 1, 20)])
    assert len(ts) == 1
    t = ts[0]
    assert t["lado"] == 1 and abs(t["p_sai"] - 115) < 1e-9 and abs(t["ret"] - 0.15) < 1e-9
    assert t["pnl"] == 30 and t["horas"] == 2


def test_ignora_trade_ja_aberto_e_vira_a_mao():
    ts = c.reconstruir([f(0, "A", 1, 100, 3),                 # já estava aberto: ignora
                        f(H, "A", 2, 100, 2),                 # zera
                        f(2 * H, "A", 1, 100, 0),             # abre short
                        f(3 * H, "B", 2, 90, -1, 10),         # fecha short e abre long
                        f(4 * H, "A", 1, 99, 1, 9)])
    assert [t["lado"] for t in ts] == [-1, 1]
    assert abs(ts[0]["ret"] - 0.10) < 1e-9 and abs(ts[1]["ret"] - 0.10) < 1e-9


def test_copia_entra_no_candle_seguinte():
    cs = [{"t": i * H, "o": 100 + i, "h": 0, "l": 0, "c": 0, "v": 0} for i in range(5)]
    r = c.copiar({"lado": 1, "t_ent": H // 2, "t_sai": 3 * H + 1}, cs)
    assert abs(r - ((104 / 101 - 1) - c.TAXA)) < 1e-12
    assert c.copiar({"lado": 1, "t_ent": H + 1, "t_sai": H + 2}, cs) is None
