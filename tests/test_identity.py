"""时点去重测试：事后链接不得跨组归并。"""

import unittest

from app.identity import (Link, components_for_day, dedup_by_block,
                          late_link_audit)


class IdentityTest(unittest.TestCase):
    def test_links_only_visible_after_learned_day(self):
        links = [Link("J1", "A", "B", 5), Link("J2", "B", "C", 9)]
        accounts = ["A", "B", "C"]
        day1 = components_for_day(links, accounts, 4)
        self.assertEqual(len({day1["A"], day1["B"], day1["C"]}), 3)
        day5 = components_for_day(links, accounts, 5)
        self.assertEqual(day5["A"], day5["B"])
        self.assertNotEqual(day5["B"], day5["C"])
        day9 = components_for_day(links, accounts, 9)
        self.assertEqual(len({day9["A"], day9["B"], day9["C"]}), 1)

    def test_block_dedup_ignores_late_links(self):
        offers = [
            {"block": "B1", "assigned_day": 1, "account": "A",
             "market": "east", "price": 90},
            {"block": "B1", "assigned_day": 1, "account": "B",
             "market": "east", "price": 150},
        ]
        links = [Link("J1", "A", "B", 11)]
        dedup = dedup_by_block(offers, links)
        self.assertNotEqual(dedup["B1"].components["A"],
                            dedup["B1"].components["B"])
        self.assertEqual([link.link_id for link in dedup["B1"].late_links],
                         ["J1"])

    def test_late_audit_flags_cross_cell_merges(self):
        offers = [
            {"block": "B1", "assigned_day": 1, "account": "A",
             "market": "east", "price": 90},
            {"block": "B2", "assigned_day": 2, "account": "B",
             "market": "east", "price": 150},
        ]
        audit = late_link_audit(offers, [Link("J1", "A", "B", 12)])
        self.assertTrue(audit[0]["would_merge_cells"])


if __name__ == "__main__":
    unittest.main()
