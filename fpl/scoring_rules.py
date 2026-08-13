"""Season-keyed FPL scoring rules. Single source of truth for every point value.

Read from official Premier League sources on 2026-08-13; see SOURCES below and the
generated `references/scoring_rules.md`. Values for 2016-17 to 2025-26 are transcribed
candidates until `fpl.dataset reconcile` reproduces each season's `total_points`.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from loguru import logger
import typer

from fpl.config import PROJ_ROOT

app = typer.Typer()

REFERENCE_PATH = PROJ_ROOT / "references" / "scoring_rules.md"

SOURCES = {
    "point values": "https://fantasy.premierleague.com/api/bootstrap-static/ (game_config.scoring)",
    "defcon thresholds, divisors": "https://fantasy.premierleague.com/help/rules",
    "2026-27 BPS changes": "https://fantasy.premierleague.com/help/new",
}
DATE_READ = "2026-08-13"

POSITIONS = ("GK", "DEF", "MID", "FWD")

# Manager entries (element_type 5, position "AM") existed in 2024-25 only and score under a
# separate mng_* rule set that this model does not carry. They have no entry here, so scoring
# an AM row raises rather than silently returning zero.


@dataclass(frozen=True)
class SeasonRules:
    long_play_minutes: int
    appearance_short: int
    appearance_long: int
    goals: dict[str, int]
    assists: int
    clean_sheets: dict[str, int]
    goals_conceded: dict[str, int]
    concede_limit: int
    saves: int
    saves_limit: int
    penalties_saved: int
    penalties_missed: int
    yellow_cards: int
    red_cards: int
    own_goals: int
    defcon_points: int
    defcon_threshold: dict[str, int]
    bps_version: str


_PRE_DEFCON = SeasonRules(
    long_play_minutes=60,
    appearance_short=1,
    appearance_long=2,
    goals={"GK": 10, "DEF": 6, "MID": 5, "FWD": 4},
    assists=3,
    clean_sheets={"GK": 4, "DEF": 4, "MID": 1, "FWD": 0},
    goals_conceded={"GK": -1, "DEF": -1, "MID": 0, "FWD": 0},
    concede_limit=2,
    saves=1,
    saves_limit=3,
    penalties_saved=5,
    penalties_missed=-2,
    yellow_cards=-1,
    red_cards=-3,
    own_goals=-2,
    defcon_points=0,
    defcon_threshold={},
    bps_version="pre-2025-26",
)

# Defensive contribution starts in 2025-26. Gated on season, never on column presence:
# merged_gw.csv carries raw CBIT counts for 2016-17 to 2018-19, seasons in which those
# actions scored nothing.
_DEFCON_THRESHOLD = {"DEF": 10, "MID": 12, "FWD": 12}

SCORING_RULES = {
    season: _PRE_DEFCON
    for season in (
        "2016-17",
        "2017-18",
        "2018-19",
        "2019-20",
        "2020-21",
        "2021-22",
        "2022-23",
        "2023-24",
        "2024-25",
    )
} | {
    "2025-26": replace(
        _PRE_DEFCON,
        defcon_points=2,
        defcon_threshold=_DEFCON_THRESHOLD,
        bps_version="2025-26",
    ),
    "2026-27": replace(
        _PRE_DEFCON,
        defcon_points=2,
        defcon_threshold=_DEFCON_THRESHOLD,
        bps_version="2026-27",
    ),
}

# Partial by design: the public API omits game_config.scoring.bps, so these hold only the
# per-action values the official rules and 2026-27 patch notes state outright. Enough to
# establish that a BPS->bonus mapping fit on one version does not transfer to another;
# not enough to compute a BPS score from actions. See references/known-issues.md.
BPS_TABLES = {
    "pre-2025-26": {"tackled": -1},
    "2025-26": {"tackled": -1, "penalties_saved": 8, "cbi_per_bps": 2},
    "2026-27": {
        "tackled": 0,
        "penalties_saved": 7,
        "cbi_per_bps": 3,
        "saves": 2,
        "save_from_inside_box": 1,
        "save_from_big_chance": 1,
        "save_from_outside_box": 0,
    },
}


def _rules_table() -> str:
    seasons = list(SCORING_RULES)
    values: dict[str, list[str]] = {}
    for season in seasons:
        r = SCORING_RULES[season]
        values[season] = [
            str(r.appearance_short),
            str(r.appearance_long),
            *(str(r.goals[p]) for p in POSITIONS),
            str(r.assists),
            *(str(r.clean_sheets[p]) for p in ("GK", "DEF", "MID")),
            str(r.goals_conceded["GK"]),
            str(r.saves),
            str(r.penalties_saved),
            str(r.penalties_missed),
            str(r.yellow_cards),
            str(r.red_cards),
            str(r.own_goals),
            str(r.defcon_points),
            str(r.defcon_threshold.get("DEF", "n/a")),
            str(r.defcon_threshold.get("MID", "n/a")),
            r.bps_version,
        ]
    labels = [
        "Playing 1 to 59 minutes",
        f"Playing {_PRE_DEFCON.long_play_minutes} minutes or more",
        *(f"Goal scored ({p})" for p in POSITIONS),
        "Assist",
        *(f"Clean sheet ({p})" for p in ("GK", "DEF", "MID")),
        f"Per {_PRE_DEFCON.concede_limit} goals conceded (GK/DEF)",
        f"Per {_PRE_DEFCON.saves_limit} saves",
        "Penalty saved",
        "Penalty missed",
        "Yellow card",
        "Red card",
        "Own goal",
        "Defensive contribution",
        "DefCon threshold (DEF)",
        "DefCon threshold (MID/FWD)",
        "BPS version",
    ]
    rows = ["| Rule | " + " | ".join(seasons) + " |", "|---" * (len(seasons) + 1) + "|"]
    for i, label in enumerate(labels):
        rows.append(f"| {label} | " + " | ".join(values[s][i] for s in seasons) + " |")
    return "\n".join(rows)


@app.command()
def rules_doc(output_path: Path = REFERENCE_PATH) -> None:
    logger.info(f"Generating {output_path} from fpl.scoring_rules")
    sources = "\n".join(f"- {what}: {url}" for what, url in SOURCES.items())
    bps = "\n".join(
        f"| {version} | " + ", ".join(f"`{k}` = {v}" for k, v in sorted(table.items())) + " |"
        for version, table in BPS_TABLES.items()
    )
    output_path.write_text(
        f"""# Scoring rules

Generated from `fpl/scoring_rules.py` by `uv run python -m fpl.scoring_rules`.
Edit the module, not this file.

Read {DATE_READ} from:

{sources}

The DefCon thresholds and the "per 2 conceded" / "per 3 saves" divisors are not in the API
config; they come from the rules page prose.

The API returns 2026-27 values only. Every earlier season below is a transcribed candidate.
M2's `fpl.dataset reconcile` scores each season's rows through this table and compares to the
recorded `total_points`; until that runs, the pre-2026-27 columns are unproved.

## Point values by season

{_rules_table()}

Positions are per-season attributes. Manager entries (`AM`, 2024-25 only) score under a
separate rule set and have no entry in this table; scoring one raises.

## BPS versions

The public API omits `game_config.scoring.bps`, so the tables below hold only the per-action
values stated outright in the official rules and the 2026-27 patch notes. They establish that
the versions differ; they do not compute a BPS score.

| Version | Documented values |
|---|---|
{bps}

The 2026-27 patch notes state four changes from 2025-26: the -1 for being tackled is removed,
saving a penalty drops from 8 to 7 BPS, CBI earns 1 BPS per 3 rather than per 2, and saves are
restructured — 2 BPS per save, +1 from inside the box, +1 from a Big Chance, with saves from
outside the box dropped. The same notes date the inside/outside-box save metrics to 2025-26,
which is why 2024-25 and earlier are a third version.

No official source was checked for BPS changes *within* 2016-17 to 2024-25. Those seasons share
a version label by assumption, not by evidence.
""",
        encoding="utf-8",
    )
    logger.success(f"Wrote {output_path}")


if __name__ == "__main__":
    app()
