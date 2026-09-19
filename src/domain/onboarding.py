"""First-run onboarding state machine. Pure logic, no GTK."""

STEPS = ("welcome", "appearance", "sync", "done")


def needs_onboarding(completed: bool) -> bool:
    return not completed


class OnboardingFlow:
    def __init__(self):
        self._index = 0

    @property
    def step(self) -> str:
        return STEPS[self._index]

    def is_last(self) -> bool:
        return self._index == len(STEPS) - 1

    def can_go_back(self) -> bool:
        return self._index > 0

    def can_go_forward(self) -> bool:
        return not self.is_last()

    def go_back(self) -> None:
        if self.can_go_back():
            self._index -= 1

    def go_forward(self) -> None:
        if self.can_go_forward():
            self._index += 1
