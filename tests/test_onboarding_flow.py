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
        self.assertEqual(STEPS, ("welcome", "appearance", "sync", "done"))

    def test_forward_and_back(self):
        flow = OnboardingFlow()
        flow.go_forward()
        self.assertEqual(flow.step, "appearance")
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

    def test_full_journey(self):
        flow = OnboardingFlow()
        flow.go_forward()
        self.assertEqual(flow.step, "appearance")
        flow.go_forward()
        self.assertEqual(flow.step, "sync")
        flow.go_forward()
        self.assertEqual(flow.step, "done")
