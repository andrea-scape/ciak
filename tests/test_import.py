import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from src.data.importers import (
    GenericJSON,
    IMDbCSV,
    ImportItem,
    ImportParseError,
    LetterboxdCSV,
    Matcher,
    TraktCSV,
    TraktExport,
    date_to_ts,
    select_parser,
)
from src.data.local.repository import LocalMediaRepository
from src.data.tmdb.service import TmdbMetadataService
from src.domain.models import Movie


def _write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class DateUtilsTest(unittest.TestCase):
    def test_date_to_ts(self):
        self.assertEqual(date_to_ts("2023-08-14"), 1691971200)
        self.assertIsNone(date_to_ts(""))
        self.assertIsNone(date_to_ts(None))
        self.assertIsNone(date_to_ts("not a date"))


class TraktCsvParseTest(unittest.TestCase):
    def test_watched_and_watchlist_targets(self):
        csv_text = (
            "Listing,Title,Year,Type,IMDB ID,Rating,Watched Date\n"
            "Watched Movies,Fight Club,1999,movie,tt0137523,,\n"
            "Watchlist,Pulp Fiction,1994,movie,tt0110912,,\n"
        )
        with tempfile.TemporaryDirectory() as d:
            path = _write(os.path.join(d, "trakt.csv"), csv_text)
            items = TraktCSV.parse(path)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "Fight Club")
        self.assertEqual(items[0].year, 1999)
        self.assertEqual(items[0].imdb_id, "tt0137523")
        self.assertEqual(items[0].media_type, "movie")
        self.assertEqual(items[0].target, "watched")
        self.assertEqual(items[1].title, "Pulp Fiction")
        self.assertEqual(items[1].target, "watchlist")

    def test_ratings_and_date_parsing(self):
        csv_text = (
            "Listing,Title,Year,Type,IMDB ID,Rating,Watched Date\n"
            "Ratings,Shawshank,1994,movie,tt0111161,8,2023-08-14\n"
        )
        with tempfile.TemporaryDirectory() as d:
            path = _write(os.path.join(d, "trakt.csv"), csv_text)
            items = TraktCSV.parse(path)
        self.assertEqual(items[0].target, "ratings")
        self.assertEqual(items[0].rating, 8)
        self.assertEqual(items[0].watched_date, "2023-08-14")

    def test_unknown_listing_defaults_to_watched(self):
        csv_text = "Listing,Title\n,Random,2020\n"
        with tempfile.TemporaryDirectory() as d:
            path = _write(os.path.join(d, "trakt.csv"), csv_text)
            items = TraktCSV.parse(path)
        self.assertEqual(items[0].target, "watched")


class LetterboxdCsvParseTest(unittest.TestCase):
    def test_diary_row_watched_with_imdb_id(self):
        csv_text = (
            "Date,Name,Year,Letterboxd URI,Rating,Tags\n"
            "2023-08-14,Fight Club,1999,https://boxd.it/2iTkfq,4.5,\n"
        )
        with tempfile.TemporaryDirectory() as d:
            path = _write(os.path.join(d, "diary.csv"), csv_text)
            items = LetterboxdCSV.parse(path)
        self.assertEqual(items[0].title, "Fight Club")
        self.assertEqual(items[0].year, 1999)
        self.assertEqual(items[0].watched_date, "2023-08-14")
        self.assertEqual(items[0].rating, 9)
        self.assertEqual(items[0].target, "watched")

    def test_watchlist_row_has_no_date(self):
        csv_text = "Date,Name,Year,Letterboxd URI,Rating,Tags\n" \
                   ",Pulp Fiction,1994,https://boxd.it/2iTkfn,,\n"
        with tempfile.TemporaryDirectory() as d:
            path = _write(os.path.join(d, "watchlist.csv"), csv_text)
            items = LetterboxdCSV.parse(path)
        self.assertEqual(items[0].target, "watchlist")
        self.assertIsNone(items[0].watched_date)
        self.assertIsNone(items[0].rating)

    def test_rating_half_star_rounds_up(self):
        csv_text = "Date,Name,Year,Rating\n2023-01-01,A,2020,0.5\n"
        with tempfile.TemporaryDirectory() as d:
            path = _write(os.path.join(d, "diary.csv"), csv_text)
            items = LetterboxdCSV.parse(path)
        self.assertEqual(items[0].rating, 1)


class ImdbCsvParseTest(unittest.TestCase):
    def test_watchlist_import(self):
        csv_text = (
            "Position,Const,Type,Title,Year,Rating\n"
            "1,tt0137523,movie,Fight Club,1999,7.5\n"
        )
        with tempfile.TemporaryDirectory() as d:
            path = _write(os.path.join(d, "imdb.csv"), csv_text)
            items = IMDbCSV.parse(path)
        self.assertEqual(items[0].title, "Fight Club")
        self.assertEqual(items[0].imdb_id, "tt0137523")
        self.assertEqual(items[0].media_type, "movie")
        self.assertEqual(items[0].target, "watchlist")


class JsonParseTest(unittest.TestCase):
    def test_flat_list(self):
        data = [
            {"title": "Fight Club", "year": 1999, "imdb_id": "tt0137523"},
            {"title": "Silo", "media_type": "show", "rating": 8},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = _write(os.path.join(d, "import.json"), json.dumps(data))
            items = GenericJSON.parse(path)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "Fight Club")
        self.assertEqual(items[1].media_type, "show")
        self.assertEqual(items[1].rating, 8)

    def test_categorized_export(self):
        data = {
            "watched": [{"title": "A", "watched_date": "2023-08-14"}],
            "watchlist": [{"title": "B"}],
            "ratings": [{"title": "C", "rating": 9}],
            "collection": [{"title": "D"}],
        }
        with tempfile.TemporaryDirectory() as d:
            path = _write(os.path.join(d, "export.json"), json.dumps(data))
            items = GenericJSON.parse(path)
        targets = {i.title: i.target for i in items}
        self.assertEqual(targets["A"], "watched")
        self.assertEqual(targets["B"], "watchlist")
        self.assertEqual(targets["C"], "ratings")
        self.assertEqual(targets["D"], "collection")

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write(os.path.join(d, "bad.json"), "{not json")
            with self.assertRaises(Exception):
                GenericJSON.parse(path)


class SelectParserTest(unittest.TestCase):
    def test_extension_win(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write(os.path.join(d, "x.json"), "[]")
            self.assertIs(select_parser(path), GenericJSON)

    def test_header_detection(self):
        with tempfile.TemporaryDirectory() as d:
            imdb = _write(os.path.join(d, "imdb.csv"), "Position,Const\n1,tt1\n")
            diary = _write(
                os.path.join(d, "diary.csv"), "Date,Name,Year\n2023,FC,1999\n"
            )
            trakt = _write(
                os.path.join(d, "trakt.csv"), "Title,Year,IMDB ID\nFC,1999,\n"
            )
            self.assertIs(select_parser(imdb), IMDbCSV)
            self.assertIs(select_parser(diary), LetterboxdCSV)
            self.assertIs(select_parser(trakt), TraktCSV)

    def test_zip_and_directory_select_trakt(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIs(select_parser(os.path.join(d, "x.zip")), TraktExport)
            self.assertIs(select_parser(d), TraktExport)


class TraktExportTest(unittest.TestCase):
    HISTORY = [
        {
            "id": 1,
            "watched_at": "2026-08-17T18:14:00.000Z",
            "action": "watch",
            "type": "movie",
            "movie": {
                "ids": {"imdb": "tt30057084", "tmdb": 1097549},
                "year": 2024,
                "title": "Babygirl",
            },
        },
        {
            "id": 2,
            "watched_at": "2026-08-16T21:00:00.000Z",
            "action": "scrobble",
            "type": "episode",
            "episode": {
                "ids": {"imdb": "tt1", "tmdb": 5001},
                "number": 3,
                "season": 2,
                "title": "Pilot",
            },
            "show": {
                "ids": {"imdb": "tt259", "tmdb": 259265},
                "year": 2024,
                "title": "Something Very Bad",
            },
        },
    ]

    @staticmethod
    def _write_export(d, include_history=True, include_files=True):
        files = {}
        if include_history:
            files["watched-history-1.json"] = json.dumps(TraktExportTest.HISTORY)
        if include_files:
            files["watched-movies.json"] = json.dumps([
                {
                    "last_watched_at": "2026-08-01T10:00:00.000Z",
                    "movie": {
                        "ids": {"imdb": "tt2", "tmdb": 680},
                        "year": 1994,
                        "title": "Pulp Fiction",
                    },
                    "plays": 1,
                }
            ])
            files["ratings-movies.json"] = json.dumps([
                {
                    "rated_at": "2026-05-09T22:33:46.000Z",
                    "rating": 8,
                    "movie": {
                        "ids": {"imdb": "tt30057084", "tmdb": 1097549},
                        "year": 2024,
                        "title": "Babygirl",
                    },
                }
            ])
            files["ratings-shows.json"] = json.dumps([
                {
                    "rated_at": "2026-01-01T00:00:00.000Z",
                    "rating": 9,
                    "show": {
                        "ids": {"imdb": "tt259", "tmdb": 259265},
                        "year": 2024,
                        "title": "Something Very Bad",
                    },
                }
            ])
            files["ratings-seasons.json"] = json.dumps([
                {
                    "rated_at": "2026-05-09T22:33:46.000Z",
                    "rating": 8,
                    "season": {"number": 1},
                    "show": {
                        "ids": {"imdb": "tt259", "tmdb": 259265},
                        "year": 2024,
                        "title": "Something Very Bad",
                    },
                }
            ])
            files["lists-watchlist.json"] = json.dumps([
                {
                    "listed_at": "2026-07-01T00:00:00.000Z",
                    "rank": 1,
                    "movie": {
                        "ids": {"imdb": "tt2", "tmdb": 680},
                        "year": 1994,
                        "title": "Pulp Fiction",
                    },
                }
            ])
        for name, content in files.items():
            _write(os.path.join(d, name), content)

    def _parse_dir(self, **kwargs):
        with tempfile.TemporaryDirectory() as d:
            self._write_export(d, **kwargs)
            return TraktExport.parse(d)

    def test_parses_history_precisely(self):
        items = self._parse_dir()
        watched = [i for i in items if i.target == "watched"]
        self.assertEqual(len(watched), 2)

        movie = watched[0]
        self.assertEqual(movie.title, "Babygirl")
        self.assertEqual(movie.tmdb_id, 1097549)
        self.assertEqual(movie.imdb_id, "tt30057084")
        self.assertEqual(movie.media_type, "movie")
        self.assertEqual(movie.watched_date, "2026-08-17")

        episode = watched[1]
        self.assertEqual(episode.title, "Something Very Bad")
        self.assertEqual(episode.tmdb_id, 5001)
        self.assertEqual(episode.show_tmdb_id, 259265)
        self.assertEqual(episode.season_number, 2)
        self.assertEqual(episode.episode_number, 3)
        self.assertEqual(episode.media_type, "episode")
        self.assertEqual(episode.source, "Pilot")
        self.assertEqual(episode.watched_date, "2026-08-16")

    def test_history_wins_over_watched_files(self):
        items = self._parse_dir()
        titles = [i.title for i in items if i.target == "watched"]
        self.assertEqual(titles, ["Babygirl", "Something Very Bad"])

    def test_watched_files_used_without_history(self):
        items = self._parse_dir(include_history=False)
        watched = [i for i in items if i.target == "watched"]
        self.assertEqual(len(watched), 1)
        self.assertEqual(watched[0].title, "Pulp Fiction")
        self.assertEqual(watched[0].tmdb_id, 680)
        self.assertEqual(watched[0].watched_date, "2026-08-01")

    def test_ratings_and_watchlist(self):
        items = self._parse_dir()
        ratings = [i for i in items if i.target == "ratings"]
        watchlist = [i for i in items if i.target == "watchlist"]
        self.assertEqual(len(ratings), 2)
        self.assertEqual(len(watchlist), 1)
        movie_rating = next(i for i in ratings if i.title == "Babygirl")
        self.assertEqual(movie_rating.rating, 8)
        self.assertEqual(movie_rating.watched_date, "2026-05-09")
        show_rating = next(i for i in ratings if i.title == "Something Very Bad")
        self.assertEqual(show_rating.rating, 9)
        self.assertEqual(show_rating.media_type, "show")
        self.assertEqual(watchlist[0].tmdb_id, 680)
        self.assertEqual(watchlist[0].watched_date, "2026-07-01")

    def test_season_rating_not_promoted_when_show_rated(self):
        items = self._parse_dir()
        show_ratings = [i for i in items
                        if i.target == "ratings" and i.media_type == "show"]
        self.assertEqual(len(show_ratings), 1)
        self.assertEqual(show_ratings[0].rating, 9)

    def test_season_rating_promoted_when_show_unrated(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_export(d)
            with open(os.path.join(d, "ratings-shows.json"), "w") as f:
                f.write("[]")
            with open(os.path.join(d, "ratings-seasons.json"), "w") as f:
                f.write(json.dumps([
                    {
                        "rated_at": "2026-05-09T22:33:46.000Z",
                        "rating": 8,
                        "season": {"number": 1},
                        "show": {
                            "ids": {"imdb": "tt259", "tmdb": 259265},
                            "year": 2024,
                            "title": "Something Very Bad",
                        },
                    }
                ]))
            items = TraktExport.parse(d)
        promoted = [i for i in items if i.target == "ratings"
                    and i.media_type == "show" and i.tmdb_id == 259265]
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0].rating, 8)

    def test_latest_season_wins(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_export(d, include_files=False)
            with open(os.path.join(d, "ratings-seasons.json"), "w") as f:
                f.write(json.dumps([
                    {
                        "rated_at": "2026-01-01T00:00:00.000Z",
                        "rating": 3,
                        "season": {"number": 1},
                        "show": {
                            "ids": {"tmdb": 9}, "year": 2020, "title": "S",
                        },
                    },
                    {
                        "rated_at": "2026-06-01T00:00:00.000Z",
                        "rating": 8,
                        "season": {"number": 2},
                        "show": {
                            "ids": {"tmdb": 9}, "year": 2020, "title": "S",
                        },
                    },
                ]))
            items = TraktExport.parse(d)
        promoted = [i for i in items if i.target == "ratings"
                    and i.tmdb_id == 9]
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0].rating, 8)

    def test_zip_archive(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_export(d)
            zip_path = os.path.join(d, "export.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                for name in os.listdir(d):
                    if name.endswith(".json"):
                        zf.write(os.path.join(d, name), name)
            items = TraktExport.parse(zip_path)
        watched = [i for i in items if i.target == "watched"]
        self.assertEqual(len(watched), 2)

    def test_nested_zip_archive(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_export(d)
            zip_path = os.path.join(d, "export.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                for name in os.listdir(d):
                    if name.endswith(".json"):
                        zf.write(
                            os.path.join(d, name), f"trakt-export/{name}"
                        )
            items = TraktExport.parse(zip_path)
        self.assertEqual(len(items), 5)

    def test_non_trakt_export_raises(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "user-profile.json"), "{}")
            with self.assertRaises(ImportParseError):
                TraktExport.parse(d)


class _StubModel:
    def __init__(self, tmdb_id, media_type, title, year):
        self.tmdb_id = tmdb_id
        self.media_type = media_type
        self.title = title
        self.year = year

class _StubService:
    """Resolves only the ids/titles it was configured with."""

    def __init__(self, by_imdb=None, by_search=None):
        self.by_imdb = by_imdb or {}
        self.by_search = by_search or {}

    def resolve_imdb(self, imdb_id):
        return self.by_imdb.get(imdb_id)

    def search_best(self, title, year, media_type=None):
        key = (title.lower(), year)
        return self.by_search.get(key)


class MatcherTest(unittest.TestCase):
    def _make_repo(self, d):
        db = os.path.join(d, "test.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo

    def test_matches_local_by_imdb_id(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            conn = repo._ensure_conn()
            conn.execute(
                "INSERT INTO media_items (tmdb_id, media_type, title, year, "
                "imdb_id, cached_at, updated_at) VALUES (550, 'movie', "
                "'Fight Club', 1999, 'tt0137523', 0, 0)"
            )
            conn.commit()
            matcher = Matcher(repo, _StubService())
            result = matcher.match(
                ImportItem(title="Fight Club", imdb_id="tt0137523")
            )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.tmdb_id, 550)

    def test_matches_local_by_title_year(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            conn = repo._ensure_conn()
            conn.execute(
                "INSERT INTO media_items (tmdb_id, media_type, title, year, "
                "imdb_id, cached_at, updated_at) VALUES (550, 'movie', "
                "'Fight Club', 1999, NULL, 0, 0)"
            )
            conn.commit()
            matcher = Matcher(repo, _StubService())
            result = matcher.match(
                ImportItem(title="Fight Club", year=1999)
            )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.tmdb_id, 550)

    def test_resolves_via_tmdb_find(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            service = _StubService(
                by_imdb={"tt0137523": _StubModel(550, "movie", "Fight Club", 1999)}
            )
            matcher = Matcher(repo, service)
            result = matcher.match(
                ImportItem(title="Fight Club", imdb_id="tt0137523")
            )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.tmdb_id, 550)

    def test_resolves_via_tmdb_search(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            service = _StubService(
                by_search={("pulp fiction", 1994): _StubModel(680, "movie", "Pulp Fiction", 1994)}
            )
            matcher = Matcher(repo, service)
            result = matcher.match(
                ImportItem(title="Pulp Fiction", year=1994)
            )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.tmdb_id, 680)

    def test_direct_tmdb_id_no_lookups(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            matcher = Matcher(repo, _StubService())
            result = matcher.match(ImportItem(
                title="Babygirl", tmdb_id=1097549, media_type="movie"
            ))
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.tmdb_id, 1097549)
        self.assertEqual(result.media_type, "movie")

    def test_unmatched_when_nothing_found(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            matcher = Matcher(repo, _StubService())
            result = matcher.match(ImportItem(title="Nothing"))
        self.assertEqual(result.status, "unmatched")
        self.assertIsNone(result.tmdb_id)

    def test_duplicate_when_already_in_target(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            repo.mark_watched(550, "movie")
            service = _StubService(
                by_imdb={"tt0137523": _StubModel(550, "movie", "Fight Club", 1999)}
            )
            matcher = Matcher(repo, service)
            result = matcher.match(
                ImportItem(title="Fight Club", imdb_id="tt0137523", target="watched")
            )
        self.assertEqual(result.status, "duplicate")
        self.assertEqual(result.tmdb_id, 550)


class RepositoryImportTest(unittest.TestCase):
    def _make_repo(self, d):
        db = os.path.join(d, "test.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo

    def test_import_watched_sets_timestamp_and_media_meta(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            count = repo.import_watched([
                {
                    "tmdb_id": 550, "media_type": "movie", "title": "Fight Club",
                    "year": 1999, "imdb_id": "tt0137523",
                    "watched_at": 1691971200,
                },
            ])
            self.assertEqual(count, 1)
            conn = repo._ensure_conn()
            row = conn.execute(
                "SELECT tmdb_id, watched_at FROM watched_items WHERE tmdb_id=550"
            ).fetchone()
            self.assertEqual(row[1], 1691971200)
            meta = conn.execute(
                "SELECT title, imdb_id FROM media_items WHERE tmdb_id=550"
            ).fetchone()
            self.assertEqual(meta[0], "Fight Club")
            self.assertEqual(meta[1], "tt0137523")

    def test_import_watched_episode_caches_parent_show(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            count = repo.import_watched([
                {
                    "tmdb_id": 5001, "media_type": "episode",
                    "title": "Something Very Bad", "year": 2024,
                    "imdb_id": "tt259", "show_tmdb_id": 259265,
                    "season_number": 2, "episode_number": 3,
                    "watched_at": 1691971200,
                },
            ])
            self.assertEqual(count, 1)
            conn = repo._ensure_conn()
            row = conn.execute(
                "SELECT tmdb_id, media_type, show_tmdb_id, season_number, "
                "episode_number FROM watched_items WHERE tmdb_id=5001"
            ).fetchone()
            self.assertEqual(tuple(row), (5001, "episode", 259265, 2, 3))
            meta = conn.execute(
                "SELECT media_type, title FROM media_items WHERE tmdb_id=259265"
            ).fetchone()
            self.assertEqual(tuple(meta), ("show", "Something Very Bad"))

    def test_import_watchlist(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            count = repo.import_watchlist([
                {"tmdb_id": 680, "media_type": "movie", "title": "Pulp Fiction",
                 "year": 1994, "added_at": 1691971200},
            ])
            self.assertEqual(count, 1)
            conn = repo._ensure_conn()
            row = conn.execute(
                "SELECT COUNT(*) FROM watchlist_items WHERE tmdb_id=680"
            ).fetchone()
            self.assertEqual(row[0], 1)

    def test_import_ratings_halves_to_star_scale(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            count = repo.import_ratings([
                {"tmdb_id": 1, "media_type": "movie", "title": "A",
                 "rating": 8, "rated_at": 1691971200},
                {"tmdb_id": 2, "media_type": "movie", "title": "B",
                 "rating": 11, "rated_at": 1691971200},
            ])
            self.assertEqual(count, 1)
            conn = repo._ensure_conn()
            rows = conn.execute("SELECT COUNT(*) FROM ratings").fetchone()
            self.assertEqual(rows[0], 1)
            stored = conn.execute(
                "SELECT rating FROM ratings WHERE tmdb_id=1"
            ).fetchone()
            self.assertEqual(stored[0], 4)

    def test_import_marks_duplicates_via_existing_ids(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            repo.import_watched([
                {"tmdb_id": 550, "media_type": "movie", "title": "Fight Club",
                 "year": 1999, "watched_at": 1691971200},
            ])
            existing = repo.get_existing_ids("watched_items")
            self.assertIn(550, existing)


class ServiceRefreshTest(unittest.TestCase):
    """get_movie/get_show refresh bypasses the cache and overwrites rows."""

    def _make_service(self, cached_poster=False):
        client = mock.Mock()
        client._image_url.side_effect = lambda path, size=None: path
        client.get_movie.return_value = {
            "id": 550, "title": "Fight Club", "release_date": "1999-10-15",
            "poster_path": "/poster550.jpg", "overview": "o",
            "runtime": 139, "vote_average": 8.4, "vote_count": 100,
            "imdb_id": "tt0137523",
            "genres": [{"id": 18, "name": "Drama"}],
            "belongs_to_collection": None,
            "tagline": "t", "certification": "R", "budget": 1, "revenue": 2,
        }
        cache = mock.Mock()
        if cached_poster:
            cached = Movie(
                tmdb_id=550, title="Fight Club", year=1999,
                poster_url="cached-poster.jpg",
            )
        else:
            cached = Movie(tmdb_id=550, title="Fight Club", year=1999)
        cache.get_media.return_value = cached
        return TmdbMetadataService(client, cache), client

    def test_plain_get_movie_uses_cache(self):
        service, client = self._make_service(cached_poster=True)
        movie = service.get_movie(550)
        client.get_movie.assert_not_called()
        self.assertEqual(movie.poster_url, "cached-poster.jpg")

    def test_refresh_bypasses_cache(self):
        service, client = self._make_service(cached_poster=True)
        movie = service.get_movie(550, refresh=True)
        client.get_movie.assert_called_once_with(550)
        self.assertEqual(movie.tmdb_id, 550)
        self.assertEqual(movie.poster_url, "/poster550.jpg")

    def test_refresh_overwrites_cache(self):
        service, client = self._make_service(cached_poster=True)
        service.get_movie(550, refresh=True)
        put = [c for c in service._cache.put_media.call_args_list
               if c.args and c.args[0].poster_url == "/poster550.jpg"]
        self.assertEqual(len(put), 1)


class BackfillTest(unittest.TestCase):
    def test_backfill_missing_posters_persists_urls(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "t.sqlite")
            repo = LocalMediaRepository(db)
            repo.initialize()
            repo.import_watched([
                {"tmdb_id": 550, "media_type": "movie", "title": "Fight Club",
                 "year": 1999, "watched_at": 1691971200},
                {"tmdb_id": 5001, "media_type": "episode",
                 "title": "Show", "show_tmdb_id": 999, "season_number": 1,
                 "episode_number": 1, "watched_at": 1691971200},
            ])

            from src.data.local.cache import MetadataCache
            from src.ui import import_dialog

            cache = MetadataCache(db)
            client = mock.Mock()
            client._image_url.side_effect = lambda path, size=None: path
            client.get_movie.return_value = {
                "id": 550, "title": "Fight Club",
                "release_date": "1999-10-15", "poster_path": "/550.jpg",
                "overview": "o", "runtime": 139, "vote_average": 8.4,
                "vote_count": 100, "genres": [],
                "belongs_to_collection": None,
            }
            client.get_tv.return_value = {
                "id": 999, "name": "Show", "first_air_date": "2020-01-01",
                "poster_path": "/999.jpg", "overview": "o",
                "vote_average": 8.0, "vote_count": 10, "genres": [],
                "status": "Returning Series",
            }
            service = TmdbMetadataService(client, cache)

            with mock.patch.object(
                import_dialog, "_prefetch_poster", return_value=True
            ) as prefetch:
                done = import_dialog.backfill_missing_posters(repo, service)
            self.assertEqual(done, 2)
            self.assertEqual(prefetch.call_count, 2)
            self.assertEqual(repo.get_media_missing_posters(), [])

            conn = repo._ensure_conn()
            for tmdb_id in (550, 999):
                url = conn.execute(
                    "SELECT poster_url FROM media_items WHERE tmdb_id=?",
                    (tmdb_id,),
                ).fetchone()[0]
                self.assertTrue(url, f"poster_url missing for {tmdb_id}")


class TraktExportWatchlistShowTest(unittest.TestCase):
    def test_watchlist_show_media_type_inferred(self):
        with tempfile.TemporaryDirectory() as d:
            zip_path = os.path.join(d, "export.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr(
                    "lists-watchlist.json",
                    json.dumps([{
                        "type": "show",
                        "listed_at": "2026-07-30T21:36:03.000Z",
                        "show": {
                            "title": "Neuromancer",
                            "year": 2027,
                            "ids": {"tmdb": 215528, "slug": "neuromancer"}
                        }
                    }])
                )
            items = TraktExport.parse(zip_path)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].media_type, "show")
            self.assertEqual(items[0].tmdb_id, 215528)
            self.assertEqual(items[0].title, "Neuromancer")
            self.assertEqual(items[0].target, "watchlist")


if __name__ == "__main__":
    unittest.main()
