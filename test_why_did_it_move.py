"""Tests for why_did_it_move. Run: python test_why_did_it_move.py"""
from __future__ import annotations

import math
import unittest

import llm
import why_did_it_move as w


class TestLMDI(unittest.TestCase):
    def test_hand_computed_two_drivers(self):
        # V = a * b.  a: 2 -> 3, b: 5 -> 4, so V: 10 -> 12.
        # L(12, 10) = 2 / ln(1.2);  contribution_a = L * ln(1.5);  contribution_b = L * ln(0.8)
        before = {"a": 2.0, "b": 5.0, "v": 10.0}
        after = {"a": 3.0, "b": 4.0, "v": 12.0}
        c = w.lmdi(before, after, ["a", "b"], "v")
        expected_a = 2 / math.log(1.2) * math.log(1.5)          # = 4.4478...
        self.assertAlmostEqual(c["a"], expected_a, places=6)
        self.assertAlmostEqual(c["a"], 4.4478, places=3)
        self.assertAlmostEqual(c["a"] + c["b"], 2.0, places=9)   # sums exactly to V1 - V0

    def test_only_one_driver_moves(self):
        before = {"a": 2.0, "b": 5.0, "v": 10.0}
        after = {"a": 4.0, "b": 5.0, "v": 20.0}
        c = w.lmdi(before, after, ["a", "b"], "v")
        self.assertAlmostEqual(c["a"], 10.0, places=9)
        self.assertAlmostEqual(c["b"], 0.0, places=9)

    def test_no_change_no_division_by_zero(self):
        same = {"a": 2.0, "b": 5.0, "v": 10.0}
        c = w.lmdi(same, same, ["a", "b"], "v")
        self.assertEqual(c, {"a": 0.0, "b": 0.0})


class TestEngineOnSyntheticData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = w.make_data()
        cls.week = int(cls.d.week.max())
        cls.ev = w.build_evidence(cls.d, cls.week)

    def test_data_shape(self):
        self.assertEqual(self.d.date.nunique(), 70)
        self.assertEqual(self.d.segment.nunique(), 6)
        self.assertEqual(self.week, 10)
        self.assertTrue((self.d.orders <= self.d.active_users).all())

    def test_contributions_sum_to_the_change(self):
        w0, w1 = w.weekly(self.d, self.week - 1), w.weekly(self.d, self.week)
        t0, t1 = w.totals(w0), w.totals(w1)
        delta = t1["revenue"] - t0["revenue"]
        drivers = w.lmdi(t0, t1, list(w.DRIVERS), "revenue")
        self.assertAlmostEqual(sum(drivers.values()), delta, places=4)
        volume, seg = w.kitagawa(w0, w1)
        self.assertAlmostEqual(volume + seg.rate.sum() + seg.mix.sum(), delta, places=4)
        self.assertTrue(((seg.rate_conversion + seg.rate_aov - seg.rate).abs() < 1e-4).all())
        self.assertEqual(self.ev["checks"], {"driver_sum_gap_lakh": 0.0, "segment_sum_gap_lakh": 0.0})

    def test_engine_recovers_the_injected_story(self):
        ev = self.ev
        self.assertLess(ev["change_pct"], -5)
        self.assertGreater(ev["change_pct"], -11)
        top = ev["top_contributors"][0]
        self.assertEqual(top["name"], "Android · Tier-2 conversion")
        self.assertLess(top["contribution_lakh"], -5)
        by_name = {s["segment"]: s for s in ev["segments"]}
        self.assertLess(by_name["iOS · Tier-1"]["mix_contribution_lakh"], -2)     # mix shift away from high-AOV
        self.assertTrue(all(s["rate_from_aov_lakh"] >= 0 for s in ev["segments"]))  # AOV up everywhere
        conv = next(x for x in ev["drivers"] if x["driver"] == "conversion_rate")
        self.assertLess(conv["contribution_pct_pts"], -8)

    def test_template_renders_three_lines(self):
        text = w.template_explanation(self.ev)
        lines = text.split("\n")
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("Revenue fell from INR"))
        self.assertTrue(lines[1].startswith("Biggest causes: Android · Tier-2 conversion"))
        self.assertTrue(lines[2].startswith("Not a factor:"))
        self.assertEqual(llm.unpinned(text, self.ev), set())   # the template only uses evidence numbers

    def test_unpinned_catches_an_invented_number(self):
        ok = w.template_explanation(self.ev)
        self.assertEqual(llm.unpinned(ok, self.ev), set())
        bad = ok.replace("Revenue fell", "Revenue fell by roughly 8.37 lakh - it fell")
        self.assertIn("8.37", llm.unpinned(bad, self.ev))


if __name__ == "__main__":
    unittest.main(verbosity=2)
