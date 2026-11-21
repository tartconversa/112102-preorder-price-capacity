"""资料导入与结构校验。

一个 *轮次（round）* 是一份不可变的输入目录，包含 README 约定的六张 CSV。
基线轮次即仓库内的 sources/；新一轮合成资料由 ``import-round`` 复制到
``data/rounds/<名称>/`` 后只读使用，分析与决策记录都引用该快照及文件指纹。
"""

from __future__ import annotations

import csv
import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

TABLES = (
    "offers",
    "commerce_events",
    "identity_links",
    "sales_terms",
    "sessions",
    "booking_rights",
)

HEADERS = {
    "offers": [
        "offer", "household", "account", "market", "block", "assigned_day",
        "price", "deposit", "offer_state",
    ],
    "commerce_events": [
        "event", "offer", "order", "day", "kind", "amount", "replaces",
    ],
    "identity_links": ["link", "left_account", "right_account", "learned_day"],
    "sales_terms": [
        "term", "product", "preserves_price", "keeps_party",
        "replacement_requires_consent",
    ],
    "sessions": ["session", "product", "day", "capacity", "status", "learned_day"],
    "booking_rights": [
        "right", "order", "term", "session", "guests", "state",
        "recorded_day", "alternative", "expires_day",
    ],
}

INT_FIELDS = {
    "offers": {"assigned_day", "price", "deposit"},
    "commerce_events": {"day", "amount"},
    "identity_links": {"learned_day"},
    "sessions": {"day", "capacity", "learned_day"},
    "booking_rights": {"guests", "recorded_day", "expires_day"},
}


class DataError(ValueError):
    """输入资料违反结构或引用约定。"""


@dataclass(frozen=True)
class Round:
    """一轮已封存的输入资料。"""

    name: str
    path: Path
    tables: dict[str, list[dict[str, object]]]
    fingerprints: dict[str, str]

    def table(self, name: str) -> list[dict[str, object]]:
        return self.tables[name]

    def horizon(self) -> int:
        """资料中出现的最大日期，作为默认截止日。"""
        days = [0]
        for row in self.tables["commerce_events"]:
            days.append(int(row["day"]))
        for row in self.tables["sessions"]:
            days.append(int(row["learned_day"]))
        for row in self.tables["booking_rights"]:
            days.append(int(row["recorded_day"]))
        return max(days)


def _coerce(name: str, rows: list[dict[str, str]]) -> list[dict[str, object]]:
    int_fields = INT_FIELDS.get(name, set())
    out: list[dict[str, object]] = []
    for raw in rows:
        row: dict[str, object] = {}
        for key, value in raw.items():
            if key in int_fields:
                row[key] = int(value) if value != "" else None
            else:
                row[key] = value
        out.append(row)
    return out


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_round(path: str | Path, name: str | None = None) -> Round:
    """读取并校验一个轮次目录。"""

    root = Path(path)
    if not root.is_dir():
        raise DataError(f"轮次目录不存在: {root}")
    tables: dict[str, list[dict[str, object]]] = {}
    fingerprints: dict[str, str] = {}
    for table in TABLES:
        csv_path = root / f"{table}.csv"
        if not csv_path.is_file():
            raise DataError(f"缺少资料表: {csv_path}")
        with csv_path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != HEADERS[table]:
                raise DataError(
                    f"{table}.csv 表头不符: 期望 {HEADERS[table]}, 实际 {reader.fieldnames}"
                )
            raw_rows = list(reader)
        if not raw_rows:
            raise DataError(f"{table}.csv 为空")
        for row in raw_rows:
            if None in row:
                raise DataError(f"{table}.csv 存在多余列: {row}")
            if any(value is None for value in row.values()):
                raise DataError(f"{table}.csv 存在空字段: {row}")
        try:
            tables[table] = _coerce(table, raw_rows)
        except ValueError as exc:
            raise DataError(f"{table}.csv 数值字段无法解析: {exc}") from exc
        fingerprints[table] = _fingerprint(csv_path)
    validate_references(tables)
    return Round(name=name or root.name, path=root.resolve(),
                 tables=tables, fingerprints=fingerprints)


def validate_references(tables: dict[str, list[dict[str, object]]]) -> None:
    """跨表引用与基本业务形状校验。"""

    offers = {r["offer"]: r for r in tables["offers"]}
    events = tables["commerce_events"]
    event_ids = {r["event"] for r in events}
    for row in events:
        if row["offer"] not in offers:
            raise DataError(f"回执 {row['event']} 引用未知报价 {row['offer']}")
        if row["replaces"] and row["replaces"] not in event_ids:
            raise DataError(
                f"回执 {row['event']} 撤回了未知回执 {row['replaces']}"
            )
        if row["kind"] == "refund_reversed" and not row["replaces"]:
            raise DataError(f"退款撤回 {row['event']} 缺少 replaces 引用")
    terms = {r["term"] for r in tables["sales_terms"]}
    sessions = {r["session"] for r in tables["sessions"]}
    for row in tables["booking_rights"]:
        if row["term"] not in terms:
            raise DataError(f"权利 {row['right']} 引用未知条款 {row['term']}")
        if row["alternative"] and row["alternative"] not in sessions:
            raise DataError(
                f"权利 {row['right']} 引用未知替代场次 {row['alternative']}"
            )
    # 报价与事件的订单编号必须一一对应（同报价多事件共用同一订单）。
    order_by_offer: dict[str, str] = {}
    for row in events:
        seen = order_by_offer.setdefault(str(row["offer"]), str(row["order"]))
        if seen != row["order"]:
            raise DataError(
                f"报价 {row['offer']} 出现多个订单编号: {seen} / {row['order']}"
            )
    # 金额非负。
    for row in events:
        if int(row["amount"]) < 0:
            raise DataError(f"回执 {row['event']} 金额为负")


def import_round(source: str | Path, dest_root: str | Path, name: str) -> Round:
    """把新的合成资料目录复制为受管轮次并加载校验。

    源目录被视为不可信数据：只复制其中约定的六张 CSV。先在同级临时目录
    完成复制与全部校验，再原子改名落位——校验失败不会留下半成品目录。
    """

    src = Path(source)
    if not src.is_dir():
        raise DataError(f"源资料目录不存在: {src}")
    dest_root = Path(dest_root)
    dest = dest_root / name
    if dest.exists():
        raise DataError(f"轮次已存在: {dest}")
    dest_root.mkdir(parents=True, exist_ok=True)
    staging = dest_root / f".{name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        for table in TABLES:
            src_file = src / f"{table}.csv"
            if not src_file.is_file():
                raise DataError(f"源资料缺少 {table}.csv")
            shutil.copy2(src_file, staging / f"{table}.csv")
        round_obj = load_round(staging, name=name)
        staging.rename(dest)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return Round(name=name, path=dest.resolve(),
                 tables=round_obj.tables, fingerprints=round_obj.fingerprints)
