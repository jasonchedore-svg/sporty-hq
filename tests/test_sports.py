from datetime import date, datetime, timezone

from sporty_hq.sports import family, in_season, nfl_week1_wednesday, sport_rank


def test_nfl_week1_wednesday_2026() -> None:
    assert nfl_week1_wednesday(2026) == date(2026, 9, 9)


def test_mlb_nfl_in_season_ncaaf_next() -> None:
    mlb_night = datetime(2026, 9, 8, 23, 0, tzinfo=timezone.utc)
    nfl_kick = datetime(2026, 9, 11, 0, 20, tzinfo=timezone.utc)
    nfl_before = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)
    nba_sep = datetime(2026, 9, 8, 23, 0, tzinfo=timezone.utc)
    nhl_oct = datetime(2026, 10, 15, 23, 0, tzinfo=timezone.utc)
    assert in_season("baseball_mlb", mlb_night)
    assert in_season("americanfootball_nfl", nfl_kick)
    assert not in_season("americanfootball_nfl", nfl_before)
    assert in_season("americanfootball_ncaaf", mlb_night)
    assert not in_season("basketball_nba", nba_sep)
    assert in_season("icehockey_nhl", nhl_oct)
    assert sport_rank("baseball_mlb") == sport_rank("americanfootball_nfl") == 0
    assert sport_rank("americanfootball_ncaaf") == 1
    assert family("soccer_epl") == "soccer"
    assert family("americanfootball_cfl") == "skip"
