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
            self.assertIn("2023", rows[0]["Watched Date"])  # 1692000000 = Aug 2023
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


if __name__ == "__main__":
    unittest.main()
