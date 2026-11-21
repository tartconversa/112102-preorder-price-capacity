"""银行回执链测试：退款发出/撤回按截止日重放。"""

import unittest

from app.finance import parse_receipts, receipt_chain_anomalies


def rows(*data):
    return [
        dict(event=e, offer=offer, order=order, day=day, kind=kind,
             amount=amount, replaces=replaces)
        for e, offer, order, day, kind, amount, replaces in data
    ]


class ReceiptChainTest(unittest.TestCase):
    def _chain(self):
        return parse_receipts(rows(
            ("E1", "Q1", "O1", 2, "charged", 20, ""),
            ("ER1", "Q1", "O1", 12, "refund_sent", 20, ""),
            ("ER2", "Q1", "O1", 14, "refund_reversed", 20, "ER1"),
        ))["O1"]

    def test_refund_active_before_reversal(self):
        ledger = self._chain()
        self.assertFalse(ledger.is_paid(12))
        self.assertEqual(ledger.net_cash(12), 0)
        # 撤回在 day 14 才到达；day 13 退款仍然生效。
        self.assertFalse(ledger.is_paid(13))
        self.assertEqual(ledger.net_cash(13), 0)

    def test_reversal_restores_cash(self):
        ledger = self._chain()
        self.assertEqual(ledger.net_cash(14), 20)
        self.assertTrue(ledger.is_paid(14))

    def test_refund_without_charge_is_anomaly(self):
        ledgers = parse_receipts(rows(
            ("ER1", "Q9", "O9", 5, "refund_sent", 20, ""),
        ))
        anomalies = receipt_chain_anomalies(ledgers)
        self.assertEqual({a.code for a in anomalies},
                         {"refund_without_charge"})

    def test_multiple_live_refunds_flagged(self):
        ledgers = parse_receipts(rows(
            ("E1", "Q1", "O1", 2, "charged", 20, ""),
            ("ER1", "Q1", "O1", 5, "refund_sent", 20, ""),
            ("ER2", "Q1", "O1", 6, "refund_sent", 20, ""),
        ))
        self.assertEqual(
            {a.code for a in receipt_chain_anomalies(ledgers)},
            {"multiple_live_refunds"})


if __name__ == "__main__":
    unittest.main()
