"""Ingestion contract tests. Data-free: every frame here is built in the test."""

from datetime import datetime

import polars as pl
import pytest

from fpl.dataset import CONTRACT, check_grain, realised_components
from fpl.scoring import INPUT_COLUMNS, score


def fixture_row(element: int, gw: int, fixture: int, minutes: int = 90, **overrides) -> dict:
    row = {
        "season": "2025-26",
        "kickoff_time": datetime(2025, 8, 16, 14, 0),
        "code": 1000 + element,
        "position": "MID",
        "team": 1,
        "team_name": "Arsenal",
        "GW": gw,
        "fixture": fixture,
        "element": element,
        "opponent": 2,
        "was_home": True,
        "minutes": minutes,
        "starts": 1,
        "goals_scored": 0,
        "assists": 0,
        "clean_sheets": 0,
        "goals_conceded": 0,
        "saves": 0,
        "bonus": 0,
        "bps": 0,
        "yellow_cards": 0,
        "red_cards": 0,
        "own_goals": 0,
        "penalties_missed": 0,
        "penalties_saved": 0,
        "expected_goals": None,
        "expected_assists": None,
        "expected_goals_conceded": None,
        "clearances_blocks_interceptions": None,
        "tackles": None,
        "recoveries": None,
        "defensive_contribution": None,
        "total_points": 2,
    }
    return row | overrides


def frame(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows)).with_columns(
        pl.col("kickoff_time").dt.replace_time_zone("UTC")
    )


def test_triple_gameweek_is_within_the_grain():
    check_grain(frame(*(fixture_row(1, 25, f) for f in (100, 101, 102))))


def test_four_fixtures_in_one_gameweek_is_rejected():
    with pytest.raises(AssertionError):
        check_grain(frame(*(fixture_row(1, 25, f) for f in (100, 101, 102, 103))))


def test_repeated_key_is_rejected():
    with pytest.raises(AssertionError):
        check_grain(frame(fixture_row(1, 25, 100), fixture_row(1, 25, 100, minutes=45)))


def test_minutes_outside_range_is_rejected():
    with pytest.raises(AssertionError):
        check_grain(frame(fixture_row(1, 25, 100, minutes=131)))


def test_contract_excludes_leaky_and_unused_source_columns():
    for column in ("xP", "penalties_order", "mng_win", "ict_index", "value"):
        assert column not in CONTRACT


def test_realised_components_satisfy_the_scoring_contract():
    components = realised_components(frame(fixture_row(1, 25, 100)))
    assert set(INPUT_COLUMNS) <= set(components.columns)


def test_realised_components_reproduce_recorded_points():
    played = frame(
        fixture_row(1, 25, 100, position="GK", minutes=90, saves=7, clean_sheets=1, total_points=8),
        fixture_row(2, 25, 101, position="FWD", minutes=20, goals_scored=1, total_points=5),
        fixture_row(3, 25, 102, minutes=0, total_points=0),
    )
    scored = score(realised_components(played), "2025-26")
    assert scored["xp"].to_list() == pytest.approx([8.0, 5.0, 0.0])
