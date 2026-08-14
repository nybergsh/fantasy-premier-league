"""Three honest baselines for the M3 backtest harness.

Every later component decision (M4+) gets judged against these numbers, so they have to be
simple enough to trust by inspection (tools/fpl_xp_model_spec_v1.1.md, M3). Each function
matches the `predict(player_fixtures, asof) -> pl.DataFrame` contract (spec S4.1): every row of
`player_fixtures` comes back with an `xp` column, built only from rows with
`kickoff_time < asof`. Backtesting and live projection are the same call.

Player identity across seasons must key on `code`, never `element` — see known-issues.md #8.
`GW` is the join grain for double-gameweek rows: two fixtures in the same gameweek get the same
per-fixture prediction, so their sum (the gameweek xP, S1.5) rises for a double gameweek rather
than being split.
"""

from datetime import datetime

import numpy as np
import polars as pl

LAG_WINDOW = 3
TRAILING_MINUTES_WINDOW = 5


def _gameweek_totals(player_fixtures: pl.DataFrame) -> pl.DataFrame:
    """Collapse fixture grain to (season, code, GW), summing across double gameweeks."""
    return player_fixtures.group_by("season", "code", "GW").agg(
        pl.col("kickoff_time").min(),
        pl.col("total_points").sum(),
        pl.col("minutes").sum(),
    )


def _season_at(player_fixtures: pl.DataFrame, asof: datetime) -> str:
    """The season of the next fixture at or after `asof` — the one being walked forward."""
    return (
        player_fixtures.filter(pl.col("kickoff_time") >= asof)
        .sort("kickoff_time")
        .get_column("season")
        .head(1)
        .item()
    )


def baseline_positional_mean(
    player_fixtures: pl.DataFrame, asof: datetime, availability: pl.DataFrame | None = None
) -> pl.DataFrame:
    """The floor: expanding mean gameweek points for the player's position, any season."""
    history = _gameweek_totals(player_fixtures).filter(pl.col("kickoff_time") < asof)
    positions = player_fixtures.select("code", "GW", "season", "position").unique()
    means = (
        history.join(positions, on=("code", "GW", "season"))
        .group_by("position")
        .agg(pl.col("total_points").mean().alias("xp"))
    )
    return player_fixtures.join(means, on="position", how="left").with_columns(
        pl.col("xp").fill_null(0.0)
    )


def baseline_minutes_times_ppg(
    player_fixtures: pl.DataFrame, asof: datetime, availability: pl.DataFrame | None = None
) -> pl.DataFrame:
    """The real bar: trailing mean minutes x season-to-date points per 90.

    The one baseline that decomposes into a minutes half and a rate half. Both are kept as
    columns (`xp_minutes_component`, `xp_rate_component`) so evaluate.py's sensitivity
    decomposition can hold one fixed and vary the other.
    """
    history = _gameweek_totals(player_fixtures).filter(pl.col("kickoff_time") < asof)

    trailing_minutes = (
        history.sort("kickoff_time")
        .group_by("code")
        .agg(pl.col("minutes").tail(TRAILING_MINUTES_WINDOW).mean().alias("xp_minutes_component"))
    )
    points_per_90 = (
        history.group_by("season", "code")
        .agg(pl.col("total_points").sum(), pl.col("minutes").sum())
        .filter(pl.col("minutes") > 0)
        .select(
            "season",
            "code",
            xp_rate_component=pl.col("total_points") / pl.col("minutes") * 90,
        )
    )
    return (
        player_fixtures.join(trailing_minutes, on="code", how="left")
        .join(points_per_90, on=("season", "code"), how="left")
        .with_columns(
            pl.col("xp_minutes_component").fill_null(0.0),
            pl.col("xp_rate_component").fill_null(0.0),
        )
        .with_columns(xp=pl.col("xp_minutes_component") / 90 * pl.col("xp_rate_component"))
    )


def baseline_lag_regression(
    player_fixtures: pl.DataFrame, asof: datetime, availability: pl.DataFrame | None = None
) -> pl.DataFrame:
    """OLS on the player's own last LAG_WINDOW gameweek points, fit on the target season only.

    `total_points` isn't comparable across scoring-rule changes, so training uses only rows
    from the season containing `asof` (spec S8, M3). Missing lags (early gameweeks) are filled
    with 0 rather than dropped, so the model runs from GW1 with a documented cold start rather
    than raising.
    """
    season = _season_at(player_fixtures, asof)
    gw = _gameweek_totals(player_fixtures).filter(pl.col("season") == season).sort("GW")
    lag_columns = [f"lag_{lag}" for lag in range(1, LAG_WINDOW + 1)]
    lagged = gw.with_columns(
        pl.col("total_points").shift(lag).over("code").fill_null(0.0).alias(name)
        for lag, name in zip(range(1, LAG_WINDOW + 1), lag_columns)
    )

    train = lagged.filter(pl.col("kickoff_time") < asof)
    design = np.column_stack([np.ones(lagged.height), lagged.select(lag_columns).to_numpy()])
    if train.height > len(lag_columns) + 1:
        train_design = np.column_stack(
            [np.ones(train.height), train.select(lag_columns).to_numpy()]
        )
        coefficients, *_ = np.linalg.lstsq(
            train_design, train["total_points"].to_numpy(), rcond=None
        )
        xp = design @ coefficients
    else:
        xp = np.zeros(lagged.height)

    predictions = lagged.select("season", "code", "GW").with_columns(pl.Series("xp", xp))
    return player_fixtures.join(predictions, on=("season", "code", "GW"), how="left")
