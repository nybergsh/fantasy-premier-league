"""Metric tests. Every scored frame is hand-built; expected values are computed by inspection,
never by calling the metric under test.
"""

import polars as pl
import pytest

from fpl.metrics import (
    block_bootstrap_ci,
    calibration_by_position,
    captain_regret,
    mae_rmse,
    metric_ci,
    sensitivity_decomposition,
    spearman_60plus,
    top_k_overlap,
)


def scored_row(code, gw, xp, actual, minutes=90, position="MID", season="2025-26", **overrides):
    row = {
        "season": season,
        "code": code,
        "GW": gw,
        "xp": xp,
        "actual": actual,
        "minutes": minutes,
        "position": position,
    }
    return row | overrides


def frame(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows))


def test_mae_rmse_on_known_errors():
    scored = frame(
        scored_row(1, 1, xp=5.0, actual=3.0),
        scored_row(2, 1, xp=1.0, actual=5.0),
    )
    result = mae_rmse(scored)
    # errors are +2 and -4: MAE = (2 + 4) / 2 = 3; RMSE = sqrt((4 + 16) / 2) = sqrt(10)
    assert result["mae"] == pytest.approx(3.0)
    assert result["rmse"] == pytest.approx(10**0.5)


def test_spearman_60plus_ignores_players_under_sixty_minutes():
    scored = frame(
        scored_row(1, 1, xp=1.0, actual=1.0, minutes=90),
        scored_row(2, 1, xp=2.0, actual=2.0, minutes=90),
        scored_row(3, 1, xp=3.0, actual=3.0, minutes=90),
        # perfectly anti-correlated, but under 60 minutes, so must not pull the average down
        scored_row(4, 1, xp=1.0, actual=100.0, minutes=10),
    )
    assert spearman_60plus(scored) == pytest.approx(1.0)


def test_spearman_60plus_ignores_a_tied_gameweek_instead_of_returning_nan():
    # xp is identical for every player in GW1 (e.g. a positional-mean baseline) — correlation
    # is undefined (NaN) there. GW2 has a real ranking. NaN must not poison the overall mean.
    scored = frame(
        scored_row(1, 1, xp=2.0, actual=1.0),
        scored_row(2, 1, xp=2.0, actual=2.0),
        scored_row(3, 1, xp=2.0, actual=3.0),
        scored_row(1, 2, xp=1.0, actual=1.0),
        scored_row(2, 2, xp=2.0, actual=2.0),
        scored_row(3, 2, xp=3.0, actual=3.0),
    )
    assert spearman_60plus(scored) == pytest.approx(1.0)


def test_captain_regret_is_the_gap_to_the_gameweek_max():
    scored = frame(
        scored_row(1, 1, xp=10.0, actual=8.0),  # model's top pick this GW
        scored_row(2, 1, xp=1.0, actual=15.0),  # the actual best score, not picked
    )
    assert captain_regret(scored) == pytest.approx(8.0 - 15.0)


def test_top_k_overlap_full_and_zero():
    scored = frame(*(scored_row(i, 1, xp=float(i), actual=float(i)) for i in range(1, 11)))
    from fpl.metrics import TOP_K

    assert TOP_K == (10, 30)
    overlaps = top_k_overlap(scored)
    assert overlaps[10] == pytest.approx(1.0)  # identical ranking, 10-of-10 in the pool


def test_calibration_groups_by_position_and_bin():
    scored = frame(
        *(scored_row(i, 1, xp=float(i), actual=float(i), position="DEF") for i in range(1, 11)),
        *(scored_row(i + 100, 1, xp=1.0, actual=99.0, position="FWD") for i in range(1, 11)),
    )
    table = calibration_by_position(scored, n_bins=2)
    assert set(table["position"].unique()) == {"DEF", "FWD"}
    fwd_actual = table.filter(pl.col("position") == "FWD")["mean_actual"]
    assert (fwd_actual == 99.0).all()


def test_metric_ci_contains_the_point_estimate():
    scored = frame(
        *(scored_row(1, gw, xp=float(gw), actual=float(gw) + 1) for gw in range(1, 6)),
    )
    point, lo, hi = metric_ci(scored, "mae", seed=1)
    assert point == pytest.approx(1.0)
    assert lo <= point <= hi


def test_block_bootstrap_ci_is_zero_width_for_identical_frames():
    scored = frame(*(scored_row(1, gw, xp=float(gw), actual=float(gw) + 1) for gw in range(1, 6)))
    point, lo, hi = block_bootstrap_ci(scored, scored, "mae", seed=1)
    assert point == pytest.approx(0.0)
    assert lo == pytest.approx(0.0)
    assert hi == pytest.approx(0.0)


def test_sensitivity_decomposition_shares_sum_to_one():
    # xp_minutes_component == true minutes for every row, so holding the rate at its realised
    # value and varying only "minutes" reproduces `actual` exactly (rho = 1, zero rank error).
    # xp_rate_component is deliberately the reverse rank of the true rate, so varying only
    # "rate" is perfectly anti-correlated (rho = -1). All the rank error is attributable to
    # the rate half by construction.
    scored = frame(
        *(
            scored_row(
                i,
                1,
                xp=float(i),
                actual=float(i),
                minutes=90,
                xp_minutes_component=90.0,
                xp_rate_component=float(11 - i),
            )
            for i in range(1, 11)
        ),
    )
    shares = sensitivity_decomposition(scored)
    assert shares["minutes_share"] == pytest.approx(0.0)
    assert shares["rate_share"] == pytest.approx(1.0)
