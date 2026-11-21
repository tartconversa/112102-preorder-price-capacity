"""需求的部分识别（partial identification）。

## 四类家庭状态

对每份报价按截止日（as-of day）判定：

- ``converted``          实际获得购买机会并完成有效付款（订金在账、退款未生效）
- ``self_abandon``       系统给了机会，家庭自行放弃（declined）
- ``no_opportunity``     系统未提供机会（sold_out；临时满位继续收意向不改变分组）
- ``intent_only``        仅登记意愿（deposit=0 的意向表，未观测到购买决定）
- ``refunded``           曾付款但截止日退款生效（观测到的未转化，硬信息）

## 锁定分母

分母只来自封存的 offers 分配表，按 block 分配日可见的去重关系计算家庭组件。
候补转正、权利变更、迟到回执都不能重新定义分母；事后身份链接不参与归并。

## 需求区间（Manski 界）

对市场 × 价格单元：

- 下界 L = 已确证付款家庭数 / n
- 上界 U = (n − 观测到的硬拒绝家庭数) / n
  硬拒绝 = declined 与退款生效的 refunded；sold_out 与 intent_only 属于未知带

变价影响用区间差表达：Δ(p_hi − p_lo) ∈ [L_hi − U_lo, U_hi − L_lo]。
市场间只在共同支持充分（两市场该价格样本量都达到阈值）时才可比较/借用，
否则显式标记 unsupported，禁止沿用邻区估计。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .finance import OrderLedger
from .identity import BlockDedup

CONVERTED = "converted"
SELF_ABANDON = "self_abandon"
NO_OPPORTUNITY = "no_opportunity"
INTENT_ONLY = "intent_only"
REFUNDED = "refunded"

STATUS_LABELS = {
    CONVERTED: "有效付款转化",
    SELF_ABANDON: "自行放弃",
    NO_OPPORTUNITY: "系统未提供机会",
    INTENT_ONLY: "仅登记意愿",
    REFUNDED: "退款生效（未转化）",
}

# 硬拒绝：已观测到的不购买决定，从上界中剔除。
HARD_REFUSAL = {SELF_ABANDON, REFUNDED}

DEFAULT_SUPPORT_THRESHOLD = 10


@dataclass(frozen=True)
class OfferOutcome:
    offer_id: str
    market: str
    block: str
    price: int
    deposit: int
    account: str
    component: str
    offer_state: str
    status: str
    order_id: str
    net_cash: int


@dataclass
class CellStats:
    market: str
    price: int
    n_offers: int
    n_components: int
    counts: dict[str, int] = field(default_factory=dict)
    lower: float = 0.0
    upper: float = 0.0

    @property
    def demand_low_units(self) -> int:
        return self.counts.get(CONVERTED, 0)

    @property
    def demand_high_units(self) -> int:
        refusals = sum(self.counts.get(s, 0) for s in HARD_REFUSAL)
        return self.n_components - refusals


def classify_offers(offer_rows: list[dict[str, object]],
                    ledgers: dict[str, OrderLedger],
                    dedup: dict[str, BlockDedup],
                    as_of_day: int) -> list[OfferOutcome]:
    """按截止日给每份报价划分四类（加退款）状态。"""

    outcomes: list[OfferOutcome] = []
    for row in offer_rows:
        offer_id = str(row["offer"])
        block = str(row["block"])
        account = str(row["account"])
        component = dedup[block].components[account]
        state = str(row["offer_state"])
        order_id = next(
            (ledger.order_id for ledger in ledgers.values()
             if ledger.offer_id == offer_id),
            "",
        )
        ledger = next(
            (ledger for ledger in ledgers.values()
             if ledger.offer_id == offer_id),
            None,
        )
        net_cash = ledger.net_cash(as_of_day) if ledger else 0

        if state == "sold_out":
            status = NO_OPPORTUNITY
        elif ledger is None:
            # opened 报价却没有任何回执：视为仅登记（系统未观测到决定）。
            status = INTENT_ONLY
        else:
            last = ledger.last_customer_event(as_of_day)
            kind = last.kind if last else None
            if kind == "charged" and ledger.is_paid(as_of_day):
                status = CONVERTED
            elif kind == "charged":
                # 收过款但退款当前生效 → 观测到的未转化。
                status = REFUNDED
            elif kind == "declined":
                status = SELF_ABANDON
            else:
                status = INTENT_ONLY
        outcomes.append(OfferOutcome(
            offer_id=offer_id, market=str(row["market"]), block=block,
            price=int(row["price"]), deposit=int(row["deposit"]),
            account=account, component=component, offer_state=state,
            status=status, order_id=order_id, net_cash=net_cash,
        ))
    return outcomes


def _component_status(outcomes: list[OfferOutcome]) -> dict[str, str]:
    """把同单元内同组件的多份报价折叠为一个家庭结论。"""

    priority = [CONVERTED, REFUNDED, SELF_ABANDON, NO_OPPORTUNITY, INTENT_ONLY]
    groups: dict[str, list[OfferOutcome]] = {}
    for outcome in outcomes:
        groups.setdefault(outcome.component, []).append(outcome)
    result: dict[str, str] = {}
    for component, items in groups.items():
        statuses = {item.status for item in items}
        for status in priority:
            if status in statuses:
                result[component] = status
                break
    return result


def cell_stats(outcomes: list[OfferOutcome]) -> list[CellStats]:
    """市场 × 价格单元的需求区间。"""

    cells: dict[tuple[str, int], list[OfferOutcome]] = {}
    for outcome in outcomes:
        cells.setdefault((outcome.market, outcome.price), []).append(outcome)
    stats: list[CellStats] = []
    for (market, price), items in sorted(cells.items()):
        component_status = _component_status(items)
        counts = {status: 0 for status in STATUS_LABELS}
        for status in component_status.values():
            counts[status] += 1
        n = len(component_status)
        lower = counts[CONVERTED] / n if n else 0.0
        refusals = sum(counts[s] for s in HARD_REFUSAL)
        upper = (n - refusals) / n if n else 0.0
        stats.append(CellStats(
            market=market, price=price, n_offers=len(items),
            n_components=n, counts=counts, lower=lower, upper=upper,
        ))
    return stats


def deposit_channel_stats(outcomes: list[OfferOutcome]) -> list[CellStats]:
    """仅含可退订金渠道（deposit>0 且实际开放）的转化区间。

    意向表与 sold_out 不进入该渠道分母；用于和全单元区间对照解释渠道作用。
    """

    channel = [o for o in outcomes
               if o.deposit > 0 and o.offer_state == "opened"]
    return cell_stats(channel)


@dataclass(frozen=True)
class PriceEffect:
    market: str
    price_low: int
    price_high: int
    delta_low: float          # L_hi - U_lo
    delta_high: float         # U_hi - L_lo
    lower_low: float
    lower_high: float
    upper_low: float
    upper_high: float


def price_effects(stats: list[CellStats]) -> list[PriceEffect]:
    """同一市场内各价格对的需求差区间（变价对需求范围的影响）。"""

    by_market: dict[str, list[CellStats]] = {}
    for stat in stats:
        by_market.setdefault(stat.market, []).append(stat)
    effects: list[PriceEffect] = []
    for market, cells in sorted(by_market.items()):
        cells = sorted(cells, key=lambda c: c.price)
        for i, lo in enumerate(cells):
            for hi in cells[i + 1:]:
                effects.append(PriceEffect(
                    market=market, price_low=lo.price, price_high=hi.price,
                    delta_low=hi.lower - lo.upper,
                    delta_high=hi.upper - lo.lower,
                    lower_low=lo.lower, lower_high=hi.lower,
                    upper_low=lo.upper, upper_high=hi.upper,
                ))
    return effects


@dataclass(frozen=True)
class SupportVerdict:
    market: str
    price: int
    supported: bool
    reason: str
    n_components: int


def common_support(stats: list[CellStats],
                   threshold: int = DEFAULT_SUPPORT_THRESHOLD) -> list[SupportVerdict]:
    """逐市场 × 价格判断共同支持；不足的单元禁止借用邻区估计。"""

    prices = sorted({s.price for s in stats})
    markets = sorted({s.market for s in stats})
    lookup = {(s.market, s.price): s for s in stats}
    verdicts: list[SupportVerdict] = []
    for market in markets:
        for price in prices:
            stat = lookup.get((market, price))
            if stat is None:
                verdicts.append(SupportVerdict(
                    market, price, False,
                    "本市场该价格没有任何试验分配，无共同支持，禁止借用邻区", 0))
            elif stat.n_components < threshold:
                verdicts.append(SupportVerdict(
                    market, price, False,
                    f"样本 {stat.n_components} 低于支持阈值 {threshold}",
                    stat.n_components))
            else:
                verdicts.append(SupportVerdict(
                    market, price, True,
                    f"样本 {stat.n_components} 达到支持阈值 {threshold}",
                    stat.n_components))
    return verdicts


def cross_market_comparison(stats: list[CellStats],
                            threshold: int = DEFAULT_SUPPORT_THRESHOLD
                            ) -> list[dict[str, object]]:
    """仅对两市场都有充分支持的价格，给出需求区间差（可借用的范围）。"""

    verdicts = {(v.market, v.price): v for v in common_support(stats, threshold)}
    markets = sorted({s.market for s in stats})
    prices = sorted({s.price for s in stats})
    lookup = {(s.market, s.price): s for s in stats}
    rows: list[dict[str, object]] = []
    if len(markets) < 2:
        return rows
    base, other = markets[0], markets[1]
    for price in prices:
        va, vb = verdicts.get((base, price)), verdicts.get((other, price))
        if va is None or vb is None or not va.supported or not vb.supported:
            reason_bits = []
            if va is None or not va.supported:
                reason_bits.append(f"{base} 支持不足")
            if vb is None or not vb.supported:
                reason_bits.append(f"{other} 支持不足")
            rows.append({
                "price": price, "comparable": False,
                "reason": "共同支持不足（" + "，".join(reason_bits) + "），禁止跨市场借用",
            })
            continue
        a, b = lookup[(base, price)], lookup[(other, price)]
        rows.append({
            "price": price, "comparable": True,
            "market_a": base, "market_b": other,
            "lower_a": a.lower, "upper_a": a.upper,
            "lower_b": b.lower, "upper_b": b.upper,
            # (b - a) 的识别区间
            "delta_low": b.lower - a.upper,
            "delta_high": b.upper - a.lower,
        })
    return rows
