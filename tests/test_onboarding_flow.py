import unittest

from src.domain.onboarding import OnboardingFlow, STEPS, needs_onboarding


class NeedsOnboardingTest(unittest.TestCase):
    def test_false_when_completed(self):
        self.assertFalse(needs_onboarding(True))

    def test_true_when_not_completed(self):
        self.assertTrue(needs_onboarding(False))


class OnboardingFlowTest(unittest.TestCase):
    def test_initial_step_is_welcome(self):
        self.assertEqual(OnboardingFlow().step, "welcome")

    def test_steps_in_order(self):
        self.assertEqual(STEPS, ("welcome", "tmdb", "appearance", "done"))

    def test_forward_and_back(self):
        flow = OnboardingFlow()
        flow.go_forward()
        self.assertEqual(flow.step, "tmdb")
        flow.go_back()
        self.assertEqual(flow.step, "welcome")

    def test_cannot_go_back_at_welcome(self):
        flow = OnboardingFlow()
        self.assertFalse(flow.can_go_back())
        flow.go_back()
        self.assertEqual(flow.step, "welcome")

    def test_cannot_go_forward_at_done(self):
        flow = OnboardingFlow()
        flow._index = len(STEPS) - 1
        self.assertTrue(flow.is_last())
        self.assertFalse(flow.can_go_forward())
        flow.go_forward()
        self.assertEqual(flow.step, "done")

    def test_tmdb_step_allows_forward_without_key(self):
        flow = OnboardingFlow()
        flow.go_forward()
        self.assertEqual(flow.step, "tmdb")
        self.assertTrue(flow.can_go_forward())

    def test_tmdb_step_blocks_forward_when_invalid(self):
        flow = OnboardingFlow()
        flow.go_forward()
        flow.set_status("invalid")
        self.assertFalse(flow.can_go_forward())

    def test_tmdb_step_allows_forward_after_skip(self):
        flow = OnboardingFlow()
        flow.go_forward()
        flow.set_status("invalid")
        flow.skip_tmdb()
        self.assertTrue(flow.can_go_forward())

    def test_unreachable_does_not_block(self):
        flow = OnboardingFlow()
        flow.go_forward()
        flow.set_status("unreachable")
        self.assertTrue(flow.can_go_forward())

