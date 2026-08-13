"""Scoring assembly tests.

Every expected total below is computed by hand from `references/scoring_rules.md` and written
out as arithmetic, never by calling `score`. The real player-fixtures are transcribed as
literals from `data/external/vaastav-fpl/`, so these tests read nothing from disk.
"""

import polars as pl
import pytest

from fpl.scoring import score
from fpl.scoring_rules import BPS_TABLES


def realised(
    position: str,
    minutes: int,
    goals: int = 0,
    assists: int = 0,
    clean_sheet: int = 0,
    goals_conceded: int = 0,
    saves: int = 0,
    penalties_saved: int = 0,
    penalties_missed: int = 0,
    yellow_cards: int = 0,
    red_cards: int = 0,
    own_goals: int = 0,
    bonus: int = 0,
    defcon: int = 0,
) -> pl.DataFrame:
    """A player-fixture that already happened, as a degenerate component distribution.

    All probability mass sits on the observed outcome, so `score` must return exactly the
    deterministic points the player actually earned.
    """
    sixty_plus = minutes >= 60
    bucket = "60plus" if sixty_plus else "1_59"
    other = "1_59" if sixty_plus else "60plus"
    per_90 = 90 / minutes

    def point_mass(count: int) -> list[float]:
        return [0.0] * count + [1.0]

    row = {
        "position": position,
        "p_zero": 0.0,
        f"p_{bucket}": 1.0,
        f"p_{other}": 0.0,
        f"exp_min_{bucket}": float(minutes),
        f"exp_min_{other}": 0.0,
        "goals_per90": goals * per_90,
        "assists_per90": assists * per_90,
        "yellow_cards_per90": yellow_cards * per_90,
        "red_cards_per90": red_cards * per_90,
        "own_goals_per90": own_goals * per_90,
        "penalties_missed_per90": penalties_missed * per_90,
        "penalties_saved_per90": penalties_saved * per_90,
        "p_clean_sheet": float(clean_sheet),
        f"p_defcon_{bucket}": float(defcon),
        f"p_defcon_{other}": 0.0,
        f"e_bonus_{bucket}": float(bonus),
        f"e_bonus_{other}": 0.0,
        f"saves_pmf_{bucket}": point_mass(saves),
        f"saves_pmf_{other}": [1.0],
        f"gc_pmf_{bucket}": point_mass(goals_conceded),
        f"gc_pmf_{other}": [1.0],
    }
    return pl.DataFrame([row])


def xp(components: pl.DataFrame, season: str) -> float:
    return score(components, season)["xp"].item()


# 2025-26 merged_gw.csv, hand-computed from the rules table.
#
# Raya      GK  GW1 e1    90' CS, 7 saves, 1 yellow, 3 bonus   2 + 4 + 2 + 3 - 1  = 10
# Senesi    DEF GW1 e72   90' 4 conceded, 15 defcon actions    2 - 2 + 2          =  2
# Garner    MID GW2 e303  90' goal, CS, 16 defcon, 1 yellow    2 + 5 + 1 + 2 - 1 + 1 = 10
# Ekitike   FWD GW1 e661  71' goal + assist, 2 bonus           2 + 4 + 3 + 2      = 11
# Chiesa    MID GW1 e385   8' goal                             1 + 5              =  6
REAL_2025_26 = [
    (realised("GK", 90, clean_sheet=1, saves=7, yellow_cards=1, bonus=3), 2 + 4 + 2 + 3 - 1),
    (realised("DEF", 90, goals_conceded=4, defcon=1), 2 - 2 + 2),
    (
        realised("MID", 90, goals=1, clean_sheet=1, yellow_cards=1, bonus=1, defcon=1),
        2 + 5 + 1 + 2 - 1 + 1,
    ),
    (realised("FWD", 71, goals=1, assists=1, goals_conceded=1, bonus=2), 2 + 4 + 3 + 2),
    (realised("MID", 8, goals=1), 1 + 5),
]


@pytest.mark.parametrize("components,expected", REAL_2025_26)
def test_real_2025_26_fixtures_reproduce_actual_total_points(components, expected):
    assert xp(components, "2025-26") == pytest.approx(expected)


def test_degenerate_distribution_reproduces_floor_functions():
    # 7 saves is 2 points, not 7/3. 5 conceded is -2, not -2.5.
    keeper = realised("GK", 90, saves=7, goals_conceded=5)
    scored = score(keeper, "2025-26")
    assert scored["xp_saves"].item() == pytest.approx(2.0)
    assert scored["xp_goals_conceded"].item() == pytest.approx(-2.0)
    assert scored["xp"].item() == pytest.approx(2 + 2 - 2)


def test_floor_expectation_is_not_floor_of_expectation():
    # Mean saves is 3, so floor(E[S]/3) would be 1. E[floor(S/3)] over {2, 4} is 0.5.
    spread = realised("GK", 90, saves=0)
    spread = spread.with_columns(
        saves_pmf_60plus=pl.lit([0.0, 0.0, 0.5, 0.0, 0.5], dtype=pl.List(pl.Float64))
    )
    assert score(spread, "2025-26")["xp_saves"].item() == pytest.approx(0.5)


def test_2024_25_awards_no_defcon_points():
    # Real 2024-25 defender: Lisandro Martinez GW1, 90' clean sheet, 1 bonus = 2 + 4 + 1 = 7.
    defender = realised("DEF", 90, clean_sheet=1, bonus=1, defcon=1)
    scored = score(defender, "2024-25")
    assert scored["xp_defcon"].item() == 0.0
    assert scored["xp"].item() == pytest.approx(2 + 4 + 1)


def test_2018_19_cbit_row_awards_no_defcon_and_reproduces_total_points():
    # James Tarkowski, 2018-19 GW1: 90', clean sheet, 1 bonus, and 19 CBIT actions. The raw
    # CBIT columns exist that season; defensive contribution scoring did not.
    defender = realised("DEF", 90, clean_sheet=1, bonus=1, defcon=1)
    scored = score(defender, "2018-19")
    assert scored["xp_defcon"].item() == 0.0
    assert scored["xp"].item() == pytest.approx(2 + 4 + 1)
    # The same fixture under 2025-26 rules would have earned the +2 the season did not award.
    assert xp(defender, "2025-26") == pytest.approx(2 + 4 + 1 + 2)


def test_no_clean_sheet_points_without_sixty_minutes():
    cameo = realised("DEF", 30)
    cameo = cameo.with_columns(p_clean_sheet=pl.lit(0.9))
    scored = score(cameo, "2025-26")
    assert scored["xp_clean_sheet"].item() == 0.0
    assert scored["xp"].item() == pytest.approx(1.0)


def test_bps_tables_differ_between_2025_26_and_2026_27():
    assert BPS_TABLES["2025-26"] != BPS_TABLES["2026-27"]
    assert BPS_TABLES["2025-26"]["tackled"] == -1
    assert BPS_TABLES["2026-27"]["tackled"] == 0
    assert BPS_TABLES["2025-26"]["cbi_per_bps"] != BPS_TABLES["2026-27"]["cbi_per_bps"]
