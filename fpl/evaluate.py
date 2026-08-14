"""Walk-forward backtest harness (spec S8, M3): the measuring instrument every later modelling
decision gets judged against, before any real component model exists.

Walks 2025-26 gameweek by gameweek. At each deadline, calls a candidate's
`predict(player_fixtures, asof) -> pl.DataFrame` (spec S4.1) and compares its predictions,
collapsed to gameweek grain, to what actually happened. Blank gameweeks (a player with no
fixture) are simply absent from the scored rows, so they never enter an error metric.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from loguru import logger
import polars as pl
import typer

from fpl.config import PROCESSED_DATA_DIR, PROJ_ROOT, REPORTS_DIR
from fpl.metrics import (
    block_bootstrap_ci,
    calibration_by_position,
    captain_regret,
    mae_rmse,
    sensitivity_decomposition,
    spearman_60plus,
    top_k_overlap,
)
from fpl.modeling.baseline import (
    baseline_lag_regression,
    baseline_minutes_times_ppg,
    baseline_positional_mean,
)

app = typer.Typer()

Predictor = Callable[..., pl.DataFrame]

SEASON = "2025-26"
INPUT_PATH = PROCESSED_DATA_DIR / "player_fixtures.parquet"
REPORT_PATH = REPORTS_DIR / "xp-model" / "1.4-backtest-baselines.md"
KNOWN_ISSUES_PATH = PROJ_ROOT / "references" / "known-issues.md"

BASELINES: dict[str, Predictor] = {
    "positional_mean": baseline_positional_mean,
    "minutes_times_ppg": baseline_minutes_times_ppg,
    "lag_regression": baseline_lag_regression,
}


def gameweek_deadlines(player_fixtures: pl.DataFrame, season: str = SEASON) -> list[datetime]:
    return (
        player_fixtures.filter(pl.col("season") == season)
        .group_by("GW")
        .agg(pl.col("kickoff_time").min())
        .sort("GW")["kickoff_time"]
        .to_list()
    )


def to_gameweek_grain(predictions: pl.DataFrame) -> pl.DataFrame:
    """Collapse fixture-grain predictions to (season, code, GW), summing xp across double
    gameweeks so a player with two fixtures scores roughly twice a player with one."""
    grain = ["season", "code", "GW", "position"]
    agg = predictions.group_by(grain).agg(
        pl.col("xp").sum(),
        pl.col("total_points").sum().alias("actual"),
        pl.col("minutes").sum(),
    )
    component_cols = [
        c for c in ("xp_minutes_component", "xp_rate_component") if c in predictions.columns
    ]
    if component_cols:
        agg = agg.join(
            predictions.group_by(grain).agg(*(pl.col(c).first() for c in component_cols)),
            on=grain,
        )
    return agg


def walk_forward(
    player_fixtures: pl.DataFrame, predict: Predictor, season: str = SEASON
) -> pl.DataFrame:
    """Predict every gameweek in `season` from only the data available at its deadline."""
    deadlines = gameweek_deadlines(player_fixtures, season)
    windows = []
    for i, asof in enumerate(deadlines):
        predicted = predict(player_fixtures, asof)
        after = pl.col("kickoff_time") >= asof
        before_next = (
            pl.col("kickoff_time") < deadlines[i + 1] if i + 1 < len(deadlines) else pl.lit(True)
        )
        windows.append(predicted.filter(after & before_next & (pl.col("season") == season)))
    return to_gameweek_grain(pl.concat(windows, how="diagonal"))


def _markdown_table(frame: pl.DataFrame, decimals: int = 3) -> str:
    rounded = frame.with_columns(pl.col(pl.Float64).round(decimals))
    header = "| " + " | ".join(rounded.columns) + " |"
    separator = "|" + "---|" * len(rounded.columns)
    rows = (
        "| " + " | ".join("" if v is None else str(v) for v in row) + " |"
        for row in rounded.iter_rows()
    )
    return "\n".join([header, separator, *rows])


def score_baseline(name: str, scored: pl.DataFrame) -> dict:
    row = {"baseline": name} | mae_rmse(scored)
    row["spearman_60plus"] = spearman_60plus(scored)
    row["captain_regret"] = captain_regret(scored)
    for k, overlap in top_k_overlap(scored).items():
        row[f"top_{k}_overlap"] = overlap
    if "xp_minutes_component" in scored.columns:
        row |= sensitivity_decomposition(scored)
    return row


@app.command()
def main(input_path: Path = INPUT_PATH, output_path: Path = REPORT_PATH) -> None:
    logger.info(f"Walking {SEASON} forward for {len(BASELINES)} baselines")
    player_fixtures = pl.read_parquet(input_path).filter(pl.col("position") != "AM")

    scored = {name: walk_forward(player_fixtures, predict) for name, predict in BASELINES.items()}
    summary = pl.DataFrame([score_baseline(name, frame) for name, frame in scored.items()])

    ci_rows = []
    names = list(scored)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            point, lo, hi = block_bootstrap_ci(scored[a], scored[b], "mae")
            ci_rows.append(f"| {a} - {b} | MAE | {point:.3f} | [{lo:.3f}, {hi:.3f}] |")

    calibration = pl.concat(
        [
            calibration_by_position(frame).with_columns(pl.lit(name).alias("baseline"))
            for name, frame in scored.items()
        ]
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        f"""# 1.4-backtest-baselines

Generated by `uv run python -m fpl.evaluate main`. Walk-forward over {SEASON}, the most recent complete season. `asof` at each gameweek's earliest kickoff; predictions after that point never see a row with `kickoff_time >= asof`.

Two caveats that apply to every later component built on this harness, not just the baselines here: DefCon has no out-of-sample season, since 2025-26 is the only season carrying the scoring column. The bonus component's eventual backtest evaluates a superseded BPS system, since BPS rules changed for 2026-27.

## Baselines

MAE/RMSE are reported but are not the steering metric — they're dominated by non-starters, and a positional mean scores respectably on them. Spearman is restricted to players with 60+ minutes, within gameweek within position. `minutes_share`/`rate_share` (where present) come from the sensitivity decomposition: holding the rate half at its realised value and varying only the minutes half, then the reverse.

`positional_mean` predicts the same value for every player in a position each gameweek, so within-group rank correlation is undefined except in the rare gameweeks with a double gameweek, where it breaks the tie. Its `spearman_60plus` is averaged over only that small, DGW-selected subset of groups, not the full set the other two baselines are scored on, so it is not comparable like-for-like to their numbers.

{_markdown_table(summary)}

## Model-vs-model comparison

38 gameweeks is a small sample; every comparison below is a block-bootstrap 95% CI over gameweeks, not a point estimate.

| Comparison | Metric | Difference | 95% CI |
|---|---|---|---|
{chr(10).join(ci_rows)}

## Calibration by position

Predicted-xp decile against mean actual points, per baseline and position.

{_markdown_table(calibration)}
""",
        encoding="utf-8",
    )
    logger.success(f"Wrote {output_path}")


if __name__ == "__main__":
    app()
