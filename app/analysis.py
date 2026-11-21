"""截止日分析流水线：把六张表加工成全套结论。"""

from __future__ import annotations

from dataclasses import dataclass

from .capacity import CapacityLine, ReleaseRecommendation, capacity_plan, recommend_release
from .demand import (DEFAULT_SUPPORT_THRESHOLD, CellStats, OfferOutcome,
                     PriceEffect, SupportVerdict, cell_stats,
                     common_support, cross_market_comparison,
                     deposit_channel_stats, price_effects, classify_offers)
from .finance import Anomaly, OrderLedger, parse_receipts, receipt_chain_anomalies
from .identity import BlockDedup, dedup_by_block, late_link_audit, parse_links
from .loading import Round
from .reconcile import AfterSalesTask, ReconciliationItem, after_sales_worklist, reconcile
from .rights import Right, Term, check_alternative_consent, parse_rights, parse_terms


@dataclass
class Analysis:
    round_obj: Round
    as_of_day: int
    support_threshold: int
    market: str
    dedup: dict[str, BlockDedup]
    outcomes: list[OfferOutcome]
    cells: list[CellStats]
    channel_cells: list[CellStats]
    effects: list[PriceEffect]
    support: list[SupportVerdict]
    comparisons: list[dict[str, object]]
    ledgers: dict[str, OrderLedger]
    anomalies: list[Anomaly]
    terms: dict[str, Term]
    rights: list[Right]
    capacity: list[CapacityLine]
    recommendations: list[ReleaseRecommendation]
    reconciliation_items: list[ReconciliationItem]
    worklist: list[AfterSalesTask]
    identity_audit: list[dict[str, object]]
    consent_issues: list[object]


def run_analysis(round_obj: Round, as_of_day: int | None = None,
                 market: str = "east",
                 support_threshold: int = DEFAULT_SUPPORT_THRESHOLD) -> Analysis:
    if as_of_day is None:
        as_of_day = round_obj.horizon()
    tables = round_obj.tables

    links = parse_links(tables["identity_links"])
    dedup = dedup_by_block(tables["offers"], links)
    audit = late_link_audit(tables["offers"], links)

    ledgers = parse_receipts(tables["commerce_events"])
    anomalies = receipt_chain_anomalies(ledgers)

    outcomes = classify_offers(tables["offers"], ledgers, dedup, as_of_day)
    cells = cell_stats(outcomes)
    channel_cells = deposit_channel_stats(outcomes)
    effects = price_effects(cells)
    support = common_support(cells, support_threshold)
    comparisons = cross_market_comparison(cells, support_threshold)

    terms = parse_terms(tables["sales_terms"])
    rights = parse_rights(tables["booking_rights"])
    consent = check_alternative_consent(rights, terms,
                                        tables["sessions"], as_of_day)

    plan = capacity_plan(tables["sessions"], rights, anomalies, as_of_day)
    recs = recommend_release(plan, cells, market)

    recon = reconcile(outcomes, ledgers, rights, anomalies, as_of_day)
    worklist = after_sales_worklist(rights, as_of_day)

    return Analysis(
        round_obj=round_obj, as_of_day=as_of_day,
        support_threshold=support_threshold, market=market,
        dedup=dedup, outcomes=outcomes, cells=cells,
        channel_cells=channel_cells, effects=effects, support=support,
        comparisons=comparisons, ledgers=ledgers, anomalies=anomalies,
        terms=terms, rights=rights, capacity=plan, recommendations=recs,
        reconciliation_items=recon, worklist=worklist,
        identity_audit=audit, consent_issues=consent,
    )
