from feedback.cleaning import clean_feedback
from feedback.selection import select_feedback
from tests.helpers import BaseTest


class SelectionTests(BaseTest):
    def test_inclusive_boundaries_and_ignores_gap(self):
        cleaned, _ = clean_feedback(self.raw, self.config)
        selected, info = select_feedback(self.raw, cleaned, "2026-10-19", "2026-10-22", self.config)
        self.assertEqual(set(selected["反馈ID"]), {"001","002","004","005"})
        self.assertEqual((info["current_count"], info["previous_count"], info["outside_count"]), (2,2,4))
        self.assertTrue(info["ready"])

    def test_missing_boundary_needs_explicit_confirmation(self):
        raw = self.raw.iloc[[4,5]]
        cleaned, _ = clean_feedback(raw,self.config)
        _, info = select_feedback(raw,cleaned,"2026-10-19","2026-10-22",self.config)
        self.assertFalse(info["ready"])
        _, confirmed = select_feedback(raw,cleaned,"2026-10-19","2026-10-22",self.config,True)
        self.assertTrue(confirmed["ready"])
        self.assertEqual(confirmed["previous_count"], 0)

    def test_overlapping_periods_select_each_id_once(self):
        cleaned, _ = clean_feedback(self.raw,self.config)
        selected, info = select_feedback(self.raw,cleaned,"2026-10-12","2026-10-22",self.config)
        self.assertFalse(selected["反馈ID"].duplicated().any())
        self.assertGreater(info["current_count"]+info["previous_count"],info["selected_count"])
