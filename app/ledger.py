"""放量决策版本台账。

每次提交放量方案都生成一份不可变决策记录（JSON，仅标准库），记录：
- 轮次名称与六张输入表的 sha256 指纹
- 截止日、共同支持阈值、市场
- 各场次可放容量与推荐报价区间
- 当时的需求识别区间与对账异常摘要

财务可凭版本号追查任一版放量决策所依据的付款、可兑现席位和未清偿承诺。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .loading import Round


@dataclass
class DecisionRecord:
    version: str
    created_utc: str
    round_name: str
    round_path: str
    as_of_day: int
    market: str
    support_threshold: int
    input_fingerprints: dict[str, str]
    demand_cells: list[dict[str, object]]
    capacity_lines: list[dict[str, object]]
    recommendations: list[dict[str, object]]
    reconciliation: list[dict[str, object]]
    pending_commitments: list[dict[str, object]]
    consent_register: list[dict[str, object]]
    notes: str = ""


class DecisionLedger:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, version: str) -> Path:
        return self.root / f"{version}.json"

    def exists(self, version: str) -> bool:
        return self._path(version).exists()

    def save(self, record: DecisionRecord) -> Path:
        path = self._path(record.version)
        if path.exists():
            raise FileExistsError(f"决策版本已存在: {record.version}")
        with path.open("w", encoding="utf-8") as stream:
            json.dump(asdict(record), stream, ensure_ascii=False, indent=2,
                      sort_keys=True)
            stream.write("\n")
        return path

    def load(self, version: str) -> dict[str, object]:
        with self._path(version).open(encoding="utf-8") as stream:
            return json.load(stream)

    def versions(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.json"))


def new_version(prefix: str = "plan") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}"


def fingerprints_of(round_obj: Round) -> dict[str, str]:
    return dict(round_obj.fingerprints)
