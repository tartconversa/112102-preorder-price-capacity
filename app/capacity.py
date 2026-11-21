"""可兑现席位与新放量方案。

场次容量按 sessions 的时点行（learned_day）取截止日最新值；取消场次不参与放量。
可放新名额 = 物理容量 − 仍有有效权利的预约 − 已发出且未过期的替代邀约
            − 异常订单占用（回执链异常的权利，容量冻结待财务处理）。

异常订单 *只冻结容量，不从分析分母删除*（需求口径见 demand 模块）。
放量推荐结合市场 × 价格的需求识别区间：
- 保守口径按转化率下界 L 反推所需报价数，保证即便最差转化也能填满可放席位；
- 乐观口径按上界 U 反推；L=0 时上界端不可知（标记 unknown）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .demand import CellStats
from .finance import Anomaly
from .rights import Right, holds_as_of

@dataclass(frozen=True)
class SessionSnapshot:
    session: str
    product: str
    day: int
    capacity: int
    status: str


def session_snapshots(rows: list[dict[str, object]],
                      as_of_day: int) -> dict[str, SessionSnapshot]:
    """截止日各场次的最新状态行。"""

    latest: dict[str, SessionSnapshot] = {}
    for row in rows:
        if int(row["learned_day"]) > as_of_day:
            continue
        latest[str(row["session"])] = SessionSnapshot(
            session=str(row["session"]), product=str(row["product"]),
            day=int(row["day"]), capacity=int(row["capacity"]),
            status=str(row["status"]),
        )
    return latest


@dataclass(frozen=True)
class CapacityLine:
    session: str
    product: str
    status: str
    capacity: int
    confirmed_guests: int
    alternative_guests: int
    pending_guests: int
    anomalous_guests: int
    releasable_seats: int
    anomaly_order_ids: tuple[str, ...]


def capacity_plan(sessions_rows: list[dict[str, object]],
                  rights: list[Right],
                  anomalies: list[Anomaly],
                  as_of_day: int) -> list[CapacityLine]:
    """截止日每个场次的可放容量。"""

    snapshots = session_snapshots(sessions_rows, as_of_day)
    holds = holds_as_of(rights, as_of_day)
    anomalous_orders = {a.order_id for a in anomalies}

    lines: list[CapacityLine] = []
    for session_id in sorted(snapshots):
        snap = snapshots[session_id]
        session_holds = [h for h in holds if h.session == session_id]
        confirmed = sum(h.guests for h in session_holds
                        if h.kind == "confirmed")
        alt = sum(h.guests for h in session_holds
                  if h.kind == "alternative_accepted")
        pending = sum(h.guests for h in session_holds
                      if h.kind == "alternative_pending")
        anomalous = sum(h.guests for h in session_holds
                        if h.order_id in anomalous_orders)
        anomaly_orders = tuple(sorted(
            {h.order_id for h in session_holds if h.order_id in anomalous_orders}))
        used = confirmed + alt + pending
        if snap.status == "cancelled":
            releasable = 0
        else:
            releasable = max(0, snap.capacity - used)
        lines.append(CapacityLine(
            session=session_id, product=snap.product, status=snap.status,
            capacity=snap.capacity, confirmed_guests=confirmed,
            alternative_guests=alt, pending_guests=pending,
            anomalous_guests=anomalous, releasable_seats=releasable,
            anomaly_order_ids=anomaly_orders,
        ))
    # 注：异常订单若无权利行，则不占座，其现金敞口由 reconcile 模块暴露。
    return lines


@dataclass(frozen=True)
class ReleaseRecommendation:
    session: str
    product: str
    market: str
    price: int
    releasable_seats: int
    conversion_lower: float
    conversion_upper: float
    offers_optimistic: int | None      # ceil(seats / U)
    offers_conservative: int | None    # ceil(seats / L)，L=0 → None（不可知）
    expected_fill_low: int             # 按 L 估算的转化数
    expected_fill_high: int            # 按 U 估算的转化数
    note: str


def recommend_release(lines: list[CapacityLine], stats: list[CellStats],
                      market: str) -> list[ReleaseRecommendation]:
    """把需求区间翻译成每个场次的新报价放量建议。"""

    lookup = {(s.market, s.price): s for s in stats}
    prices = sorted({s.price for s in stats if s.market == market})
    recs: list[ReleaseRecommendation] = []
    for line in lines:
        if line.status == "cancelled":
            for price in prices:
                stat = lookup[(market, price)]
                recs.append(ReleaseRecommendation(
                    line.session, line.product, market, price, 0,
                    stat.lower, stat.upper, 0, 0, 0, 0,
                    "场次已取消，不接受新放量；按改期/退款流程处理既有权利"))
            continue
        for price in prices:
            stat = lookup[(market, price)]
            seats = line.releasable_seats
            optimistic = (math.ceil(seats / stat.upper)
                          if stat.upper > 0 else None)
            conservative = (math.ceil(seats / stat.lower)
                            if stat.lower > 0 else None)
            # 预期转化区间针对“按保守量报价”的情形。
            if conservative is not None:
                exp_low = math.floor(conservative * stat.lower)
                exp_high = math.floor(conservative * stat.upper)
                note = "按转化下界反推报价数，最差情形也可填满可放席位"
            else:
                exp_low = 0
                exp_high = 0
                note = "转化下界为 0，无法保证填满，放量上界不可知，需先扩样"
            recs.append(ReleaseRecommendation(
                line.session, line.product, market, price, seats,
                stat.lower, stat.upper, optimistic, conservative,
                exp_low, exp_high if exp_high is not None else 0, note))
    return recs
