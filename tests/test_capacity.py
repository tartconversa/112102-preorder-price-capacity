"""容量、替代邀约与对账测试。"""

import unittest
from pathlib import Path

from app.analysis import run_analysis
from app.loading import load_round
from app.rights import parse_terms

ROOT = Path(__file__).resolve().parents[1]


class CapacityAndRightsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.round_obj = load_round(ROOT / "sources", name="baseline")

    def _lines(self, day):
        analysis = run_analysis(self.round_obj, as_of_day=day)
        return {line.session: line for line in analysis.capacity}

    def test_capacity_before_cancellation(self):
        lines = self._lines(11)
        # S1: 7 - R1(3) = 4；S2: 7 - R2(2) = 5；S3 全空 9
        self.assertEqual(lines["S1"].releasable_seats, 4)
        self.assertEqual(lines["S2"].releasable_seats, 5)
        self.assertEqual(lines["S3"].releasable_seats, 9)
        self.assertEqual(lines["S1"].status, "available")

    def test_pending_alternative_holds_seat_until_expiry(self):
        day12 = self._lines(12)
        # R1 待决替代到 S2（到期日 14）：S1 已取消，S2 扣 R2(2)+待决(3)。
        self.assertEqual(day12["S1"].status, "cancelled")
        self.assertEqual(day12["S1"].releasable_seats, 0)
        self.assertEqual(day12["S2"].pending_guests, 3)
        self.assertEqual(day12["S2"].releasable_seats, 2)

    def test_accepted_alternative_moves_hold(self):
        day14 = self._lines(14)
        # R1 已接受改到 S2：确认 R2(2) + 替代接受 R1(3) = 5，可放 2。
        self.assertEqual(day14["S2"].confirmed_guests, 2)
        self.assertEqual(day14["S2"].alternative_guests, 3)
        self.assertEqual(day14["S2"].pending_guests, 0)
        self.assertEqual(day14["S2"].releasable_seats, 2)
        self.assertEqual(day14["S3"].releasable_seats, 9)

    def test_after_sales_worklist_tracks_lifecycle(self):
        day12 = run_analysis(self.round_obj, as_of_day=12)
        kinds12 = {t.kind for t in day12.worklist}
        self.assertIn("alternative_pending", kinds12)
        day14 = run_analysis(self.round_obj, as_of_day=14)
        kinds14 = {t.kind for t in day14.worklist}
        self.assertEqual(kinds14, {"reschedule_accepted"})
        task = day14.worklist[0]
        self.assertEqual(task.order_id, "O1-2")
        self.assertEqual(task.session, "S2")

    def test_reconciliation_exposes_both_mismatch_directions(self):
        analysis = run_analysis(self.round_obj, as_of_day=14)
        codes = {item.code for item in analysis.reconciliation_items}
        # 24 笔在账收款没有权利（现金敞口），1 个权利没有在账收款（R2/O1-8）。
        self.assertIn("cash_without_right", codes)
        self.assertIn("right_without_cash", codes)
        right_gap = next(item for item in analysis.reconciliation_items
                         if item.code == "right_without_cash")
        self.assertEqual(right_gap.order_id, "O1-8")
        cash_gaps = [item for item in analysis.reconciliation_items
                     if item.code == "cash_without_right"]
        self.assertEqual(len(cash_gaps), 24)
        self.assertEqual(sum(item.cash_impact for item in cash_gaps), 480)

    def test_anomalous_order_remains_in_denominator(self):
        # O1-8 回执链异常（declined 却持有权利），其报价 Q1-8 仍在分母中。
        analysis = run_analysis(self.round_obj, as_of_day=14)
        offers = {o.offer_id: o for o in analysis.outcomes}
        self.assertIn("Q1-8", offers)
        self.assertEqual(sum(c.n_components for c in analysis.cells), 80)

    def test_same_product_alternative_needs_no_cross_consent(self):
        # 基线数据中 R1 的替代场次 S2 与原场次 S1 同为 group 票种，
        # 不触发跨票种同意；但仍受条款 replacement_requires_consent 约束
        # （alternative_accepted 本身即同意留痕）。
        analysis = run_analysis(self.round_obj, as_of_day=14)
        cross = [i for i in analysis.consent_issues
                 if i.from_product != i.to_product]
        self.assertEqual(cross, [])

    def test_cross_product_consent_states(self):
        from app.rights import (ALTERNATIVE_ACCEPTED, ALTERNATIVE_PENDING,
                                Right, check_alternative_consent)
        terms = parse_terms(self.round_obj.tables["sales_terms"])
        sessions = self.round_obj.tables["sessions"]
        rights = [
            Right("RX", "OX", "T1", "S1", 2, ALTERNATIVE_PENDING, 12,
                  "S3", 14),
            Right("RY", "OY", "T1", "S1", 1, ALTERNATIVE_ACCEPTED, 13,
                  "S3", 14),
        ]
        pending = check_alternative_consent(rights, terms, sessions, 12)
        self.assertEqual(pending[0].code, "cross_product_consent_pending")
        self.assertEqual(pending[0].from_product, "group")
        self.assertEqual(pending[0].to_product, "flex")
        accepted = check_alternative_consent(rights, terms, sessions, 13)
        codes = sorted(i.code for i in accepted)
        self.assertEqual(codes,
                         ["cross_product_consent_pending",
                          "cross_product_consent_recorded"])
        # 邀约过期且未同意 → unresolved，且不再计入容量占用（holds 规则）。
        expired = check_alternative_consent(rights, terms, sessions, 15)
        self.assertIn("cross_product_consent_unresolved",
                      [i.code for i in expired])

    def test_release_recommendation_uses_bounds(self):
        analysis = run_analysis(self.round_obj, as_of_day=14, market="east")
        rec = next(r for r in analysis.recommendations
                   if r.session == "S2" and r.price == 120)
        # 2 席，L=1/3, U=5/6 → 保守 ceil(2/(1/3))=6，乐观 ceil(2/(5/6))=3
        self.assertEqual(rec.offers_conservative, 6)
        self.assertEqual(rec.offers_optimistic, 3)
        cancelled = next(r for r in analysis.recommendations
                         if r.session == "S1")
        self.assertEqual(cancelled.releasable_seats, 0)
        self.assertEqual(cancelled.offers_conservative, 0)


if __name__ == "__main__":
    unittest.main()
