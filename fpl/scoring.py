"""Assemble component distributions into expected points, per the official scoring rules.

Points are not linear in minutes, so this is the law of total expectation over the three
minutes buckets rather than a scalar multiply:

    xP = P(0)*0 + P(1-59)*E[points | 1-59] + P(60+)*E[points | 60+]

Count components (saves, goals conceded) arrive as probability mass functions because
E[floor(X)] != floor(E[X]); threshold components (defcon) arrive as probabilities because
+2 is awarded once at or above the threshold, not pro-rated.
"""

import polars as pl

from fpl.scoring_rules import SCORING_RULES, SeasonRules

# The input contract. `*_pmf_*` are list columns holding probability mass over counts
# 0, 1, 2, ...; the list index is the count. Everything is conditional on the minutes
# bucket named in the suffix, because a cameo and a full game differ on every component.
INPUT_COLUMNS = (
    "position",
    "p_zero",
    "p_1_59",
    "p_60plus",
    "exp_min_1_59",
    "exp_min_60plus",
    "goals_per90",
    "assists_per90",
    "yellow_cards_per90",
    "red_cards_per90",
    "own_goals_per90",
    "penalties_missed_per90",
    "penalties_saved_per90",
    "p_clean_sheet",
    "p_defcon_1_59",
    "p_defcon_60plus",
    "e_bonus_1_59",
    "e_bonus_60plus",
    "saves_pmf_1_59",
    "saves_pmf_60plus",
    "gc_pmf_1_59",
    "gc_pmf_60plus",
)


def _floor_expectation(pmf: str, divisor: int) -> pl.Expr:
    """E[floor(X / divisor)] against a pmf list column, rather than floor of the mean."""
    return pl.col(pmf).list.eval(pl.element() * (pl.int_range(pl.len()) // divisor)).list.sum()


def _bucket_points(bucket: str, rules: SeasonRules) -> dict[str, pl.Expr]:
    """Expected points by component, conditional on the player's minutes falling in `bucket`."""
    sixty_plus = bucket == "60plus"
    per_90 = pl.col(f"exp_min_{bucket}") / 90
    goal_value = pl.col("position").replace_strict(rules.goals, return_dtype=pl.Int32)
    cs_value = pl.col("position").replace_strict(rules.clean_sheets, return_dtype=pl.Int32)
    gc_value = pl.col("position").replace_strict(rules.goals_conceded, return_dtype=pl.Int32)

    return {
        "appearance": pl.lit(
            float(rules.appearance_long if sixty_plus else rules.appearance_short)
        ),
        "goals": pl.col("goals_per90") * per_90 * goal_value,
        "assists": pl.col("assists_per90") * per_90 * rules.assists,
        "clean_sheet": (pl.col("p_clean_sheet") * cs_value) if sixty_plus else pl.lit(0.0),
        "goals_conceded": _floor_expectation(f"gc_pmf_{bucket}", rules.concede_limit) * gc_value,
        "saves": _floor_expectation(f"saves_pmf_{bucket}", rules.saves_limit) * rules.saves,
        "defcon": (
            pl.lit(0.0)
            if rules.defcon_points == 0
            else pl.col(f"p_defcon_{bucket}") * rules.defcon_points
        ),
        "bonus": pl.col(f"e_bonus_{bucket}"),
        "cards": (
            pl.col("yellow_cards_per90") * per_90 * rules.yellow_cards
            + pl.col("red_cards_per90") * per_90 * rules.red_cards
        ),
        "penalties": (
            pl.col("penalties_missed_per90") * per_90 * rules.penalties_missed
            + pl.col("penalties_saved_per90") * per_90 * rules.penalties_saved
        ),
        "own_goals": pl.col("own_goals_per90") * per_90 * rules.own_goals,
    }


def score(components: pl.DataFrame, season: str) -> pl.DataFrame:
    """Component distributions in, expected points out, at player-fixture grain.

    Adds one `xp_<component>` column plus `xp`. Each component column is the
    probability-weighted contribution across both non-zero minutes buckets, so the component
    columns sum to `xp` and can be read as a decomposition.
    """
    rules = SCORING_RULES[season]
    short = _bucket_points("1_59", rules)
    sixty_plus = _bucket_points("60plus", rules)

    weighted = {
        f"xp_{name}": pl.col("p_1_59") * short[name] + pl.col("p_60plus") * sixty_plus[name]
        for name in short
    }
    return components.with_columns(**weighted).with_columns(
        pl.sum_horizontal(*weighted).alias("xp")
    )
