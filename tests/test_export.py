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
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["Rating"], "2.0")
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
        from src.data.local.repository import LocalMediaRepository
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo

    def test_get_export_data_populates_all_fields(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            conn = repo._ensure_conn()
            conn.execute(
                "INSERT INTO media_items (tmdb_id, media_type, title, year, "
                "cached_at, updated_at) VALUES (1, 'movie', 'Test', 2020, 0, 0)"
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


if __name__ == "__main__":
    unittest.main()
