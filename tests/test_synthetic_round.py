"""合成新一轮资料的端到端测试：跨票种同意、过期邀约、异常订单与锁定分母。"""

import tempfile
import unittest
from pathlib import Path

from app.analysis import run_analysis
from app.loading import import_round

HEADERS = {
    "offers.csv": "offer,household,account,market,block,assigned_day,price,deposit,offer_state",
    "commerce_events.csv": "event,offer,order,day,kind,amount,replaces",
    "identity_links.csv": "link,left_account,right_account,learned_day",
    "sales_terms.csv": "term,product,preserves_price,keeps_party,replacement_requires_consent",
    "sessions.csv": "session,product,day,capacity,status,learned_day",
    "booking_rights.csv": "right,order,term,session,guests,state,recorded_day,alternative,expires_day",
}


def _write_round(root: Path, content: dict[str, str]) -> Path:
    src = root / "drop"
    src.mkdir(exist_ok=True)
    for name, header in HEADERS.items():
        body = content.get(name, "")
        (src / name).write_text(header + "\n" + body, encoding="utf-8")
    return src


# 一个市场、一个价格（100）、4 个家庭：
# 付款 / 放弃 / sold_out / 仅意向各一。
OFFERS = """\
Q1,F1,A1,east,B1,1,100,20,opened
Q2,F2,A2,east,B1,1,100,20,opened
Q3,F3,A3,east,B1,1,100,20,sold_out
Q4,F4,A4,east,B1,1,100,0,opened
"""
EVENTS = """\
E1,Q1,O1,2,charged,20,
E2,Q2,O2,2,declined,0,
E3,Q3,O3,2,intent,0,
E4,Q4,O4,2,intent,0,
"""
# 正常退款链：收款后退款（day 8），再由银行迟到撤回（day 10）。
EVENTS_REFUNDED = EVENTS + "E5,Q1,O1,8,refund_sent,20,\n"
EVENTS_REVERSED = EVENTS_REFUNDED + "E6,Q1,O1,10,refund_reversed,20,E5\n"
# 断裂链：O1 的收款回执缺失，只剩退款行。
EVENTS_BROKEN = """\
E2,Q2,O2,2,declined,0,
E3,Q3,O3,2,intent,0,
E4,Q4,O4,2,intent,0,
E5,Q1,O1,8,refund_sent,20,
"""
TERMS = "T1,group,yes,yes,yes\n"
SESSIONS = "S1,group,9,10,available,1\nS2,flex,9,10,available,1\n"
LINKS = "J1,A1,A2,12\n"
RIGHTS = """\
R1,O1,T1,S1,2,alternative_pending,8,S2,12
"""


class SyntheticRoundTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._call = 0

    def tearDown(self):
        self._tmp.cleanup()

    def _analysis(self, as_of_day: int, events: str = EVENTS,
                  rights: str = RIGHTS, name: str | None = None):
        src = _write_round(self.tmp, {
            "offers.csv": OFFERS, "commerce_events.csv": events,
            "identity_links.csv": LINKS, "sales_terms.csv": TERMS,
            "sessions.csv": SESSIONS, "booking_rights.csv": rights,
        })
        name = name or f"r{self._call}"
        self._call += 1
        round_obj = import_round(src, self.tmp / "rounds", name)
        return run_analysis(round_obj, as_of_day=as_of_day)

    def test_denominator_stays_fixed_and_bounds_partial(self):
        analysis = self._analysis(7)
        cell = analysis.cells[0]
        self.assertEqual(cell.n_components, 4)
        # day 7：O1 仍在账 → L=1/4；硬拒绝只有 O2 → U=3/4
        self.assertAlmostEqual(cell.lower, 0.25)
        self.assertAlmostEqual(cell.upper, 0.75)

    def test_refund_moves_payment_to_refunded_not_deleted(self):
        analysis = self._analysis(9, events=EVENTS_REFUNDED)
        cell = analysis.cells[0]
        # 退款生效：转化 0，硬拒绝=放弃+退款=2 → L=0, U=2/4；分母仍为 4
        self.assertEqual(cell.counts["converted"], 0)
        self.assertEqual(cell.counts["refunded"], 1)
        self.assertEqual(cell.n_components, 4)
        self.assertEqual(cell.lower, 0.0)
        self.assertEqual(cell.upper, 0.5)

    def test_bank_reversal_restores_conversion(self):
        # 银行迟到撤回退款后，家庭重新成为有效付款，结论按转化事件重出。
        analysis = self._analysis(10, events=EVENTS_REVERSED)
        cell = analysis.cells[0]
        self.assertEqual(cell.counts["converted"], 1)
        self.assertEqual(cell.counts["refunded"], 0)
        self.assertAlmostEqual(cell.lower, 0.25)

    def test_broken_receipt_chain_flagged_but_kept_in_denominator(self):
        analysis = self._analysis(9, events=EVENTS_BROKEN)
        codes = {a.code for a in analysis.anomalies}
        self.assertIn("refund_without_charge", codes)
        # 异常订单不能从分析分母中删掉。
        self.assertEqual(
            sum(c.n_components for c in analysis.cells), 4)
        recon = {item.code for item in analysis.reconciliation_items}
        self.assertIn("refund_without_charge", recon)

    def test_pending_cross_product_hold_and_expiry(self):
        pending = self._analysis(9)
        issue = pending.consent_issues[0]
        self.assertEqual(issue.code, "cross_product_consent_pending")
        self.assertEqual(issue.from_product, "group")
        self.assertEqual(issue.to_product, "flex")
        # 未过期的替代邀约继续扣 S2 容量：10-2=8
        s2 = next(line for line in pending.capacity if line.session == "S2")
        self.assertEqual(s2.pending_guests, 2)
        self.assertEqual(s2.releasable_seats, 8)
        # 过期（day 15 > expires 12）：席位释放，同意状态转 unresolved
        expired = self._analysis(15)
        self.assertEqual(
            expired.consent_issues[0].code,
            "cross_product_consent_unresolved")
        s2 = next(line for line in expired.capacity if line.session == "S2")
        self.assertEqual(s2.pending_guests, 0)
        self.assertEqual(s2.releasable_seats, 10)

    def test_unknown_price_market_has_no_support(self):
        # north 市场没有任何分配 → 单元不存在，借用被禁止
        analysis = self._analysis(7)
        north = [v for v in analysis.support if v.market == "north"]
        self.assertEqual(north, [])
        east = {v.price: v for v in analysis.support
                if v.market == "east"}
        self.assertFalse(east[100].supported)  # n=4 < 默认阈值 10


if __name__ == "__main__":
    unittest.main()
