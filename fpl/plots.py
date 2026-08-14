"""Figures for the xP model reports. PNG at a fixed size — SVG diffs poorly and PDF doesn't
render inline on GitHub (spec S9.1).
"""

from pathlib import Path

from loguru import logger
import matplotlib.pyplot as plt
import polars as pl
import typer

from fpl.config import FIGURES_DIR
from fpl.evaluate import BASELINES, INPUT_PATH, SEASON, walk_forward
from fpl.metrics import metric_ci

app = typer.Typer()

FIGURE_SIZE = (8, 5)
BASELINE_COMPARISON_PATH = FIGURES_DIR / "xp-model" / "1.4-baseline-comparison.png"


def baseline_comparison(scored: dict[str, pl.DataFrame], metric: str, output_path: Path) -> None:
    """Bar chart of `metric` per baseline, with block-bootstrap 95% CIs (spec S9.1)."""
    names = list(scored)
    cis = [metric_ci(scored[name], metric) for name in names]
    points = [ci[0] for ci in cis]
    lower_err = [p - lo for p, (_, lo, _) in zip(points, cis)]
    upper_err = [hi - p for p, (_, _, hi) in zip(points, cis)]

    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    ax.bar(names, points, yerr=[lower_err, upper_err], capsize=6)
    ax.set_ylabel(metric)
    ax.set_title(f"Baseline comparison — {metric} ({SEASON}, 95% block-bootstrap CI)")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


@app.command()
def main(input_path: Path = INPUT_PATH, output_path: Path = BASELINE_COMPARISON_PATH) -> None:
    logger.info(f"Walking {SEASON} forward for {len(BASELINES)} baselines")
    player_fixtures = pl.read_parquet(input_path).filter(pl.col("position") != "AM")
    scored = {name: walk_forward(player_fixtures, predict) for name, predict in BASELINES.items()}
    baseline_comparison(scored, "mae", output_path)
    logger.success(f"Wrote {output_path}")


if __name__ == "__main__":
    app()
