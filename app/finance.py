"""银行回执链与现金敞口。

commerce_events 每行是一份单独回执：
- charged: 收款（订金）
- declined: 放弃付款（未收款）
- intent: 仅登记意愿（无金额）
- refund_sent: 退款已发出
- refund_reversed: 银行迟到撤回原退款，必须用 ``replaces`` 指向被撤回的回执

同一订单的退款发出与撤回分别成行；现金口径按截止日（as-of day）重放：
被撤回的退款在截止日之前已撤回，则现金并未离开商户。
"""

from __future__ import annotations

from dataclasses import dataclass, field

RECEIPT_KINDS = ("charged", "declined", "intent", "refund_sent", "refund_reversed")
CASH_IN = "charged"
CASH_OUT = "refund_sent"
CASH_RESTORE = "refund_reversed"


@dataclass(frozen=True)
class Receipt:
    event_id: str
    offer_id: str
    order_id: str
    day: int
    kind: str
    amount: int
    replaces: str


@dataclass
class OrderLedger:
    order_id: str
    offer_id: str
    receipts: list[Receipt] = field(default_factory=list)

    def active_receipts(self, as_of_day: int) -> list[Receipt]:
        """截止日内仍生效的回执（被撤回的退款剔除）。"""

        replaced: set[str] = set()
        for receipt in self.receipts:
            if receipt.day <= as_of_day and receipt.kind == CASH_RESTORE:
                replaced.add(receipt.replaces)
        return [
            r for r in self.receipts
            if r.day <= as_of_day and r.event_id not in replaced
        ]

    def net_cash(self, as_of_day: int) -> int:
        """截止日商户对该订单的净现金（收款 - 生效中的退款）。"""

        total = 0
        for receipt in self.active_receipts(as_of_day):
            if receipt.kind == CASH_IN:
                total += receipt.amount
            elif receipt.kind == CASH_OUT:
                total -= receipt.amount
        return total

    def is_paid(self, as_of_day: int) -> bool:
        """截止日是否为有效付款：净现金为正且存在生效收款。"""

        active = self.active_receipts(as_of_day)
        has_charge = any(r.kind == CASH_IN for r in active)
        return has_charge and self.net_cash(as_of_day) > 0

    def last_customer_event(self, as_of_day: int) -> Receipt | None:
        """客户侧最后一次动作（charged/declined/intent），用于状态划分。"""

        kinds = {"charged", "declined", "intent"}
        seen = [r for r in self.receipts
                if r.day <= as_of_day and r.kind in kinds]
        return seen[-1] if seen else None


@dataclass
class Anomaly:
    order_id: str
    offer_id: str
    code: str
    detail: str


def parse_receipts(rows: list[dict[str, object]]) -> dict[str, OrderLedger]:
    ledgers: dict[str, OrderLedger] = {}
    for row in rows:
        receipt = Receipt(
            event_id=str(row["event"]), offer_id=str(row["offer"]),
            order_id=str(row["order"]), day=int(row["day"]),
            kind=str(row["kind"]), amount=int(row["amount"]),
            replaces=str(row["replaces"]),
        )
        if receipt.kind not in RECEIPT_KINDS:
            raise ValueError(f"未知回执类型 {receipt.kind}（{receipt.event_id}）")
        ledger = ledgers.setdefault(
            receipt.order_id, OrderLedger(receipt.order_id, receipt.offer_id))
        ledger.receipts.append(receipt)
    for ledger in ledgers.values():
        ledger.receipts.sort(key=lambda r: (r.day, r.event_id))
    return dict(sorted(ledgers.items()))


def receipt_chain_anomalies(ledgers: dict[str, OrderLedger]) -> list[Anomaly]:
    """回执链异常：缺少收款或退款回执的订单。

    异常订单 *不能从分析分母中删除*，但会影响可放容量（见 capacity 模块）。
    """

    anomalies: list[Anomaly] = []
    for order_id, ledger in ledgers.items():
        kinds = {r.kind for r in ledger.receipts}
        if "refund_sent" in kinds and not any(
                r.kind == CASH_RESTORE for r in ledger.receipts):
            # 退款已发出且始终未被撤回，却没有对应收款行（链不完整）。
            if "charged" not in kinds:
                anomalies.append(Anomaly(
                    order_id, ledger.offer_id, "refund_without_charge",
                    "存在退款回执但缺少收款回执，回执链不完整"))
        if "refund_reversed" in kinds and "charged" not in kinds:
            anomalies.append(Anomaly(
                order_id, ledger.offer_id, "reversal_without_charge",
                "存在退款撤回但缺少收款回执，回执链不完整"))
        # 同一订单出现多次未被撤回的退款。
        replaced = {r.replaces for r in ledger.receipts
                    if r.kind == CASH_RESTORE}
        live_refunds = [r for r in ledger.receipts
                        if r.kind == CASH_OUT and r.event_id not in replaced]
        if len(live_refunds) > 1:
            anomalies.append(Anomaly(
                order_id, ledger.offer_id, "multiple_live_refunds",
                f"存在 {len(live_refunds)} 笔生效中的退款，回执链异常"))
    return anomalies
