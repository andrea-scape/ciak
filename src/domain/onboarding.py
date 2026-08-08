"""First-run onboarding state machine. Pure logic, no GTK."""

STEPS = ("welcome", "tmdb", "appearance", "done")


def needs_onboarding(completed: bool) -> bool:
    return not completed


class OnboardingFlow:
    def __init__(self):
        self._index = 0
        self.tmdb_key = None
        self.tmdb_status = None
        self.tmdb_skipped = False

    @property
    def step(self) -> str:
        return STEPS[self._index]

    def is_last(self) -> bool:
        return self._index == len(STEPS) - 1

    def can_go_back(self) -> bool:
        return self._index > 0

    def can_go_forward(self) -> bool:
        if self.is_last():
            return False
        if self.step == "tmdb" and self.tmdb_status == "invalid":
            return False
        return True

    def go_back(self) -> None:
        if self.can_go_back():
            self._index -= 1

    def go_forward(self) -> None:
        if self.can_go_forward():
            self._index += 1

    def set_key(self, key: str) -> None:
        self.tmdb_key = key

    def set_status(self, status: str) -> None:
        self.tmdb_status = status

    def skip_tmdb(self) -> None:
        self.tmdb_skipped = True
        self.tmdb_key = None
        self.tmdb_status = None
