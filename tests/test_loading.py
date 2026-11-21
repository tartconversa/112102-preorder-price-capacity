"""轮次导入、结构校验与决策台账测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from app.ledger import DecisionLedger
from app.loading import DataError, import_round, load_round

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "sources"
TABLES = ("offers", "commerce_events", "identity_links", "sales_terms",
          "sessions", "booking_rights")


class LoadingTest(unittest.TestCase):
    def test_baseline_loads_with_fingerprints(self):
        round_obj = load_round(SOURCES, name="baseline")
        self.assertEqual(set(round_obj.fingerprints), set(TABLES))
        self.assertTrue(all(len(d) == 64
                            for d in round_obj.fingerprints.values()))
        self.assertEqual(round_obj.horizon(), 14)

    def test_import_only_copies_known_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "drop"
            src.mkdir()
            for table in TABLES:
                (src / f"{table}.csv").write_text(
                    (SOURCES / f"{table}.csv").read_text(encoding="utf-8"),
                    encoding="utf-8")
            (src / "json.py").write_text("raise RuntimeError('planted')")
            dest_root = tmp_path / "rounds"
            round_obj = import_round(src, dest_root, "round2")
            self.assertEqual(
                {p.name for p in round_obj.path.iterdir()},
                {f"{t}.csv" for t in TABLES})
            self.assertFalse((round_obj.path / "json.py").exists())

    def test_broken_reference_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "drop"
            src.mkdir()
            for table in TABLES:
                (src / f"{table}.csv").write_text(
                    (SOURCES / f"{table}.csv").read_text(encoding="utf-8"),
                    encoding="utf-8")
            offers = (src / "offers.csv").read_text(encoding="utf-8").splitlines()
            (src / "offers.csv").write_text(
                "\n".join(offers[:3]) + "\n", encoding="utf-8")
            with self.assertRaises(DataError):
                import_round(src, Path(tmp) / "rounds", "bad")

    def test_existing_round_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "drop"
            src.mkdir()
            for table in TABLES:
                (src / f"{table}.csv").write_text(
                    (SOURCES / f"{table}.csv").read_text(encoding="utf-8"),
                    encoding="utf-8")
            import_round(src, tmp_path / "rounds", "r")
            with self.assertRaises(DataError):
                import_round(src, tmp_path / "rounds", "r")


class LedgerTest(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp)
            record = dict(version="plan-test", round_name="baseline",
                          input_fingerprints={"offers": "x"})
            path = ledger.save(_record(record))
            self.assertTrue(path.exists())
            loaded = ledger.load("plan-test")
            self.assertEqual(loaded["round_name"], "baseline")
            self.assertEqual(ledger.versions(), ["plan-test"])

    def test_duplicate_version_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp)
            ledger.save(_record({"version": "v1"}))
            with self.assertRaises(FileExistsError):
                ledger.save(_record({"version": "v1"}))

    def test_record_is_plain_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp)
            ledger.save(_record({
                "version": "v1",
                "capacity_lines": [{"session": "S1", "seats": [1, 2, 3]}],
            }))
            data = json.loads((Path(tmp) / "v1.json").read_text(encoding="utf-8"))
            self.assertEqual(data["capacity_lines"][0]["seats"], [1, 2, 3])


def _record(overrides):
    from app.ledger import DecisionRecord
    base = dict(
        version="v", created_utc="t", round_name="baseline",
        round_path="/x", as_of_day=14, market="east", support_threshold=10,
        input_fingerprints={}, demand_cells=[], capacity_lines=[],
        recommendations=[], reconciliation=[], pending_commitments=[],
        consent_register=[], notes="",
    )
    base.update(overrides)
    return DecisionRecord(**base)


if __name__ == "__main__":
    unittest.main()
