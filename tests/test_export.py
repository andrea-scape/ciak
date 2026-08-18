import csv
import json
import os
import tempfile
import unittest

from src.data.export import (
    ExportData,
    write_trakt_csv,
    write_letterboxd_csv,
    write_imdb_csv,
    write_json,
)
from src.data.local.repository import LocalMediaRepository


class ExportDataTest(unittest.TestCase):
    def test_export_data_merges_all_categories(self):
        data = ExportData(
            watched=[
                {"tmdb_id": 550, "media_type": "movie", "title": "Fight Club",
                 "year": 1999, "watched_at": 1692000000, "imdb_id": "tt0137523"}
            ],
            watchlist=[
                {"tmdb_id": 680, "media_type": "movie", "title": "Pulp Fiction",
                 "year": 1994, "added_at": 1692000000}
            ],
            ratings=[
                {"tmdb_id": 550, "media_type": "movie", "rating": 5,
                 "rated_at": 1692000000, "title": "Fight Club", "year": 1999}
            ],
            collection=[
                {"tmdb_id": 550, "media_type": "movie", "title": "Fight Club",
                 "year": 1999, "collected_at": 1692000000}
            ],
        )
        self.assertEqual(len(data.watched), 1)
        self.assertEqual(len(data.watchlist), 1)


class TraktCsvTest(unittest.TestCase):
    def test_watched_row_format(self):
        watched = [{"tmdb_id": 550, "media_type": "movie", "title": "Fight Club",
                     "year": 1999, "watched_at": 1692000000, "imdb_id": "tt0137523"}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_trakt_csv(watched, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Title"], "Fight Club")
            self.assertEqual(rows[0]["Year"], "1999")
            self.assertEqual(rows[0]["Type"], "movie")
            self.assertEqual(rows[0]["IMDB ID"], "tt0137523")
            self.assertEqual(rows[0]["Watched Date"], "2023-08-14")
        finally:
            os.unlink(path)


class LetterboxdCsvTest(unittest.TestCase):
    def test_header_and_row(self):
        watched = [{"tmdb_id": 550, "media_type": "movie", "title": "Fight Club",
                     "year": 1999, "watched_at": 1692000000}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_letterboxd_csv(watched, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(reader.fieldnames[0], "Date")
            self.assertEqual(rows[0]["Name"], "Fight Club")
            self.assertEqual(rows[0]["Year"], "1999")
        finally:
            os.unlink(path)


class ImdbCsvTest(unittest.TestCase):
    def test_imdb_csv_columns(self):
        watched = [{"tmdb_id": 550, "media_type": "movie", "title": "Fight Club",
                     "year": 1999, "imdb_id": "tt0137523"}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_imdb_csv(watched, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(rows[0]["Const"], "tt0137523")
            self.assertEqual(rows[0]["Title"], "Fight Club")
            self.assertEqual(rows[0]["Type"], "movie")
        finally:
            os.unlink(path)


class TraktCsvExportDataTest(unittest.TestCase):
    def test_export_data_trakt_csv(self):
        data = ExportData(
            watched=[{"tmdb_id": 550, "media_type": "movie", "title": "Fight Club",
                       "year": 1999, "watched_at": 1692000000, "imdb_id": "tt0137523"}],
            watchlist=[{"tmdb_id": 680, "media_type": "movie", "title": "Pulp Fiction",
                         "year": 1994, "added_at": 1692000000}],
            ratings=[],
            collection=[],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_trakt_csv(data, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["Listing"], "Watched Movies")
            self.assertEqual(rows[1]["Listing"], "Watchlist")
        finally:
            os.unlink(path)

    def test_trakt_csv_merges_rating_into_watched_row(self):
        data = ExportData(
            watched=[{"tmdb_id": 550, "media_type": "movie", "title": "Fight Club",
                       "year": 1999, "watched_at": 1692000000}],
            watchlist=[],
            ratings=[{"tmdb_id": 550, "media_type": "movie", "rating": 8,
                       "rated_at": 1692000000}],
            collection=[],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_trakt_csv(data, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Listing"], "Watched Movies")
            self.assertEqual(rows[0]["Rating"], "8")
        finally:
            os.unlink(path)

    def test_trakt_csv_rated_not_watched_row(self):
        data = ExportData(
            watched=[],
            watchlist=[],
            ratings=[{"tmdb_id": 1, "media_type": "movie", "rating": 7,
                       "rated_at": 1692000000, "title": "Shawshank"}],
            collection=[],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_trakt_csv(data, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Listing"], "Ratings")
            self.assertEqual(rows[0]["Rating"], "7")
        finally:
            os.unlink(path)

    def test_writers_return_row_count(self):
        data = ExportData(
            watched=[{"tmdb_id": 1, "media_type": "movie", "title": "A",
                       "year": 2020}],
            watchlist=[{"tmdb_id": 2, "media_type": "movie", "title": "B",
                         "year": 2021}],
            ratings=[],
            collection=[],
        )
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(write_trakt_csv(data, os.path.join(d, "t.csv")), 2)
            self.assertEqual(write_letterboxd_csv(data, os.path.join(d, "l.csv")), 2)
            self.assertEqual(write_imdb_csv(data, os.path.join(d, "i.csv")), 2)
            self.assertEqual(write_json(data, os.path.join(d, "j.json")), 2)
            empty = ExportData()
            self.assertEqual(write_trakt_csv(empty, os.path.join(d, "e.csv")), 0)
            self.assertEqual(write_json(empty, os.path.join(d, "e.json")), 0)


class LetterboxdCsvExportDataTest(unittest.TestCase):
    def test_export_data_letterboxd_csv(self):
        data = ExportData(
            watched=[{"tmdb_id": 550, "media_type": "movie", "title": "Fight Club",
                       "year": 1999, "watched_at": 1692000000}],
            watchlist=[],
            ratings=[{"tmdb_id": 550, "media_type": "movie", "rating": 4,
                       "rated_at": 1692000000, "title": "Fight Club", "year": 1999}],
            collection=[],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_letterboxd_csv(data, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Name"], "Fight Club")
            self.assertEqual(rows[0]["Rating"], "2.0")
        finally:
            os.unlink(path)

    def test_letterboxd_includes_watchlist(self):
        data = ExportData(
            watched=[],
            watchlist=[{"tmdb_id": 680, "media_type": "movie",
                         "title": "Pulp Fiction", "year": 1994}],
            ratings=[],
            collection=[],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_letterboxd_csv(data, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Name"], "Pulp Fiction")
            self.assertEqual(rows[0]["Year"], "1994")
            self.assertEqual(rows[0]["Date"], "")
        finally:
            os.unlink(path)

    def test_letterboxd_rated_not_watched(self):
        data = ExportData(
            watched=[],
            watchlist=[],
            ratings=[{"tmdb_id": 1, "media_type": "movie", "rating": 3,
                       "rated_at": 1692000000, "title": "Shawshank",
                       "year": 1994}],
            collection=[],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_letterboxd_csv(data, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Name"], "Shawshank")
            self.assertEqual(rows[0]["Rating"], "1.5")
            self.assertEqual(rows[0]["Date"], "2023-08-14")
        finally:
            os.unlink(path)

    def test_letterboxd_rewatch_and_tags_are_blank(self):
        watched = [{"tmdb_id": 550, "media_type": "movie", "title": "Fight Club",
                     "year": 1999, "watched_at": 1692000000}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_letterboxd_csv(watched, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(rows[0]["Rewatch"], "")
            self.assertEqual(rows[0]["Tags"], "")
        finally:
            os.unlink(path)


class ImdbCsvExportDataTest(unittest.TestCase):
    def test_export_data_imdb_csv(self):
        data = ExportData(
            watched=[{"tmdb_id": 550, "media_type": "movie", "title": "Fight Club",
                       "year": 1999, "imdb_id": "tt0137523"}],
            watchlist=[{"tmdb_id": 680, "media_type": "movie", "title": "Pulp Fiction",
                         "year": 1994}],
            ratings=[],
            collection=[],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_imdb_csv(data, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["Position"], "1")
            self.assertEqual(rows[1]["Position"], "2")
        finally:
            os.unlink(path)


class EpisodeItemTest(unittest.TestCase):
    def test_episode_watched_item(self):
        watched = [{"tmdb_id": 76479, "media_type": "show",
                     "show_tmdb_id": 76479, "season_number": 1, "episode_number": 1,
                     "title": "Breaking Bad S01E01", "year": 2008,
                     "watched_at": 1692000000, "imdb_id": "tt0959621"}]
        for writer in (write_trakt_csv, write_letterboxd_csv, write_imdb_csv):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
                path = f.name
            try:
                writer(watched, path)
                with open(path) as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                self.assertGreaterEqual(len(rows), 1)
            finally:
                os.unlink(path)

    def test_episode_export_data(self):
        data = ExportData(
            watched=[{"tmdb_id": 76479, "media_type": "show",
                       "show_tmdb_id": 76479, "season_number": 1, "episode_number": 1,
                       "title": "Breaking Bad S01E01", "year": 2008,
                       "watched_at": 1692000000}],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_trakt_csv(data, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Type"], "show")
        finally:
            os.unlink(path)


class JsonExportTest(unittest.TestCase):
    def test_json_structure(self):
        data = ExportData(
            watched=[{"tmdb_id": 1, "title": "A"}],
            watchlist=[{"tmdb_id": 2, "title": "B"}],
            ratings=[],
            collection=[],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            write_json(data, path)
            with open(path) as f:
                parsed = json.load(f)
            self.assertIn("watched", parsed)
            self.assertIn("watchlist", parsed)
            self.assertIn("ratings", parsed)
            self.assertIn("collection", parsed)
            self.assertEqual(len(parsed["watched"]), 1)
            self.assertEqual(parsed["watched"][0]["title"], "A")
        finally:
            os.unlink(path)


class RepositoryExportTest(unittest.TestCase):
    def _make_repo(self, tmpdir):
        db = os.path.join(tmpdir, "test.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo

    def test_get_export_data_populates_all_fields(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            conn = repo._ensure_conn()
            conn.execute(
                "INSERT INTO media_items (tmdb_id, media_type, title, year, "
                "imdb_id, cached_at, updated_at) VALUES (1, 'movie', 'Test', 2020, "
                "'tt9999999', 0, 0)"
            )
            conn.commit()
            repo.mark_watched(1, "movie")
            repo.add_to_watchlist(1, "movie")
            repo.rate_item(1, "movie", 4)
            repo.add_to_collection(1, "movie")

            data = repo.get_export_data()
            self.assertIsInstance(data, ExportData)
            self.assertEqual(len(data.watched), 1)
            self.assertEqual(len(data.watchlist), 1)
            self.assertEqual(len(data.ratings), 1)
            self.assertEqual(len(data.collection), 1)
            self.assertEqual(data.watched[0]["title"], "Test")
            self.assertEqual(data.watched[0]["imdb_id"], "tt9999999")
            self.assertEqual(data.watchlist[0]["imdb_id"], "tt9999999")


class ExportIntegrationTest(unittest.TestCase):
    """Test the full pipeline: repo -> ExportData -> file write."""

    def _make_repo(self, tmpdir):
        db = os.path.join(tmpdir, "test.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo

    def _make_repo_with_data(self, tmpdir):
        db = os.path.join(tmpdir, "test.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        conn = repo._ensure_conn()
        for tmdb_id, title, year, imdb in [
            (550, "Fight Club", 1999, "tt0137523"),
            (680, "Pulp Fiction", 1994, "tt0110912"),
        ]:
            conn.execute(
                "INSERT INTO media_items (tmdb_id, media_type, title, year, "
                "imdb_id, cached_at, updated_at) VALUES (?, 'movie', ?, ?, ?, 0, 0)",
                (tmdb_id, title, year, imdb),
            )
        conn.commit()
        repo.mark_watched(550, "movie")
        repo.mark_watched(680, "movie")
        repo.rate_item(550, "movie", 5)
        repo.add_to_watchlist(680, "movie")
        repo.add_to_collection(550, "movie")
        return repo

    def test_trakt_csv_full_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo_with_data(d)
            data = repo.get_export_data()
            path = os.path.join(d, "export.csv")
            write_trakt_csv(data, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 4)
            titles = {r["Title"] for r in rows}
            self.assertIn("Fight Club", titles)
            self.assertIn("Pulp Fiction", titles)

    def test_imdb_csv_uses_imdb_id_from_cache(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo_with_data(d)
            data = repo.get_export_data()
            path = os.path.join(d, "export.csv")
            write_imdb_csv(data, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            fight_club = next(r for r in rows if r["Title"] == "Fight Club")
            self.assertEqual(fight_club["Const"], "tt0137523")
            self.assertEqual(
                fight_club["URL"], "https://www.imdb.com/title/tt0137523/"
            )

    def test_imdb_csv_dedupes_watched_and_rated(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            conn = repo._ensure_conn()
            conn.execute(
                "INSERT INTO media_items (tmdb_id, media_type, title, year, "
                "imdb_id, cached_at, updated_at) VALUES "
                "(1, 'movie', 'Rated Movie', 2020, 'tt1111111', 0, 0)"
            )
            conn.commit()
            repo.mark_watched(1, "movie")
            repo.rate_item(1, "movie", 5)
            data = repo.get_export_data()
            path = os.path.join(d, "export.csv")
            write_imdb_csv(data, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Title"], "Rated Movie")
            self.assertEqual(rows[0]["Rating"], "5")
            self.assertEqual(rows[0]["Const"], "tt1111111")

    def test_imdb_csv_rated_not_watched(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            conn = repo._ensure_conn()
            conn.execute(
                "INSERT INTO media_items (tmdb_id, media_type, title, year, "
                "imdb_id, cached_at, updated_at) VALUES "
                "(2, 'movie', 'Only Rated', 2019, 'tt2222222', 0, 0)"
            )
            conn.commit()
            repo.rate_item(2, "movie", 3)
            data = repo.get_export_data()
            path = os.path.join(d, "export.csv")
            write_imdb_csv(data, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Title"], "Only Rated")
            self.assertEqual(rows[0]["Rating"], "3")
            self.assertEqual(rows[0]["Position"], "1")

    def test_json_full_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo_with_data(d)
            data = repo.get_export_data()
            path = os.path.join(d, "export.json")
            write_json(data, path)
            with open(path) as f:
                parsed = json.load(f)
            self.assertEqual(len(parsed["watched"]), 2)
            self.assertEqual(len(parsed["ratings"]), 1)
            self.assertEqual(len(parsed["watchlist"]), 1)
            self.assertEqual(len(parsed["collection"]), 1)
            self.assertIn("exported_at", parsed)

    def test_export_data_empty_database(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo_with_data(d)
            conn = repo._ensure_conn()
            for table in ("watched_items", "watchlist_items", "ratings", "collection_items"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
            data = repo.get_export_data()
            self.assertEqual(data.watched, [])
            self.assertEqual(data.watchlist, [])
            self.assertEqual(data.ratings, [])
            self.assertEqual(data.collection, [])

    def test_letterboxd_csv_rating_conversion(self):
        watched = [
            {"tmdb_id": 1, "media_type": "movie", "title": "A",
             "year": 2020, "watched_at": 1692000000, "rating": 4},
            {"tmdb_id": 2, "media_type": "movie", "title": "B",
             "year": 2021, "watched_at": 1692000000, "rating": 3},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            write_letterboxd_csv(watched, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(rows[0]["Rating"], "2.0")
            self.assertEqual(rows[1]["Rating"], "1.5")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
