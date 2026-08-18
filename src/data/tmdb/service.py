"""High-level metadata service that wraps TmdbClient with caching.

Checks the local cache first (MetadataCache), falls back to the TMDB API
on a miss or TTL expiration, and populates the cache with fresh data.
Returns domain model instances (Movie, Show, Season, etc.), never raw
API responses.
"""

from ...domain.exceptions import NetworkError
from ...domain.models import Movie, Show, Season, Episode, CastMember
from .client import TmdbClient
from ..local.cache import MetadataCache
import httpx


class TmdbMetadataService:
    """Read-only TMDB metadata, backed by a local TTL cache."""

    def __init__(self, client: TmdbClient, cache: MetadataCache):
        self._client = client
        self._cache = cache

    def close(self) -> None:
        """Close underlying HTTP clients (safe to call on shutdown)."""
        self._client.close()

    # ------------------------------------------------------------------
    # Search (never cached; fresh results expected)
    # ------------------------------------------------------------------

    def search_movies(self, query: str) -> list[Movie]:
        try:
            data = self._client.search_movie(query)
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"TMDB search failed: {exc}") from exc
        return [self._raw_to_movie(item) for item in data.get("results", [])]

    def search_shows(self, query: str) -> list[Show]:
        try:
            data = self._client.search_tv(query)
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"TMDB search failed: {exc}") from exc
        return [self._raw_to_show(item) for item in data.get("results", [])]

    # ------------------------------------------------------------------
    # Import resolution helpers
    # ------------------------------------------------------------------

    def resolve_imdb(self, imdb_id: str) -> Movie | Show | None:
        """Resolve an IMDb id to a Movie or Show, or None on a miss."""
        try:
            data = self._client.find_by_imdb(imdb_id)
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"TMDB lookup failed: {exc}") from exc
        movies = data.get("movie_results") or []
        shows = data.get("tv_results") or []
        if movies:
            return self._raw_to_movie(movies[0])
        if shows:
            return self._raw_to_show(shows[0])
        return None

    def search_best(
        self, query: str, year: int | None, media_type: str | None
    ) -> Movie | Show | None:
        """Return the top search hit matching the title (and year when known).

        Prefers an exact year match; falls back to the first result.
        """
        try:
            raw_results = None
            if media_type in (None, "movie", "episode"):
                movies = self.search_movies(query)
                if movies:
                    picked = self._pick_best(movies, query, year)
                    raw_results = picked
            if raw_results is None and media_type in (None, "show", "episode"):
                shows = self.search_shows(query)
                if shows:
                    picked = self._pick_best(shows, query, year)
                    raw_results = picked
            return raw_results
        except NetworkError:
            return None

    def _pick_best(self, results, query: str, year: int | None):
        if year is not None:
            for result in results:
                if result.year == year:
                    return result
        return results[0] if results else None

    # ------------------------------------------------------------------
    # Detail (cache-first)
    # ------------------------------------------------------------------

    def get_movie(self, tmdb_id: int) -> Movie:
        cached = self._cache.get_media(tmdb_id)
        if cached is not None:
            return cached
        try:
            raw = self._client.get_movie(tmdb_id)
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"Failed to fetch movie {tmdb_id}: {exc}") from exc
        movie = self._raw_to_movie(raw)
        self._cache.put_media(movie)
        return movie

    def get_show(self, tmdb_id: int) -> Show:
        cached = self._cache.get_media(tmdb_id)
        if cached is not None:
            return cached
        try:
            raw = self._client.get_tv(tmdb_id)
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"Failed to fetch show {tmdb_id}: {exc}") from exc
        show = self._raw_to_show(raw)
        self._cache.put_media(show)
        return show

    def get_show_seasons(self, tmdb_id: int) -> list[Season]:
        cached = self._cache.get_seasons(tmdb_id)
        if cached is not None:
            return cached
        try:
            raw = self._client.get_tv(tmdb_id)
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"Failed to fetch show seasons {tmdb_id}: {exc}") from exc
        seasons = [
            self._raw_to_season(s, tmdb_id) for s in raw.get("seasons", [])
        ]
        self._cache.put_seasons(tmdb_id, seasons)
        return seasons

    def get_season_episodes(
        self, show_tmdb_id: int, season_number: int
    ) -> list[Episode]:
        cached = self._cache.get_episodes(show_tmdb_id, season_number)
        if cached is not None:
            return cached
        try:
            raw = self._client.get_tv_season(show_tmdb_id, season_number)
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(
                f"Failed to fetch episodes S{season_number:02d} for {show_tmdb_id}: {exc}"
            ) from exc
        episodes = [
            self._raw_to_episode(ep, show_tmdb_id, season_number)
            for ep in raw.get("episodes", [])
        ]
        self._cache.put_episodes(show_tmdb_id, season_number, episodes)
        return episodes

    # ------------------------------------------------------------------
    # Cast
    # ------------------------------------------------------------------

    def get_movie_cast(self, tmdb_id: int) -> list[CastMember]:
        try:
            data = self._client.get_movie_credits(tmdb_id)
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"Failed to fetch movie cast {tmdb_id}: {exc}") from exc
        director = next(
            (c for c in data.get("crew", []) if c.get("job") == "Director"), None
        )
        leader = []
        if director:
            leader.append(
                CastMember(
                    person_id=director["id"],
                    name=director.get("name", "Unknown"),
                    character="Director",
                    photo_url=self._client._image_url(director.get("profile_path"), "w185"),
                )
            )
        cast = [self._raw_to_cast(m) for m in data.get("cast", [])[:20 - len(leader)]]
        return leader + cast

    def get_show_cast(self, tmdb_id: int) -> list[CastMember]:
        try:
            data = self._client.get_tv_credits(tmdb_id)
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"Failed to fetch show cast {tmdb_id}: {exc}") from exc
        creators = []
        try:
            creators = self.get_show(tmdb_id).creators or []
        except NetworkError:
            creators = []
        leader = [
            CastMember(
                person_id=c.get("id"),
                name=c.get("name", "Unknown"),
                character="Showrunner",
                photo_url=c.get("photo_url"),
            )
            for c in creators
        ]
        cast = [self._raw_to_cast(m) for m in data.get("cast", [])[:20 - len(leader)]]
        return leader + cast

    # ------------------------------------------------------------------
    # Related / Similar
    # ------------------------------------------------------------------

    def get_related_movies(self, tmdb_id: int) -> list[Movie]:
        source = self.get_movie(tmdb_id)
        priority, secondary = [], []

        # Collection / saga items first
        if source.collection_id:
            try:
                col = self._client.get_collection(source.collection_id)
                priority = [
                    self._raw_to_movie(p)
                    for p in col.get("parts", [])
                    if p.get("id") != tmdb_id
                ]
            except httpx.HTTPError:
                pass

        # Genre + year discover
        try:
            if source.genre_ids and source.year:
                data = self._client.discover_movie(
                    genre_ids=source.genre_ids,
                    year_min=source.year - 7,
                    year_max=source.year + 7,
                )
            else:
                data = self._client.get_movie_similar(tmdb_id)
        except (httpx.HTTPError, ValueError) as exc:
            if not priority:
                raise NetworkError(f"Failed to fetch related for {tmdb_id}: {exc}") from exc
            data = {}

        secondary = [
            item for item in data.get("results", [])
            if item.get("id") != tmdb_id
        ]
        if source.year:
            lo, hi = source.year - 7, source.year + 7
            secondary = [
                item for item in secondary
                if (y := self._result_year(item)) is not None and lo <= y <= hi
            ]

        seen = {m.tmdb_id for m in priority}
        merged = priority[:]
        for item in secondary:
            if item.get("id") not in seen:
                seen.add(item["id"])
                merged.append(self._raw_to_movie(item))
        return merged[:12]

    def get_related_shows(self, tmdb_id: int) -> list[Show]:
        source = self.get_show(tmdb_id)
        results = self._related_items(
            tmdb_id,
            source,
            discover_fn=self._client.discover_tv,
            fallback_fn=self._client.get_tv_similar,
        )
        return [self._raw_to_show(item) for item in results]

    def _related_items(self, tmdb_id, source, discover_fn, fallback_fn) -> list[dict]:
        """Return items sharing source genres and released within +/-7 years.

        Uses TMDB discover when the source has both genres and a year; falls
        back to the '/similar' endpoint otherwise.  The +/-7 year window is
        always enforced client-side as a safety net.
        """
        try:
            if source.genre_ids and source.year:
                data = discover_fn(
                    genre_ids=source.genre_ids,
                    year_min=source.year - 7,
                    year_max=source.year + 7,
                )
            else:
                data = fallback_fn(tmdb_id)
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"Failed to fetch related for {tmdb_id}: {exc}") from exc
        results = [item for item in data.get("results", []) if item.get("id") != tmdb_id]
        if source.year:
            lo, hi = source.year - 7, source.year + 7
            results = [
                item for item in results
                if (y := self._result_year(item)) is not None and lo <= y <= hi
            ]
        return results[:20]

    @staticmethod
    def _result_year(raw: dict) -> int | None:
        for key in ("release_date", "first_air_date"):
            value = raw.get(key)
            if value:
                try:
                    return int(value[:4])
                except (ValueError, TypeError):
                    return None
        return None

    # ------------------------------------------------------------------
    # Discovery (never cached; dynamic lists)
    # ------------------------------------------------------------------

    def get_trending(self, media_type: str = "all") -> list:
        try:
            data = self._client.get_trending(media_type)
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"Failed to fetch trending: {exc}") from exc
        results = []
        for item in data.get("results", []):
            # Type-specific endpoints (/trending/movie/week) omit media_type.
            kind = item.get("media_type") or media_type
            if kind == "movie":
                results.append(self._raw_to_movie(item))
            elif kind == "tv":
                results.append(self._raw_to_show(item))
        return results

    def get_recent_movies(self) -> list[Movie]:
        """Most recently released movies (discover, release date desc)."""
        try:
            data = self._client.discover_movie(sort_by="primary_release_date.desc")
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"Failed to fetch recent movies: {exc}") from exc
        return [self._raw_to_movie(item) for item in data.get("results", [])]

    def get_recent_shows(self) -> list[Show]:
        """Most recently aired TV shows (discover, first air date desc)."""
        try:
            data = self._client.discover_tv(sort_by="first_air_date.desc")
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"Failed to fetch recent shows: {exc}") from exc
        return [self._raw_to_show(item) for item in data.get("results", [])]

    def get_popular_movies(self) -> list[Movie]:
        try:
            data = self._client.get_popular_movies()
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"Failed to fetch popular movies: {exc}") from exc
        return [self._raw_to_movie(item) for item in data.get("results", [])]

    def get_popular_shows(self) -> list[Show]:
        try:
            data = self._client.get_popular_tv()
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"Failed to fetch popular shows: {exc}") from exc
        return [self._raw_to_show(item) for item in data.get("results", [])]

    def get_calendar(self) -> list:
        """Return upcoming TV episodes (on the air)."""
        try:
            data = self._client.get_upcoming_tv()
        except (httpx.HTTPError, ValueError) as exc:
            raise NetworkError(f"Failed to fetch calendar: {exc}") from exc
        return [self._raw_to_show(item) for item in data.get("results", [])]

    def get_latest_season_episodes(self, show_tmdb_id: int):
        seasons = self.get_show_seasons(show_tmdb_id)
        latest = 0
        for s in seasons:
            if s.season_number > latest and s.season_number >= 1:
                latest = s.season_number
        if latest == 0:
            return []
        return self.get_season_episodes(show_tmdb_id, latest)

    # ------------------------------------------------------------------
    # Raw → Model converters
    # ------------------------------------------------------------------

    def _raw_to_movie(self, raw: dict) -> Movie:
        year = None
        if raw.get("release_date"):
            year = int(raw["release_date"][:4])
        return Movie(
            tmdb_id=raw["id"],
            title=raw.get("title", "Unknown"),
            year=year,
            release_date=raw.get("release_date"),
            overview=raw.get("overview"),
            runtime=raw.get("runtime"),
            rating=raw.get("vote_average"),
            votes=raw.get("vote_count"),
            poster_url=self._client._image_url(raw.get("poster_path")),
            backdrop_url=self._client._image_url(raw.get("backdrop_path"), "w780"),
            imdb_id=raw.get("imdb_id"),
            genres=[g["name"] for g in raw.get("genres", [])],
            genre_ids=self._extract_genre_ids(raw),
            collection_id=(raw.get("belongs_to_collection") or {}).get("id"),
            tagline=raw.get("tagline"),
            budget=raw.get("budget"),
            revenue=raw.get("revenue"),
        )

    def _raw_to_show(self, raw: dict) -> Show:
        year = None
        if raw.get("first_air_date"):
            year = int(raw["first_air_date"][:4])
        episode_runtimes = raw.get("episode_run_time") or [0]
        next_ep = raw.get("next_episode_to_air") or {}
        return Show(
            tmdb_id=raw["id"],
            title=raw.get("name", "Unknown"),
            year=year,
            overview=raw.get("overview"),
            status=raw.get("status"),
            runtime=episode_runtimes[0] if episode_runtimes else None,
            rating=raw.get("vote_average"),
            votes=raw.get("vote_count"),
            poster_url=self._client._image_url(raw.get("poster_path")),
            backdrop_url=self._client._image_url(raw.get("backdrop_path"), "w780"),
            genres=[g["name"] for g in raw.get("genres", [])],
            genre_ids=self._extract_genre_ids(raw),
            tagline=raw.get("tagline"),
            next_episode_air_date=next_ep.get("air_date"),
            next_episode_season=next_ep.get("season_number"),
            next_episode_number=next_ep.get("episode_number"),
            next_episode_name=next_ep.get("name"),
            next_episode_still=self._client._image_url(next_ep.get("still_path"), "w300"),
            creators=[
                {
                    "id": c.get("id"),
                    "name": c.get("name", "Unknown"),
                    "photo_url": self._client._image_url(c.get("profile_path"), "w185"),
                }
                for c in raw.get("created_by", [])
            ],
        )

    @staticmethod
    def _extract_genre_ids(raw: dict) -> list[int]:
        if raw.get("genres") and isinstance(raw["genres"], list):
            return [g["id"] for g in raw["genres"] if isinstance(g, dict) and g.get("id")]
        if raw.get("genre_ids") and isinstance(raw["genre_ids"], list):
            return [int(g) for g in raw["genre_ids"]]
        return []

    def _raw_to_season(self, raw: dict, show_tmdb_id: int) -> Season:
        return Season(
            tmdb_id=raw.get("id", 0),
            show_tmdb_id=show_tmdb_id,
            season_number=raw["season_number"],
            name=raw.get("name"),
            overview=raw.get("overview"),
            poster_url=self._client._image_url(raw.get("poster_path")),
            episode_count=raw.get("episode_count", 0),
        )

    def _raw_to_episode(
        self, raw: dict, show_tmdb_id: int, season_number: int
    ) -> Episode:
        return Episode(
            tmdb_id=raw.get("id", 0),
            show_tmdb_id=show_tmdb_id,
            season_number=season_number,
            episode_number=raw["episode_number"],
            title=raw.get("name", f"Episode {raw['episode_number']}"),
            overview=raw.get("overview"),
            runtime=raw.get("runtime"),
            rating=raw.get("vote_average"),
            air_date=raw.get("air_date"),
            poster_url=self._client._image_url(raw.get("still_path"), "w300"),
        )

    def _raw_to_cast(self, raw: dict) -> CastMember:
        return CastMember(
            person_id=raw["id"],
            name=raw.get("name", "Unknown"),
            character=raw.get("character"),
            photo_url=self._client._image_url(raw.get("profile_path"), "w185"),
        )
