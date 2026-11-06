"""The connector against real ERPNext, including native variants and multi-level explosion."""
import json
import os
from pathlib import Path
import unittest

from erp_demo.api import Client
from erp_demo.cad import setup_catalog, load_assembly, plan_sync, apply_sync, metadata

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/cad"


@unittest.skipUnless(os.getenv("ERP_INTEGRATION") == "1", "requires real ERPNext")
class CADIntegration(unittest.TestCase):
    def test_revisions_variants_reuse_and_blocked_mismatches(self):
        from erp_demo.workflow import seed
        api = Client.from_env()
        seed(api)
        self.assertEqual(setup_catalog(api, EXAMPLES / "catalog.json")["catalog_items"], 8)
        setup_catalog(api, EXAMPLES / "catalog.json")
        a = load_assembly(EXAMPLES / "filler-A.json")
        b = load_assembly(EXAMPLES / "filler-B.json")
        def sync(model):
            plan = plan_sync(api, model)
            self.assertEqual(plan["issues"], [])
            return apply_sync(api, model, plan["approval"])
        first = sync(a)
        self.assertEqual(len(first["boms"]), 3)
        self.assertEqual([r["action"] for r in sync(a)["boms"]], ["unchanged"] * 3)
        for config in ("BALM", "LOTION"):
            item = api.get("Item", "EQ-FILLER-" + config)
            self.assertEqual(item["variant_of"], "EQ-FILLER")
            self.assertEqual(item["attributes"][0]["attribute_value"], config.title())
        second = sync(b)
        self.assertEqual([r["action"] for r in second["boms"]], ["new_revision"] * 3)
        names_a = {r["part"]: r["bom"] for r in first["boms"]}
        names_b = {r["part"]: r["bom"] for r in second["boms"]}
        self.assertEqual(len(set(names_a.values()) | set(names_b.values())), 6)
        for names, bolts in ((names_a, 8), (names_b, 12)):
            balm = api.get("BOM", names["EQ-FILLER-BALM"])
            self.assertEqual(balm["docstatus"], 1)
            self.assertEqual(metadata(balm)["child_boms"]["EQ-HEAD"], names["EQ-HEAD"])
            self.assertEqual(next(r["qty"] for r in balm["items"] if r["item_code"] == "EQ-HEAD"), 2)
            # ERPNext itself explodes the pinned child, independently of our reuse calculation.
            exploded = {r["item_code"]: r["stock_qty"] for r in balm["exploded_items"]}
            self.assertEqual(exploded["EQ-M6-BOLT"], bolts)
        # New revisions do not switch the original default selected by ERPNext.
        self.assertEqual(api.get("Item", "EQ-FILLER-BALM")["default_bom"], names_a["EQ-FILLER-BALM"])
        b["parts"]["EQ-HEAD"]["components"][0]["qty"] += 1
        blocked = plan_sync(api, b)
        self.assertIn("changed_without_revision", {i["reason"] for i in blocked["issues"]})
        with self.assertRaisesRegex(ValueError, "blocked"):
            apply_sync(api, b, blocked["approval"])
        b = load_assembly(EXAMPLES / "filler-B.json")
        b["parts"]["EQ-M6-BOLT"]["part"] = "EQ-M6-B0LT"
        blocked_part = plan_sync(api, b)
        self.assertIn("item_mismatch", {i["reason"] for i in blocked_part["issues"]})
        b = load_assembly(EXAMPLES / "filler-B.json")
        b["parts"]["EQ-M6-BOLT"]["uom"] = "Kg"
        self.assertIn("item_mismatch", {i["reason"] for i in plan_sync(api, b)["issues"]})
        self.assertEqual(sum(len(api.list("BOM", {"item": code, "docstatus": ["!=", 2]}))
                             for code in names_a), 6)
        Path("artifacts").mkdir(exist_ok=True)
        Path("artifacts/cad-sync.json").write_text(json.dumps({
            "revision_a": first, "revision_b": second, "quantity_review": blocked,
            "part_review": blocked_part, "submitted_boms": 6}, indent=2))
        print("ERPNext CAD verified: 2 variants, 6 submitted BOMs, pinned A/B children, 8/12 exploded bolts, blocked mismatches")
