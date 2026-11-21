"""Markdown / CSV 报告渲染（纯文本表格，无第三方依赖）。"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from .capacity import CapacityLine, ReleaseRecommendation
from .demand import STATUS_LABELS, CellStats, OfferOutcome, PriceEffect, SupportVerdict
from .reconcile import AfterSalesTask, ReconciliationItem


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def write_csv(path: str | Path, rows: list[dict[str, object]],
              header: list[str] | None = None) -> None:
    if not rows:
        Path(path).write_text("", encoding="utf-8")
        return
    header = header or list(rows[0].keys())
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in header})
    Path(path).write_text(buffer.getvalue(), encoding="utf-8")


def outcomes_to_rows(outcomes: list[OfferOutcome]) -> list[dict[str, object]]:
    return [
        {
            "offer": o.offer_id, "market": o.market, "block": o.block,
            "price": o.price, "deposit": o.deposit, "account": o.account,
            "household_component": o.component, "offer_state": o.offer_state,
            "status": o.status, "status_label": STATUS_LABELS[o.status],
            "order": o.order_id, "net_cash": o.net_cash,
        }
        for o in outcomes
    ]


def demand_markdown(stats: list[CellStats], title: str) -> str:
    lines = [f"## {title}", "",
             "需求区间为部分识别结果：下界=有效付款/家庭数；"
             "上界=(家庭数−硬拒绝)/家庭数。硬拒绝=自行放弃+退款生效。", "",
             "| 市场 | 价格 | 家庭分母 n | 报价数 | 有效付款 | 自行放弃 | "
             "系统未给机会 | 仅登记 | 退款生效 | 需求下界 | 需求上界 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for cell in stats:
        c = cell.counts
        lines.append(
            f"| {cell.market} | {cell.price} | {cell.n_components} | "
            f"{cell.n_offers} | {c.get('converted', 0)} | "
            f"{c.get('self_abandon', 0)} | {c.get('no_opportunity', 0)} | "
            f"{c.get('intent_only', 0)} | {c.get('refunded', 0)} | "
            f"{_pct(cell.lower)} | {_pct(cell.upper)} |"
        )
    return "\n".join(lines) + "\n"


def effects_markdown(effects: list[PriceEffect]) -> str:
    lines = ["## 变价对需求范围的影响（区间识别）", "",
             "Δ需求 = 高价需求 − 低价需求；区间包含 0 表示资料无法确定方向。", "",
             "| 市场 | 低价 | 高价 | 低价区间 | 高价区间 | Δ下界 | Δ上界 | 方向 |",
             "|---|---:|---:|---|---|---:|---:|---|"]
    for effect in effects:
        if effect.delta_low >= 0:
            direction = "区间内均为非负（倾向提价不降需求）"
        elif effect.delta_high <= 0:
            direction = "区间内均为非正（倾向提价压低需求）"
        else:
            direction = "跨 0，方向不可识别"
        lines.append(
            f"| {effect.market} | {effect.price_low} | {effect.price_high} | "
            f"[{_pct(effect.lower_low)}, {_pct(effect.upper_low)}] | "
            f"[{_pct(effect.lower_high)}, {_pct(effect.upper_high)}] | "
            f"{_pct(effect.delta_low)} | {_pct(effect.delta_high)} | {direction} |"
        )
    return "\n".join(lines) + "\n"


def support_markdown(verdicts: list[SupportVerdict],
                     comparisons: list[dict[str, object]]) -> str:
    lines = ["## 共同支持与跨市场借用", "",
             "| 市场 | 价格 | 支持 | 说明 |",
             "|---|---:|:---:|---|"]
    for verdict in verdicts:
        lines.append(
            f"| {verdict.market} | {verdict.price} | "
            f"{'是' if verdict.supported else '否'} | {verdict.reason} |"
        )
    lines += ["", "### 可借用价格的区间差", "",
              "| 价格 | 可比较 | 说明/区间差 |",
              "|---:|:---:|---|"]
    for row in comparisons:
        if row.get("comparable"):
            lines.append(
                f"| {row['price']} | 是 | {row['market_b']}−{row['market_a']} "
                f"∈ [{_pct(row['delta_low'])}, {_pct(row['delta_high'])}] |"
            )
        else:
            lines.append(f"| {row['price']} | 否 | {row['reason']} |")
    return "\n".join(lines) + "\n"


def identity_markdown(audit: list[dict[str, object]]) -> str:
    lines = ["## 时点身份审计", "",
             "分组只采用各 block 分配日已可见的链接；下列链接在分组时点之后"
             "才被知晓，未用于任何归并。", "",
             "| 链接 | 左账号 | 右账号 | 知晓日 | 左 block/价 | 右 block/价 | "
             "若强行归并会跨单元 |",
             "|---|---|---|---:|---|---|:---:|"]
    if not audit:
        lines.append("| （无身份链接） | | | | | | |")
    for row in audit:
        lines.append(
            f"| {row['link']} | {row['left_account']} | {row['right_account']} | "
            f"{row['learned_day']} | {row['left_block']}/{row['left_price']} | "
            f"{row['right_block']}/{row['right_price']} | "
            f"{'是' if row['would_merge_cells'] else '否'} |"
        )
    return "\n".join(lines) + "\n"


def capacity_markdown(lines: list[CapacityLine]) -> str:
    head = ["## 可兑现席位与可放新名额（截止日口径）", "",
            "可放量 = 容量 − 确认权利 − 已接受替代 − 未过期替代邀约 − 异常冻结。",
            "",
            "| 场次 | 票种 | 状态 | 容量 | 确认占用 | 已接受替代 | "
            "待决邀约 | 异常冻结 | 可放新名额 | 异常订单 |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---|"]
    out = list(head)
    for line in lines:
        out.append(
            f"| {line.session} | {line.product} | {line.status} | "
            f"{line.capacity} | {line.confirmed_guests} | "
            f"{line.alternative_guests} | {line.pending_guests} | "
            f"{line.anomalous_guests} | {line.releasable_seats} | "
            f"{', '.join(line.anomaly_order_ids) or '—'} |"
        )
    return "\n".join(out) + "\n"


def recommendations_markdown(recs: list[ReleaseRecommendation]) -> str:
    lines = ["## 放量推荐（按需求识别区间反推）", "",
             "乐观报价数=ceil(席位/需求上界)；保守报价数=ceil(席位/需求下界)。"
             "下界为 0 时保守量不可知，需先扩样再放量。", "",
             "| 场次 | 票种 | 市场 | 价格 | 可放席位 | 转化区间 | "
             "乐观报价数 | 保守报价数 | 说明 |",
             "|---|---|---|---:|---:|---|---:|---:|---|"]
    for rec in recs:
        lines.append(
            f"| {rec.session} | {rec.product} | {rec.market} | {rec.price} | "
            f"{rec.releasable_seats} | [{_pct(rec.conversion_lower)}, "
            f"{_pct(rec.conversion_upper)}] | "
            f"{rec.offers_optimistic if rec.offers_optimistic is not None else '不可知'} | "
            f"{rec.offers_conservative if rec.offers_conservative is not None else '不可知'} | "
            f"{rec.note} |"
        )
    return "\n".join(lines) + "\n"


def reconciliation_markdown(items: list[ReconciliationItem]) -> str:
    lines = ["## 对账异常与现金敞口", "",
             "| 类型 | 订单 | 报价 | 现金影响 | 说明 |",
             "|---|---|---|---:|---|"]
    if not items:
        lines.append("| （无差异） | | | | |")
    for item in items:
        lines.append(
            f"| {item.code} | {item.order_id} | {item.offer_id} | "
            f"{item.cash_impact} | {item.detail} |"
        )
    return "\n".join(lines) + "\n"


def worklist_markdown(tasks: list[AfterSalesTask]) -> str:
    lines = ["## 售后工作台", "",
             "| 任务 | 订单 | 权利 | 场次 | 人数 | 到期日 | 说明 |",
             "|---|---|---|---|---:|---:|---|"]
    if not tasks:
        lines.append("| （无待办） | | | | | | |")
    for task in tasks:
        lines.append(
            f"| {task.kind} | {task.order_id} | {task.right_id} | "
            f"{task.session} | {task.guests} | "
            f"{task.deadline_day if task.deadline_day is not None else '—'} | "
            f"{task.detail} |"
        )
    return "\n".join(lines) + "\n"


def consent_markdown(issues: list[object]) -> str:
    lines = ["## 替代邀约与跨票种同意", "",
             "替代场次对应另一票种时，只能在征得家庭同意后使用。", "",
             "| 权利 | 订单 | 类型 | 原票种 | 替代票种 | 说明 |",
             "|---|---|---|---|---|---|"]
    if not issues:
        lines.append("| （无跨票种替代） | | | | | |")
    for issue in issues:
        lines.append(
            f"| {issue.right_id} | {issue.order_id} | {issue.code} | "
            f"{issue.from_product} | {issue.to_product} | {issue.detail} |"
        )
    return "\n".join(lines) + "\n"
