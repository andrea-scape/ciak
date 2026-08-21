from dataclasses import dataclass, field


@dataclass
class Movie:
    tmdb_id: int
    title: str
    year: int | None = None
    release_date: str | None = None
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
    collection_name: str | None = None
    tagline: str | None = None
    certification: str | None = None
    budget: int | None = None
    revenue: int | None = None
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
    creators: list[dict] = field(default_factory=list)


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
class StreamingProvider:
    provider_id: int
    provider_name: str
    logo_url: str | None = None
    display_priority: int = 0
    offering_type: str = "flatrate"


@dataclass
class StreamingInfo:
    country_code: str
    flatrate: list[StreamingProvider] = field(default_factory=list)
    rent: list[StreamingProvider] = field(default_factory=list)
    buy: list[StreamingProvider] = field(default_factory=list)
    ads: list[StreamingProvider] = field(default_factory=list)
    free: list[StreamingProvider] = field(default_factory=list)

    def rent_buy(self) -> list[StreamingProvider]:
        """Merged rent and buy list with duplicates removed by provider_id."""
        seen = set()
        combined = []
        for p in self.buy + self.rent:
            if p.provider_id not in seen:
                seen.add(p.provider_id)
                combined.append(p)
        return sorted(combined, key=lambda x: x.display_priority)

    def offering_types(self) -> list[str]:
        """Group labels in a stable order: flatrate, rent_buy, ads, free."""
        types = []
        if self.flatrate:
            types.append("flatrate")
        if self.buy or self.rent:
            types.append("rent_buy")
        if self.ads:
            types.append("ads")
        if self.free:
            types.append("free")
        return types


@dataclass
class Collection:
    collection_id: int
    name: str
    overview: str | None = None
    poster_path: str | None = None
    backdrop_path: str | None = None
    parts: list[Movie] = field(default_factory=list)


@dataclass
class Stats:
    movies_watched: int = 0
    shows_watched: int = 0
    episodes_watched: int = 0
    watchlist_items: int = 0
    ratings: int = 0
    collection_items: int = 0
