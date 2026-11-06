import copy
import unittest

from erp_demo.intake_review import review_order


def field(value, text=None, confidence=0.99):
    return {"value": value, "text": text or str(value), "page": 1, "confidence": confidence}


class ReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.order = {"customer": field("CUST-1"), "purchase_order": field("PO-1"),
                      "lines": [{"item": field("FG-LOTION"), "quantity": field(2),
                                 "unit_price": field("12.50")} ]}
        self.pages = {1: "CUST-1 PO-1 FG-LOTION 2 12.50"}
        self.customers = {"CUST-1"}
        self.items = {"FG-LOTION": "12.50"}

    def review(self, order):
        return review_order(order, self.pages, self.customers, self.items)

    def test_known_order(self):
        self.assertEqual(self.review(self.order), [])

    def test_unknown_customer_item_conflict_and_weak_evidence_are_held(self):
        order = copy.deepcopy(self.order)
        order["customer"]["value"] = "OTHER"
        order["lines"][0]["item"]["value"] = "UNKNOWN"
        order["lines"][0]["quantity"]["confidence"] = 0.2
        order["lines"][0]["unit_price"]["text"] = "invented"
        issues = self.review(order)
        self.assertTrue(any("unknown customer" in x for x in issues))
        self.assertTrue(any("unknown item" in x for x in issues))
        self.assertTrue(any("low or invalid confidence" in x for x in issues))
        self.assertTrue(any("unverifiable source" in x for x in issues))
        order["lines"][0]["item"]["value"] = "FG-LOTION"
        order["lines"][0]["unit_price"]["value"] = "12.51"
        self.assertTrue(any("price conflict" in x for x in self.review(order)))

    def test_invalid_numbers_and_empty_lines_are_held(self):
        self.order["lines"][0]["quantity"]["value"] = "NaN"
        self.order["lines"][0]["unit_price"]["value"] = "-1"
        self.assertTrue(any("invalid quantity" in x for x in self.review(self.order)))
        self.assertTrue(any("invalid price" in x for x in self.review(self.order)))
        self.order["lines"] = []
        self.assertIn("lines: no line items", self.review(self.order))


if __name__ == "__main__":
    unittest.main()
