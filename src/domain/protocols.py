from typing import Protocol, runtime_checkable


@runtime_checkable
class UserMediaRepository(Protocol):
    """Manages user-specific state: watched, watchlist, ratings, collection.

    This is the core abstraction for user data. All implementations (local
    SQLite, future Trakt sync) must satisfy this protocol so the UI layer
    remains unaware of where and how data is persisted.
    """

    def mark_watched(
        self,
        tmdb_id: int,
        media_type: str,
        show_tmdb_id: int | None = None,
        season_number: int | None = None,
        episode_number: int | None = None,
    ) -> None:
        """Record a media item as watched.

        For movies: only tmdb_id and media_type are required.
        For shows/episodes: provide show_tmdb_id, season_number, episode_number.
        If show_tmdb_id is provided without episode info, mark the whole show as watched.
        """
        ...

    def mark_unwatched(
        self,
        tmdb_id: int,
        media_type: str,
        show_tmdb_id: int | None = None,
        season_number: int | None = None,
        episode_number: int | None = None,
    ) -> None:
        """Remove a watched record."""
        ...

    def is_watched(
        self,
        tmdb_id: int,
        media_type: str,
        show_tmdb_id: int | None = None,
        season_number: int | None = None,
        episode_number: int | None = None,
    ) -> bool:
        """Check whether a specific item or episode has been marked watched."""
        ...

    def get_watched_list(self, media_type: str | None = None) -> list:
        """Return all watched items, optionally filtered by media_type."""
        ...

    def add_to_watchlist(self, tmdb_id: int, media_type: str) -> None:
        """Add an item to the user's watchlist."""
        ...

    def remove_from_watchlist(self, tmdb_id: int, media_type: str) -> None:
        """Remove an item from the user's watchlist."""
        ...

    def get_watchlist(self, media_type: str | None = None) -> list:
        """Return all watchlist items, optionally filtered by media_type."""
        ...

    def rate_item(self, tmdb_id: int, media_type: str, rating: int) -> None:
        """Rate a media item (1-5).  Overwrites any existing rating."""
        ...

    def remove_rating(self, tmdb_id: int, media_type: str) -> None:
        """Remove the user's rating for an item."""
        ...

    def get_ratings(self, media_type: str | None = None) -> list:
        """Return all rated items, optionally filtered by media_type."""
        ...

    def add_to_collection(self, tmdb_id: int, media_type: str) -> None:
        """Add an item to the user's collection."""
        ...

    def remove_from_collection(self, tmdb_id: int, media_type: str) -> None:
        """Remove an item from the user's collection."""
        ...

    def get_collection(self, media_type: str | None = None) -> list:
        """Return all collection items, optionally filtered by media_type."""
        ...

    def get_stats(self) -> "Stats":
        """Return aggregate user statistics (counts per category)."""
        ...


class MetadataService(Protocol):
    """Read-only metadata from an external provider (TMDB).

    This service fetches media information (titles,
    plots, posters, cast, etc.) and returns plain domain models.  It
    does NOT handle user-specific state (watched, ratings, etc.).
    """

    def search_movies(self, query: str) -> list["Movie"]:
        """Search for movies matching the given query string."""
        ...

    def search_shows(self, query: str) -> list["Show"]:
        """Search for TV shows matching the given query string."""
        ...

    def get_trending(self, media_type: str = "all") -> list:
        """Return currently trending titles (movies, shows, or both)."""
        ...

    def get_popular_movies(self) -> list["Movie"]:
        """Return the most popular movies currently."""
        ...

    def get_popular_shows(self) -> list["Show"]:
        """Return the most popular TV shows currently."""
        ...

    def get_movie(self, tmdb_id: int) -> "Movie":
        """Return full detail for a single movie."""
        ...

    def get_show(self, tmdb_id: int) -> "Show":
        """Return full detail for a single TV show."""
        ...

    def get_show_seasons(self, tmdb_id: int) -> list["Season"]:
        """Return all seasons for a TV show."""
        ...

    def get_season_episodes(
        self, show_tmdb_id: int, season_number: int
    ) -> list["Episode"]:
        """Return all episodes for a specific season of a TV show."""
        ...

    def get_movie_cast(self, tmdb_id: int) -> list["CastMember"]:
        """Return cast and crew for a movie."""
        ...

    def get_show_cast(self, tmdb_id: int) -> list["CastMember"]:
        """Return cast and crew for a TV show."""
        ...

    def get_related_movies(self, tmdb_id: int) -> list["Movie"]:
        """Return movies similar to the given movie."""
        ...

    def get_related_shows(self, tmdb_id: int) -> list["Show"]:
        """Return shows similar to the given show."""
        ...

    def get_collection(self, collection_id: int) -> "Collection | None":
        """Return a TMDB movie collection (saga/franchise) and its parts."""
        ...

    def get_calendar(self) -> list:
        """Return upcoming episodes for the current week (calendar view)."""
        ...
