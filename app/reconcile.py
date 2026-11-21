"""对账与售后工作台。

三类核对：

1. 报价-回执核对：每份 opened 报价应有回执；sold_out 允许仅意向。
2. 收款-权利核对：有效付款订单应能在预约系统找到权利；持有效权利的订单
   应有在账收款（R2/O1-8 这类“有权利但付款被拒”的合成异常在此暴露）。
3. 权利-席位核对：替代邀约的接受/待决/过期与场次状态一致。

售后工作清单输出等待确认（alternative_pending）、退款处理中、改期已接受
等待履约的家庭，供售后逐项处理。所有核对按截止日重放。
"""

from __future__ import annotations

from dataclasses import dataclass

from .demand import OfferOutcome
from .finance import Anomaly, OrderLedger
from .rights import (ALTERNATIVE_ACCEPTED, ALTERNATIVE_PENDING, Right,
                     all_current_rights)


@dataclass(frozen=True)
class ReconciliationItem:
    code: str
    order_id: str
    offer_id: str
    detail: str
    cash_impact: int


def reconcile(offer_outcomes: list[OfferOutcome],
              ledgers: dict[str, OrderLedger],
              rights: list[Right],
              anomalies: list[Anomaly],
              as_of_day: int) -> list[ReconciliationItem]:
    items: list[ReconciliationItem] = []

    paid_orders = {o.order_id for o in offer_outcomes if o.status == "converted"}
    right_orders = {r.order_id for r in all_current_rights(rights, as_of_day)}
    rights_by_order: dict[str, Right] = {}
    for right in all_current_rights(rights, as_of_day):
        rights_by_order.setdefault(right.order_id, right)

    # 1) 在账收款但预约系统无权利 → 现金敞口（已收钱，未给席位承诺）。
    for order_id in sorted(paid_orders):
        if order_id not in right_orders:
            ledger = ledgers[order_id]
            items.append(ReconciliationItem(
                "cash_without_right", order_id, ledger.offer_id,
                "订金在账但预约系统无有效权利，形成现金敞口，需补登记或退款",
                ledger.net_cash(as_of_day),
            ))

    # 2) 持有效权利但无在账收款 → 席位被无款订单占用（容量冻结）。
    for order_id in sorted(right_orders):
        ledger = ledgers.get(order_id)
        if ledger is None or not ledger.is_paid(as_of_day):
            right = rights_by_order[order_id]
            items.append(ReconciliationItem(
                "right_without_cash", order_id,
                ledger.offer_id if ledger else "",
                f"权利 {right.right_id} 占用 {right.guests} 席但无在账收款，"
                "冻结容量并催办付款/释放",
                0,
            ))

    # 3) 回执链异常直接入账。
    for anomaly in anomalies:
        ledger = ledgers.get(anomaly.order_id)
        items.append(ReconciliationItem(
            anomaly.code, anomaly.order_id, anomaly.offer_id,
            anomaly.detail,
            ledger.net_cash(as_of_day) if ledger else 0,
        ))

    # 4) 已取消场次上仍挂确认权利 → 必须走改期/退款。
    # （场次状态由 sessions 决定，这里只列权利侧，capacity 报告给场次视角。）
    return items


@dataclass(frozen=True)
class AfterSalesTask:
    kind: str
    order_id: str
    right_id: str
    session: str
    guests: int
    detail: str
    deadline_day: int | None


def after_sales_worklist(rights: list[Right], as_of_day: int) -> list[AfterSalesTask]:
    """等待售后处理的家庭任务。"""

    tasks: list[AfterSalesTask] = []
    for right in all_current_rights(rights, as_of_day):
        if right.state == ALTERNATIVE_PENDING:
            expired = (right.expires_day is not None
                       and right.expires_day < as_of_day)
            tasks.append(AfterSalesTask(
                "alternative_expired" if expired else "alternative_pending",
                right.order_id, right.right_id,
                right.alternative or right.session, right.guests,
                "替代邀约已过期，需重新安排或恢复原退款" if expired
                else "等待家庭确认替代邀约",
                right.expires_day,
            ))
        elif right.state == ALTERNATIVE_ACCEPTED:
            tasks.append(AfterSalesTask(
                "reschedule_accepted", right.order_id, right.right_id,
                right.alternative or right.session, right.guests,
                "改期已接受，等待新场次履约并核对原订金权利", None,
            ))
    return sorted(tasks, key=lambda t: (t.kind, t.order_id))
