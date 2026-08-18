import json
import os
import tempfile
import unittest

from src.data.importers import (
    GenericJSON,
    IMDbCSV,
    ImportItem,
    LetterboxdCSV,
    Matcher,
    TraktCSV,
    date_to_ts,
    select_parser,
)
from src.data.local.repository import LocalMediaRepository


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


if __name__ == "__main__":
    unittest.main()
