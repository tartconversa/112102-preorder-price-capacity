"""命令行入口。

用法（在项目根目录，建议以隔离模式运行以避免不可信目录中的模块注入）：

    python -I run.py analyze baseline --as-of-day 14
    python -I run.py plan baseline --market east --note "首批放量"
    python -I run.py import-round /path/to/new/drop --name round2
    python -I run.py decisions
    python -I run.py decision plan-20261122-000000

也可在可信环境使用 python -m app.cli <命令>。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import reports
from .analysis import Analysis, run_analysis
from .ledger import DecisionLedger, DecisionRecord, fingerprints_of, new_version
from .loading import DataError, Round, import_round, load_round

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUNDS_ROOT = PROJECT_ROOT / "data" / "rounds"
REPORTS_ROOT = PROJECT_ROOT / "data" / "reports"
DECISIONS_ROOT = PROJECT_ROOT / "data" / "decisions"


def _resolve_round(name: str) -> Round:
    if name == "baseline":
        return load_round(PROJECT_ROOT / "sources", name="baseline")
    path = ROUNDS_ROOT / name
    return load_round(path, name=name)


def _report_dir(round_name: str, as_of_day: int, market: str) -> Path:
    path = REPORTS_ROOT / f"{round_name}-day{as_of_day}-{market}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_analysis_reports(analysis: Analysis) -> Path:
    out = _report_dir(analysis.round_obj.name, analysis.as_of_day,
                      analysis.market)
    # 明细 CSV
    reports.write_csv(out / "offer_outcomes.csv",
                      reports.outcomes_to_rows(analysis.outcomes))
    reports.write_csv(out / "demand_cells.csv", [
        {
            "market": c.market, "price": c.price, "n_components": c.n_components,
            "n_offers": c.n_offers, **c.counts,
            "demand_lower": round(c.lower, 6), "demand_upper": round(c.upper, 6),
        }
        for c in analysis.cells
    ])
    reports.write_csv(out / "deposit_channel_cells.csv", [
        {
            "market": c.market, "price": c.price, "n_components": c.n_components,
            "n_offers": c.n_offers, **c.counts,
            "demand_lower": round(c.lower, 6), "demand_upper": round(c.upper, 6),
        }
        for c in analysis.channel_cells
    ])
    reports.write_csv(out / "capacity.csv", [asdict(line)
                                             for line in analysis.capacity])
    reports.write_csv(out / "recommendations.csv", [asdict(rec)
                                                    for rec in analysis.recommendations])
    reports.write_csv(out / "reconciliation.csv", [asdict(item)
                                                   for item in analysis.reconciliation_items])
    reports.write_csv(out / "after_sales_worklist.csv", [asdict(task)
                                                         for task in analysis.worklist])
    reports.write_csv(out / "identity_late_links.csv", analysis.identity_audit)
    reports.write_csv(out / "consent_register.csv", [
        {
            "right": issue.right_id, "order": issue.order_id,
            "code": issue.code, "from_product": issue.from_product,
            "to_product": issue.to_product, "detail": issue.detail,
        }
        for issue in analysis.consent_issues
    ])

    # Markdown 汇总
    parts = [
        f"# 分析报告 — 轮次 {analysis.round_obj.name}，截止日 {analysis.as_of_day}",
        "",
        reports.identity_markdown(analysis.identity_audit),
        reports.demand_markdown(analysis.cells, "全渠道需求识别区间"),
        reports.demand_markdown(analysis.channel_cells,
                                "可退订金渠道（仅 deposit>0 且开放的报价）"),
        reports.effects_markdown(analysis.effects),
        reports.support_markdown(analysis.support, analysis.comparisons),
        reports.capacity_markdown(analysis.capacity),
        reports.consent_markdown(analysis.consent_issues),
        reports.recommendations_markdown(analysis.recommendations),
        reports.reconciliation_markdown(analysis.reconciliation_items),
        reports.worklist_markdown(analysis.worklist),
    ]
    (out / "report.md").write_text("\n".join(parts), encoding="utf-8")
    return out


def cmd_import_round(args: argparse.Namespace) -> int:
    round_obj = import_round(args.source, ROUNDS_ROOT, args.name)
    print(f"已导入轮次 {round_obj.name} → {round_obj.path}")
    for table, digest in round_obj.fingerprints.items():
        print(f"  {table}: sha256 {digest[:16]}…")
    return 0


def cmd_rounds(_args: argparse.Namespace) -> int:
    print("baseline  (sources/)")
    if ROUNDS_ROOT.exists():
        for path in sorted(ROUNDS_ROOT.iterdir()):
            if path.is_dir():
                print(f"{path.name}  ({path})")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    round_obj = _resolve_round(args.round)
    analysis = run_analysis(round_obj, as_of_day=args.as_of_day,
                            market=args.market,
                            support_threshold=args.support_threshold)
    out = _write_analysis_reports(analysis)
    print(f"截止日 {analysis.as_of_day} 的分析报告已写入 {out}")
    _print_console_summary(analysis)
    return 0


def _print_console_summary(analysis: Analysis) -> None:
    print("\n需求识别区间：")
    for cell in analysis.cells:
        print(f"  {cell.market:5s} 价 {cell.price:3d}  n={cell.n_components:2d}  "
              f"需求 ∈ [{cell.lower * 100:5.1f}%, {cell.upper * 100:5.1f}%]")
    print("\n可放容量：")
    for line in analysis.capacity:
        print(f"  {line.session}（{line.product}/{line.status}）: "
              f"容量 {line.capacity}，可放新名额 {line.releasable_seats}")
    if analysis.reconciliation_items:
        print("\n对账差异：")
        for item in analysis.reconciliation_items:
            print(f"  [{item.code}] {item.order_id}: {item.detail}")


def _build_decision(analysis: Analysis, notes: str) -> DecisionRecord:
    return DecisionRecord(
        version=new_version(),
        created_utc="",  # save 时由 ledger 填充不需要；此处保留显式字段
        round_name=analysis.round_obj.name,
        round_path=str(analysis.round_obj.path),
        as_of_day=analysis.as_of_day,
        market=analysis.market,
        support_threshold=analysis.support_threshold,
        input_fingerprints=fingerprints_of(analysis.round_obj),
        demand_cells=[
            {
                "market": c.market, "price": c.price,
                "n_components": c.n_components,
                "lower": round(c.lower, 6), "upper": round(c.upper, 6),
                "counts": c.counts,
            }
            for c in analysis.cells
        ],
        capacity_lines=[asdict(line) for line in analysis.capacity],
        recommendations=[asdict(rec) for rec in analysis.recommendations],
        reconciliation=[asdict(item)
                        for item in analysis.reconciliation_items],
        pending_commitments=[asdict(task) for task in analysis.worklist],
        consent_register=[asdict(issue) for issue in analysis.consent_issues],
        notes=notes,
    )


def cmd_plan(args: argparse.Namespace) -> int:
    round_obj = _resolve_round(args.round)
    analysis = run_analysis(round_obj, as_of_day=args.as_of_day,
                            market=args.market,
                            support_threshold=args.support_threshold)
    out = _write_analysis_reports(analysis)

    ledger = DecisionLedger(DECISIONS_ROOT)
    record = _build_decision(analysis, args.note or "")
    from datetime import datetime, timezone
    record.created_utc = datetime.now(timezone.utc).isoformat()
    path = ledger.save(record)
    print(f"报告目录: {out}")
    print(f"放量决策已存档: {path}（版本 {record.version}）")
    _print_console_summary(analysis)
    return 0


def cmd_decisions(_args: argparse.Namespace) -> int:
    ledger = DecisionLedger(DECISIONS_ROOT)
    versions = ledger.versions()
    if not versions:
        print("（尚无决策记录）")
        return 0
    for version in versions:
        data = ledger.load(version)
        print(f"{version}  轮次={data['round_name']}  截止日={data['as_of_day']}  "
              f"市场={data['market']}")
    return 0


def cmd_decision(args: argparse.Namespace) -> int:
    ledger = DecisionLedger(DECISIONS_ROOT)
    if not ledger.exists(args.version):
        print(f"未找到决策版本 {args.version}", file=sys.stderr)
        return 2
    json.dump(ledger.load(args.version), sys.stdout, ensure_ascii=False,
              indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app", description="报价试营需求识别与放量决策应用（仅标准库）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import-round", help="导入并校验新一轮合成资料")
    p_import.add_argument("source")
    p_import.add_argument("--name", required=True)
    p_import.set_defaults(func=cmd_import_round)

    p_rounds = sub.add_parser("rounds", help="列出受管轮次")
    p_rounds.set_defaults(func=cmd_rounds)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--as-of-day", type=int, default=None,
                        help="截止日；默认取资料中的最大日期")
    common.add_argument("--market", default="east",
                        help="放量推荐所服务的市场（默认 east）")
    common.add_argument("--support-threshold", type=int, default=10,
                        help="共同支持的最小家庭样本量（默认 10）")

    p_analyze = sub.add_parser("analyze", parents=[common],
                               help="按截止日重出需求/容量/对账报告")
    p_analyze.add_argument("round")
    p_analyze.set_defaults(func=cmd_analyze)

    p_plan = sub.add_parser("plan", parents=[common],
                            help="分析并提交一版放量决策（写入台账）")
    p_plan.add_argument("round")
    p_plan.add_argument("--note", default="")
    p_plan.set_defaults(func=cmd_plan)

    p_list = sub.add_parser("decisions", help="列放量决策版本")
    p_list.set_defaults(func=cmd_decisions)

    p_show = sub.add_parser("decision", help="查看某版决策的完整记录")
    p_show.add_argument("version")
    p_show.set_defaults(func=cmd_decision)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DataError as exc:
        print(f"资料错误: {exc}", file=sys.stderr)
        return 2
    except (FileExistsError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
