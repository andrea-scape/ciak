"""Low-level HTTP client for The Movie Database (TMDB) API v3.

All responses are returned as raw Python dicts.  No domain model
construction happens here; that is the responsibility of the
TmdbMetadataService layer above.
"""

import threading

import httpx

TMDB_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"
DEFAULT_TIMEOUT = 20.0

POSTER_SIZES = ("w185", "w342", "w500", "w780", "original")
BACKDROP_SIZES = ("w300", "w780", "w1280", "original")


class TmdbClient:
    """Minimal synchronous HTTP wrapper around the TMDB API.

    Thread-safety: each thread lazily creates its own httpx.Client, so
    concurrent fetches from worker threads never share a connection pool.
    """

    def __init__(self, api_key: str, hide_adult_fn=None):
        self._api_key = api_key
        self._hide_adult_fn = hide_adult_fn
        self._local = threading.local()
        self._clients: list[httpx.Client] = []
        self._clients_lock = threading.Lock()

    def close(self) -> None:
        with self._clients_lock:
            clients = self._clients
            self._clients = []
        for client in clients:
            try:
                client.close()
            except httpx.HTTPError:
                pass
        try:
            self._local.client = None
        except AttributeError:
            pass

    def _http(self) -> httpx.Client:
        client = getattr(self._local, "client", None)
        if client is None:
            client = httpx.Client(
                timeout=DEFAULT_TIMEOUT,
                limits=httpx.Limits(
                    max_connections=12, max_keepalive_connections=4
                ),
            )
            self._local.client = client
            with self._clients_lock:
                self._clients.append(client)
        return client

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET a TMDB endpoint.  Injects the API key automatically."""
        p = {"api_key": self._api_key, "language": "en-US"}
        if self._hide_adult_fn is not None and self._hide_adult_fn():
            p["include_adult"] = "false"
        if params:
            p.update(params)
        resp = self._http().get(f"{TMDB_BASE}{path}", params=p)
        resp.raise_for_status()
        return resp.json()

    def _image_url(
        self, path: str | None, size: str = "w500", default: str | None = None
    ) -> str | None:
        """Build a full image URL from a TMDB image path."""
        if not path:
            return default
        return f"{IMAGE_BASE}/{size}{path}"

    def validate_key(self) -> str:
        """Return "valid", "invalid", or "unreachable" for the configured key."""
        try:
            resp = self._http().get(
                f"{TMDB_BASE}/configuration", params={"api_key": self._api_key}
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                return "invalid"
            return "unreachable"
        except httpx.HTTPError:
            return "unreachable"
        return "valid"

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_movie(self, query: str, page: int = 1) -> dict:
        return self._get("/search/movie", {"query": query, "page": page})

    def search_tv(self, query: str, page: int = 1) -> dict:
        return self._get("/search/tv", {"query": query, "page": page})

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    def get_movie(self, movie_id: int) -> dict:
        return self._get(f"/movie/{movie_id}")

    def get_tv(self, tv_id: int) -> dict:
        return self._get(f"/tv/{tv_id}")

    def find_by_imdb(self, imdb_id: str) -> dict:
        """Resolve an IMDb id to TMDB movie/tv results."""
        return self._get(
            f"/find/{imdb_id}", {"external_source": "imdb_id"}
        )

    def get_tv_season(self, tv_id: int, season_number: int) -> dict:
        return self._get(f"/tv/{tv_id}/season/{season_number}")

    # ------------------------------------------------------------------
    # Credits (cast & crew)
    # ------------------------------------------------------------------

    def get_movie_credits(self, movie_id: int) -> dict:
        return self._get(f"/movie/{movie_id}/credits")

    def get_tv_credits(self, tv_id: int) -> dict:
        return self._get(f"/tv/{tv_id}/credits")

    # ------------------------------------------------------------------
    # Discovery / lists
    # ------------------------------------------------------------------

    def get_trending(self, media_type: str = "all", time_window: str = "week") -> dict:
        return self._get(f"/trending/{media_type}/{time_window}")

    def get_popular_movies(self, page: int = 1) -> dict:
        return self._get("/movie/popular", {"page": page})

    def get_popular_tv(self, page: int = 1) -> dict:
        return self._get("/tv/popular", {"page": page})

    def get_movie_similar(self, movie_id: int, page: int = 1) -> dict:
        return self._get(f"/movie/{movie_id}/similar", {"page": page})

    def get_tv_similar(self, tv_id: int, page: int = 1) -> dict:
        return self._get(f"/tv/{tv_id}/similar", {"page": page})

    def discover_movie(
        self,
        genre_ids: list[int] | None = None,
        year_min: int | None = None,
        year_max: int | None = None,
        page: int = 1,
        sort_by: str = "popularity.desc",
    ) -> dict:
        params = {"sort_by": sort_by, "page": page}
        if genre_ids:
            params["with_genres"] = "|".join(str(g) for g in genre_ids)
        if year_min:
            params["primary_release_date.gte"] = f"{year_min}-01-01"
        if year_max:
            params["primary_release_date.lte"] = f"{year_max}-12-31"
        return self._get("/discover/movie", params)

    def discover_tv(
        self,
        genre_ids: list[int] | None = None,
        year_min: int | None = None,
        year_max: int | None = None,
        page: int = 1,
        sort_by: str = "popularity.desc",
    ) -> dict:
        params = {"sort_by": sort_by, "page": page}
        if genre_ids:
            params["with_genres"] = "|".join(str(g) for g in genre_ids)
        if year_min:
            params["first_air_date.gte"] = f"{year_min}-01-01"
        if year_max:
            params["first_air_date.lte"] = f"{year_max}-12-31"
        return self._get("/discover/tv", params)

    def get_upcoming_tv(self, page: int = 1) -> dict:
        return self._get("/tv/on_the_air", {"page": page})

    def get_collection(self, collection_id: int) -> dict:
        return self._get(f"/collection/{collection_id}")

    def get_certifications(self) -> dict:
        return self._get("/certification/movie/list")
