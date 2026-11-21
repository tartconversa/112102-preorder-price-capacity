"""端到端分析测试：锁定分母、四类状态、需求区间、截止日重放、共同支持。"""

import unittest
from pathlib import Path

from app.analysis import run_analysis
from app.demand import (CONVERTED, INTENT_ONLY, NO_OPPORTUNITY, REFUNDED,
                        SELF_ABANDON)
from app.loading import load_round

ROOT = Path(__file__).resolve().parents[1]


class BaselineAnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.round_obj = load_round(ROOT / "sources", name="baseline")

    def test_demand_bounds_at_horizon(self):
        analysis = run_analysis(self.round_obj, as_of_day=14)
        cells = {(c.market, c.price): c for c in analysis.cells}
        self.assertEqual(cells[("east", 90)].n_components, 18)
        self.assertAlmostEqual(cells[("east", 90)].lower, 5 / 18)
        self.assertAlmostEqual(cells[("east", 90)].upper, 17 / 18)
        self.assertAlmostEqual(cells[("east", 120)].lower, 8 / 24)
        self.assertAlmostEqual(cells[("east", 120)].upper, 20 / 24)
        self.assertAlmostEqual(cells[("east", 150)].lower, 5 / 18)
        self.assertAlmostEqual(cells[("east", 150)].upper, 11 / 18)
        self.assertAlmostEqual(cells[("north", 90)].lower, 7 / 20)
        self.assertAlmostEqual(cells[("north", 90)].upper, 17 / 20)

    def test_denominator_locked_across_days_and_rights(self):
        # 无论截止日怎么变，分母只由封存 offers 决定（80 份报价/家庭）。
        for day in (11, 12, 13, 14):
            analysis = run_analysis(self.round_obj, as_of_day=day)
            self.assertEqual(sum(c.n_offers for c in analysis.cells), 80)
            self.assertEqual(sum(c.n_components for c in analysis.cells), 80)

    def test_four_way_classification_counts(self):
        analysis = run_analysis(self.round_obj, as_of_day=14)
        totals = {status: 0 for status in
                  (CONVERTED, SELF_ABANDON, NO_OPPORTUNITY, INTENT_ONLY,
                   REFUNDED)}
        for cell in analysis.cells:
            for status, count in cell.counts.items():
                totals[status] += count
        self.assertEqual(totals[CONVERTED], 25)
        self.assertEqual(totals[SELF_ABANDON], 15)
        self.assertEqual(totals[NO_OPPORTUNITY], 16)
        self.assertEqual(totals[INTENT_ONLY], 24)
        self.assertEqual(totals[REFUNDED], 0)
        self.assertEqual(sum(totals.values()), 80)

    def test_refund_replay_changes_conversion_but_not_denominator(self):
        day11 = run_analysis(self.round_obj, as_of_day=11)
        day13 = run_analysis(self.round_obj, as_of_day=13)
        day14 = run_analysis(self.round_obj, as_of_day=14)
        converted = lambda a: sum(  # noqa: E731
            c.counts[CONVERTED] for c in a.cells)
        refunded = lambda a: sum(  # noqa: E731
            c.counts[REFUNDED] for c in a.cells)
        self.assertEqual(converted(day11), 25)
        # day 12 退款生效：O1-2 从转化移到退款，分母不变。
        self.assertEqual(converted(day13), 24)
        self.assertEqual(refunded(day13), 1)
        # day 14 银行迟到撤回退款：O1-2 恢复为有效付款。
        self.assertEqual(converted(day14), 25)
        self.assertEqual(refunded(day14), 0)
        self.assertEqual(
            sum(c.n_components for c in day13.cells),
            sum(c.n_components for c in day14.cells))

    def test_late_identity_links_never_merge(self):
        analysis = run_analysis(self.round_obj, as_of_day=14)
        # J1 连接 A1-2(150) 与 A1-3(90)，J2 连接 A2-1(120) 与 A2-2(150)；
        # 两条链接都晚于所在 block 的分配日，任何单元都不得被合并。
        self.assertEqual(len(analysis.identity_audit), 2)
        self.assertTrue(all(row["would_merge_cells"]
                            for row in analysis.identity_audit))
        by_offer = {o.offer_id: o for o in analysis.outcomes}
        self.assertNotEqual(by_offer["Q1-2"].component,
                            by_offer["Q1-3"].component)
        self.assertNotEqual(by_offer["Q2-1"].component,
                            by_offer["Q2-2"].component)

    def test_no_common_support_blocks_borrowing(self):
        analysis = run_analysis(self.round_obj, as_of_day=14)
        verdicts = {(v.market, v.price): v for v in analysis.support}
        self.assertFalse(verdicts[("north", 120)].supported)
        self.assertFalse(verdicts[("north", 150)].supported)
        self.assertTrue(verdicts[("north", 90)].supported)
        comparisons = {row["price"]: row for row in analysis.comparisons}
        self.assertFalse(comparisons[120]["comparable"])
        self.assertFalse(comparisons[150]["comparable"])
        self.assertTrue(comparisons[90]["comparable"])

    def test_threshold_raises_bar(self):
        strict = run_analysis(self.round_obj, as_of_day=14,
                              support_threshold=19)
        verdicts = {(v.market, v.price): v for v in strict.support}
        self.assertFalse(verdicts[("east", 90)].supported)
        self.assertTrue(verdicts[("east", 120)].supported)

    def test_price_effects_are_interval_identified(self):
        analysis = run_analysis(self.round_obj, as_of_day=14)
        effect = next(e for e in analysis.effects
                      if e.market == "east" and e.price_low == 120
                      and e.price_high == 150)
        # [L150-U120, U150-L120] = [5/18-20/24, 11/18-8/24]
        self.assertAlmostEqual(effect.delta_low, 5 / 18 - 20 / 24)
        self.assertAlmostEqual(effect.delta_high, 11 / 18 - 8 / 24)
        self.assertLess(effect.delta_low, 0)
        self.assertGreater(effect.delta_high, 0)

    def test_sold_out_kept_in_denominator_as_no_opportunity(self):
        analysis = run_analysis(self.round_obj, as_of_day=14)
        sold_out = [o for o in analysis.outcomes
                    if o.offer_state == "sold_out"]
        self.assertEqual(len(sold_out), 16)
        self.assertTrue(all(o.status == NO_OPPORTUNITY for o in sold_out))


if __name__ == "__main__":
    unittest.main()
