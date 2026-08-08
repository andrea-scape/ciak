from dataclasses import dataclass, field


@dataclass
class Movie:
    tmdb_id: int
    title: str
    year: int | None = None
    overview: str | None = None
    runtime: int | None = None
    rating: float | None = None
    votes: int | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    imdb_id: str | None = None
    genres: list[str] = field(default_factory=list)
    genre_ids: list[int] = field(default_factory=list)
    collection_id: int | None = None
    tagline: str | None = None
    certification: str | None = None
    media_type: str = "movie"


@dataclass
class Show:
    tmdb_id: int
    title: str
    year: int | None = None
    overview: str | None = None
    status: str | None = None
    runtime: int | None = None
    rating: float | None = None
    votes: int | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    imdb_id: str | None = None
    genres: list[str] = field(default_factory=list)
    genre_ids: list[int] = field(default_factory=list)
    seasons: list["Season"] = field(default_factory=list)
    tagline: str | None = None
    certification: str | None = None
    media_type: str = "show"
    next_episode_air_date: str | None = None
    next_episode_season: int | None = None
    next_episode_number: int | None = None
    next_episode_name: str | None = None
    next_episode_still: str | None = None


@dataclass
class Episode:
    tmdb_id: int
    show_tmdb_id: int
    season_number: int
    episode_number: int
    title: str
    overview: str | None = None
    runtime: int | None = None
    rating: float | None = None
    air_date: str | None = None
    poster_url: str | None = None


@dataclass
class Season:
    tmdb_id: int
    show_tmdb_id: int
    season_number: int
    name: str | None = None
    overview: str | None = None
    poster_url: str | None = None
    episode_count: int = 0
    episodes: list[Episode] = field(default_factory=list)


@dataclass
class CastMember:
    person_id: int
    name: str
    character: str | None = None
    photo_url: str | None = None


@dataclass
class Stats:
    movies_watched: int = 0
    shows_watched: int = 0
    episodes_watched: int = 0
    watchlist_items: int = 0
    ratings: int = 0
    collection_items: int = 0
