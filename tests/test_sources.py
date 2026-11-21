import csv
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / "sources"
HEADERS = {'offers': ['offer', 'household', 'account', 'market', 'block', 'assigned_day', 'price', 'deposit', 'offer_state'], 'commerce_events': ['event', 'offer', 'order', 'day', 'kind', 'amount', 'replaces'], 'identity_links': ['link', 'left_account', 'right_account', 'learned_day'], 'sales_terms': ['term', 'product', 'preserves_price', 'keeps_party', 'replacement_requires_consent'], 'sessions': ['session', 'product', 'day', 'capacity', 'status', 'learned_day'], 'booking_rights': ['right', 'order', 'term', 'session', 'guests', 'state', 'recorded_day', 'alternative', 'expires_day']}
REFERENCES = [['commerce_events', 'offer', 'offers', 'offer'], ['commerce_events', 'replaces', 'commerce_events', 'event'], ['booking_rights', 'term', 'sales_terms', 'term'], ['booking_rights', 'alternative', 'sessions', 'session']]
COUNTS = [['commerce_events', 'kind', 'refund_reversed', 1], ['sessions', 'status', 'cancelled', 1], ['booking_rights', 'state', 'alternative_accepted', 1]]


class SourceContractTests(unittest.TestCase):
    def setUp(self):
        self.records = {}
        for name, header in HEADERS.items():
            with (ROOT / f"{name}.csv").open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, header)
                self.records[name] = list(reader)

    def test_record_shape(self):
        for name, records in self.records.items():
            with self.subTest(source=name):
                self.assertTrue(records)
                for row in records:
                    self.assertEqual(set(row), set(HEADERS[name]))
                    self.assertNotIn(None, row.values())
                    self.assertTrue(row[HEADERS[name][0]])
                self.assertEqual(len(records), len({tuple(row.values()) for row in records}))

    def test_cross_source_references(self):
        for child, fk, parent, pk in REFERENCES:
            with self.subTest(source=child, field=fk):
                keys = {row[pk] for row in self.records[parent]}
                values = {row[fk] for row in self.records[child] if row[fk]}
                self.assertTrue(values)
                self.assertFalse(values - keys)

    def test_source_has_relevant_observations(self):
        for source, field, value, minimum in COUNTS:
            with self.subTest(source=source, field=field, value=value):
                self.assertGreaterEqual(sum(row[field] == value for row in self.records[source]), minimum)
