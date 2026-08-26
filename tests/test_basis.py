from arbitrage.strategies.basis import carry_nets


def test_rich_perp_is_cash_and_carry_after_fees():
    metrics = carry_nets(
        spot_px=100,
        perp_px=101.5,
        funding_rate=0.0001,
        spot_fee=0.001,
        perp_fee=0.0004,
        buffer_bps=3,
        funding_days=1,
    )
    # basis = 150 bps, fees = 14 bps, 1d funding = 3*0.0001*10000 = 3 bps
    assert metrics["basis_bps"] == 150
    assert metrics["carry_net_bps"] > 100
    assert metrics["carry_net_bps"] > metrics["reverse_net_bps"]


def test_cheap_perp_is_reverse_carry():
    metrics = carry_nets(
        spot_px=100,
        perp_px=98,
        funding_rate=0.0,
        spot_fee=0.001,
        perp_fee=0.0004,
        buffer_bps=3,
    )
    assert metrics["reverse_net_bps"] > 0
    assert metrics["reverse_net_bps"] > metrics["carry_net_bps"]


def test_tight_basis_is_not_an_edge_after_fees():
    metrics = carry_nets(
        spot_px=2452.44,
        perp_px=2451.35,
        funding_rate=0.000077,
        spot_fee=0.001,
        perp_fee=0.0004,
        buffer_bps=3,
    )
    assert metrics["carry_net_bps"] < 0
    assert metrics["reverse_net_bps"] < 12
