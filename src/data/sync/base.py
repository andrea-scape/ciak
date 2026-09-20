"""Shared types and abstract base class for sync backends."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

# Midpoints of Ciak's 1-5 star scale, used to denormalize a rating back to a
# source's wider range.  Both TMDB and Simkl push a star from this same
# center: a rating of N stars is the middle of a two-point band.
CIAK_TO_SOURCE = {1: 1, 2: 3, 3: 5, 4: 7, 5: 9}


def denormalize_star_rating(ciak_rating: int) -> int:
    return CIAK_TO_SOURCE.get(ciak_rating, 5)


class SyncCategory(Enum):
    WATCHLIST = "watchlist"
    WATCHED = "watched"
    RATINGS = "ratings"
    COLLECTION = "collection"


@dataclass
class SyncCapabilities:
    watchlist: bool = False
    watched: bool = False
    ratings: bool = False
    collection: bool = False


@dataclass
class SyncItem:
    tmdb_id: int
    media_type: str
    category: SyncCategory
    rating: int | None = None
    watched_at: int | None = None
    added_at: int | None = None
    remote_id: str | None = None
    title: str | None = None
    year: int | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class SyncResult:
    pushed: int = 0
    pulled: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_items(self) -> int:
        """Total items synced (pushed + pulled)."""
        return self.pushed + self.pulled

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


class SyncBackend(ABC):
    name: str = ""
    display_name: str = ""
    icon_name: str = ""
    capabilities: SyncCapabilities = field(default_factory=SyncCapabilities)

    @abstractmethod
    def authenticate(self, window) -> bool:
        """Run the auth flow. Returns True on success."""

    @abstractmethod
    def is_authenticated(self) -> bool:
        """Check if we have valid credentials."""

    @abstractmethod
    def disconnect(self):
        """Remove stored credentials."""

    @abstractmethod
    def pull(self) -> list[SyncItem]:
        """Fetch all items from the remote service."""

    @abstractmethod
    def push(self, items: list[SyncItem]) -> SyncResult:
        """Push local items to the remote service."""
