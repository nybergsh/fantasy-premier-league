from pathlib import Path
import subprocess

from loguru import logger
from tqdm import tqdm
import typer

from fpl.config import EXTERNAL_DATA_DIR, PROCESSED_DATA_DIR, RAW_DATA_DIR

app = typer.Typer()

VAASTAV_FPL_DIR = EXTERNAL_DATA_DIR / "vaastav-fpl"


def log_source_lineage(source_dir: Path = VAASTAV_FPL_DIR) -> None:
    """Log the pinned upstream commit SHA so processed output can be traced to a source snapshot.

    See references/data-sources/vaastav-fpl.md for the full provenance record (fetch date,
    file hash manifest, reproduction command) — this only logs the SHA on each run.
    """
    sha = subprocess.check_output(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"], text=True
    ).strip()
    logger.info(f"Source: vaastav/Fantasy-Premier-League @ {sha}")


@app.command()
def main(
    # ---- REPLACE DEFAULT PATHS AS APPROPRIATE ----
    input_path: Path = RAW_DATA_DIR / "dataset.csv",
    output_path: Path = PROCESSED_DATA_DIR / "dataset.csv",
    # ----------------------------------------------
):
    log_source_lineage()
    # ---- REPLACE THIS WITH YOUR OWN CODE ----
    logger.info("Processing dataset...")
    for i in tqdm(range(10), total=10):
        if i == 5:
            logger.info("Something happened for iteration 5.")
    logger.success("Processing dataset complete.")
    # -----------------------------------------


if __name__ == "__main__":
    app()
