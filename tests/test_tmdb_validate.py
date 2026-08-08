import unittest
from unittest import mock

import httpx

from src.data.tmdb.client import TmdbClient, TMDB_BASE


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", f"{TMDB_BASE}/configuration")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=request, response=response
            )


class FakeClient:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def get(self, *args, **kwargs):
        return FakeResponse(self.status_code)


def make_client(status_code=200):
    client = TmdbClient(api_key="test-key")
    fake = FakeClient(status_code=status_code)
    client._http = mock.Mock(return_value=fake)
    return client


class ValidateKeyTest(unittest.TestCase):
    def test_valid_key(self):
        self.assertEqual(make_client(200).validate_key(), "valid")

    def test_rejected_key_is_invalid(self):
        self.assertEqual(make_client(401).validate_key(), "invalid")

    def test_network_failure_is_unreachable(self):
        client = make_client()
        client._http.side_effect = httpx.ConnectError("boom")
        self.assertEqual(client.validate_key(), "unreachable")

    def test_other_http_error_is_unreachable(self):
        self.assertEqual(make_client(500).validate_key(), "unreachable")
