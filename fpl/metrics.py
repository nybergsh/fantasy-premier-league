"""Backtest metrics, at gameweek grain (spec S8, M3). Each function takes a scored frame with
one row per (season, code, GW) carrying `xp` and `actual` (gameweek total points), and returns
a scalar or a small table. `fpl/evaluate.py` builds that frame and calls these.
"""

import random

import polars as pl

TOP_K = (10, 30)
BOOTSTRAP_SAMPLES = 1_000


def mae_rmse(scored: pl.DataFrame) -> dict[str, float]:
    error = pl.col("xp") - pl.col("actual")
    return scored.select(
        mae=error.abs().mean(),
        rmse=(error**2).mean().sqrt(),
    ).row(0, named=True)


def spearman_60plus(scored: pl.DataFrame) -> float:
    """Rank correlation within gameweek within position, restricted to players who played 60+.

    Unrestricted rank correlation is inflated by the trivial starter/non-starter split (spec
    S8, M3) — that's why this needs the position and minutes filters, not a season-wide corr.
    """
    per_group = (
        scored.filter(pl.col("minutes") >= 60)
        .group_by("season", "GW", "position")
        .agg(pl.corr("xp", "actual", method="spearman").alias("rho"))
        # a group where every prediction ties (e.g. a positional-mean baseline) gives NaN, not
        # null, and NaN silently poisons a plain mean() — drop both.
        .filter(pl.col("rho").is_not_nan() & pl.col("rho").is_not_null())
    )
    return per_group["rho"].mean()


def captain_regret(scored: pl.DataFrame) -> float:
    """Actual points of the model's per-gameweek top pick, minus that gameweek's actual max."""
    per_gw = scored.group_by("season", "GW").agg(
        pl.col("actual").get(pl.col("xp").arg_max()).alias("picked"),
        pl.col("actual").max().alias("best"),
    )
    return (per_gw["picked"] - per_gw["best"]).mean()


def top_k_overlap(scored: pl.DataFrame) -> dict[int, float]:
    """Fraction overlap between the predicted and actual top-k, per gameweek, averaged."""
    result = {}
    for k in TOP_K:
        per_gw = scored.group_by("season", "GW").agg(
            pl.col("code").sort_by("xp", descending=True).head(k).alias("predicted_top"),
            pl.col("code").sort_by("actual", descending=True).head(k).alias("actual_top"),
        )
        overlap = per_gw.select(
            (pl.col("predicted_top").list.set_intersection("actual_top").list.len() / k).mean()
        )
        result[k] = overlap.item()
    return result


def calibration_by_position(scored: pl.DataFrame, n_bins: int = 10) -> pl.DataFrame:
    """Mean actual points per predicted-xp decile, per position."""
    binned = scored.with_columns(
        pl.col("xp")
        .qcut(n_bins, labels=[str(i) for i in range(n_bins)], allow_duplicates=True)
        .over("position")
        .alias("bin")
    )
    return (
        binned.group_by("position", "bin")
        .agg(
            pl.col("xp").mean().alias("mean_predicted"),
            pl.col("actual").mean().alias("mean_actual"),
        )
        .sort("position", "bin")
    )


_METRIC_FNS = {"mae": lambda s: mae_rmse(s)["mae"], "captain_regret": captain_regret}


def _resample_keys(gameweeks: list, rng: random.Random) -> pl.DataFrame:
    sample = rng.choices(gameweeks, k=len(gameweeks))
    return pl.DataFrame(sample, schema=["season", "GW"], orient="row")


def metric_ci(scored: pl.DataFrame, metric: str, seed: int = 0) -> tuple[float, float, float]:
    """95% CI on a single metric, resampling gameweek blocks with replacement."""
    metric_fn = _METRIC_FNS[metric]
    gameweeks = scored.select("season", "GW").unique().rows()
    rng = random.Random(seed)
    values = sorted(
        metric_fn(scored.join(_resample_keys(gameweeks, rng), on=("season", "GW"), how="inner"))
        for _ in range(BOOTSTRAP_SAMPLES)
    )
    lo = values[int(0.025 * BOOTSTRAP_SAMPLES)]
    hi = values[int(0.975 * BOOTSTRAP_SAMPLES)]
    return metric_fn(scored), lo, hi


def block_bootstrap_ci(
    scored_a: pl.DataFrame,
    scored_b: pl.DataFrame,
    metric: str,
    seed: int = 0,
) -> tuple[float, float, float]:
    """95% CI on (metric(a) - metric(b)), resampling gameweek blocks with replacement.

    38 gameweeks is a small sample (spec S8, M3) — every model-vs-model comparison needs a CI,
    not a point estimate, or "better" numbers within noise get treated as findings.
    """
    metric_fn = _METRIC_FNS[metric]
    gameweeks = scored_a.select("season", "GW").unique().rows()
    rng = random.Random(seed)
    diffs = []
    for _ in range(BOOTSTRAP_SAMPLES):
        keys = _resample_keys(gameweeks, rng)
        a = scored_a.join(keys, on=("season", "GW"), how="inner")
        b = scored_b.join(keys, on=("season", "GW"), how="inner")
        diffs.append(metric_fn(a) - metric_fn(b))
    diffs.sort()
    lo = diffs[int(0.025 * BOOTSTRAP_SAMPLES)]
    hi = diffs[int(0.975 * BOOTSTRAP_SAMPLES)]
    point = metric_fn(scored_a) - metric_fn(scored_b)
    return point, lo, hi


def sensitivity_decomposition(components: pl.DataFrame) -> dict[str, float]:
    """Share of rank error from the minutes half vs the rate half of a decomposable model.

    `components` needs `xp_minutes_component`, `xp_rate_component`, `minutes`, `actual`. Holds
    one half at its actual (realised) value and varies the other, per spec S8, M3: everything
    multiplies through a minutes probability, so ranking is expected to be dominated by minutes
    certainty rather than rate quality — tested here, not assumed.
    """
    actual_rate = (
        pl.when(pl.col("minutes") > 0)
        .then(pl.col("actual") / pl.col("minutes") * 90)
        .otherwise(0.0)
    )
    varying_minutes = components.with_columns(
        counterfactual=pl.col("xp_minutes_component") / 90 * actual_rate
    )
    varying_rate = components.with_columns(
        counterfactual=pl.col("minutes") / 90 * pl.col("xp_rate_component")
    )

    def rank_error(frame: pl.DataFrame) -> float:
        per_group = (
            frame.group_by("season", "GW", "position")
            .agg(pl.corr("counterfactual", "actual", method="spearman").alias("rho"))
            .filter(pl.col("rho").is_not_nan() & pl.col("rho").is_not_null())
        )
        return 1 - per_group["rho"].mean()

    minutes_error = rank_error(varying_minutes)
    rate_error = rank_error(varying_rate)
    total = minutes_error + rate_error
    return {
        "minutes_share": minutes_error / total if total else 0.0,
        "rate_share": rate_error / total if total else 0.0,
    }
