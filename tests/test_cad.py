"""Synthetic CAD assemblies for validation and recovery; real REST contract is separate."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from erp_demo.api import ERPError
from erp_demo.cad import (FIELD, ATTRIBUTE, validate, load_assembly, plan_sync, apply_sync,
                          reusable_parts, metadata, setup_catalog)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/cad"


class MemoryERP:
    """Document store for failure injection, deliberately not an ERPNext replacement."""
    def __init__(self):
        self.docs, self.writes = {}, []
        self.docs[("Custom Field", "BOM-" + FIELD)] = {"name": "BOM-" + FIELD}
        model = load_assembly(EXAMPLES / "filler-A.json")
        for code, p in model["parts"].items():
            self.docs[("Item", code)] = dict(item_code=code, stock_uom="Nos", is_stock_item=1,
                                           attributes=[], disabled=0, has_variants=0)
            if p.get("template"):
                self.docs[("Item", code)].update(variant_of=p["template"], attributes=[
                    {"attribute": ATTRIBUTE, "attribute_value": p["configuration"]}])

    def get(self, doctype, name):
        if (doctype, name) not in self.docs:
            raise ERPError(404, "DoesNotExistError")
        return deepcopy(self.docs[(doctype, name)])

    def list(self, doctype, filters=None):
        return [{"name": name} for (dt, name), doc in self.docs.items()
                if dt == doctype and doc["item"] == filters["item"] and doc["docstatus"] != 2]

    def create(self, doctype, **fields):
        name = "BOM-" + str(len(self.writes))
        doc = dict(fields, name=name, docstatus=0, uom="Nos")
        self.docs[(doctype, name)] = deepcopy(doc)
        self.writes.append(("create", name))
        return doc

    def submit(self, doc):
        doc = dict(doc, docstatus=1)
        self.docs[("BOM", doc["name"])] = deepcopy(doc)
        self.writes.append(("submit", doc["name"]))
        return doc


class AssemblyValidation(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((EXAMPLES / "filler-A.json").read_text())

    def test_exported_hierarchy_and_reuse_multiply_every_path(self):
        model = validate(self.data)
        self.assertLess(model["order"].index("EQ-HEAD"), model["order"].index("EQ-FILLER-BALM"))
        usage = {r["part"]: r for r in reusable_parts(model)}
        self.assertEqual(len(usage), 4)
        self.assertEqual(usage["EQ-M6-BOLT"]["assemblies"], {"EQ-FILLER-BALM": 8, "EQ-FILLER-LOTION": 4})
        self.assertEqual(usage["EQ-M6-BOLT"]["total_qty"], 12)

    def test_invalid_quantities(self):
        for qty in (0, -1, .5, True, "2", float("nan"), float("inf"), 1000001):
            with self.subTest(qty=qty):
                data = deepcopy(self.data)
                next(p for p in data["parts"] if p.get("components"))["components"][0]["qty"] = qty
                with self.assertRaises(ValueError):
                    validate(data)

    def test_unknown_part_numbers(self):
        self.data["parts"] = [p for p in self.data["parts"] if p["part"] != "EQ-M6-BOLT"]
        with self.assertRaisesRegex(ValueError, "Unknown part number"):
            validate(self.data)

    def test_cycle(self):
        next(p for p in self.data["parts"] if p["part"] == "EQ-HEAD")["components"].append(
            {"part": "EQ-FILLER-BALM", "qty": 1})
        with self.assertRaisesRegex(ValueError, "cycle"):
            validate(self.data)

    def test_duplicate_part_and_component(self):
        with self.assertRaisesRegex(ValueError, "Duplicate part"):
            validate(dict(self.data, parts=self.data["parts"] + [self.data["parts"][0]]))
        part = next(p for p in self.data["parts"] if p.get("components"))
        part["components"].append(part["components"][0])
        with self.assertRaisesRegex(ValueError, "Duplicate component"):
            validate(self.data)

    def test_unreachable_part(self):
        self.data["parts"].append({"part": "ORPHAN", "revision": "A", "name": "Orphan", "uom": "Nos"})
        with self.assertRaisesRegex(ValueError, "Unreachable"):
            validate(self.data)

    def test_unknown_fields_and_units_do_not_get_guessed(self):
        for field, value in (("quantitiy", 4), ("uom", "mm"), ("revision", ""), ("template", "PART")):
            with self.subTest(field=field):
                data = deepcopy(self.data)
                data["parts"][0][field] = value
                with self.assertRaises(ValueError):
                    validate(data)

    def test_same_configuration_cannot_have_two_part_numbers(self):
        parts = [p for p in self.data["parts"] if p.get("configuration")]
        parts[1]["configuration"] = parts[0]["configuration"]
        with self.assertRaisesRegex(ValueError, "same template configuration"):
            validate(self.data)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.api = MemoryERP()
        self.a = load_assembly(EXAMPLES / "filler-A.json")
        self.b = load_assembly(EXAMPLES / "filler-B.json")

    def sync(self, model=None):
        model = model or self.a
        plan = plan_sync(self.api, model)
        self.assertEqual(plan["issues"], [])
        return apply_sync(self.api, model, plan["approval"])

    def test_plan_is_read_only_and_initial_sync_links_children(self):
        plan = plan_sync(self.api, self.a)
        self.assertEqual(self.api.writes, [])
        self.assertEqual([a["action"] for a in plan["actions"]], ["create"] * 3)
        result = self.sync()
        names = {r["part"]: r["bom"] for r in result["boms"]}
        doc = self.api.get("BOM", names["EQ-FILLER-BALM"])
        row = next(r for r in doc["items"] if r["item_code"] == "EQ-HEAD")
        self.assertEqual(row["bom_no"], names["EQ-HEAD"])
        self.assertEqual(row["qty"], 2)

    def test_rerun_writes_nothing(self):
        self.sync()
        before = len(self.api.writes)
        self.sync()
        self.assertEqual(len(self.api.writes), before)

    def test_new_revision_keeps_old_boms_and_pins_new_child(self):
        old = self.sync()
        snapshot = deepcopy(self.api.docs)
        new = self.sync(self.b)
        self.assertEqual([r["action"] for r in new["boms"]], ["new_revision"] * 3)
        self.assertTrue(all(self.api.docs[key] == doc for key, doc in snapshot.items()))
        old_head = next(r["bom"] for r in old["boms"] if r["part"] == "EQ-HEAD")
        new_head = next(r["bom"] for r in new["boms"] if r["part"] == "EQ-HEAD")
        self.assertNotEqual(old_head, new_head)
        for row in new["boms"]:
            if row["part"].startswith("EQ-FILLER"):
                self.assertEqual(metadata(self.api.get("BOM", row["bom"]))["child_boms"]["EQ-HEAD"], new_head)

    def test_quantity_change_without_revision_is_blocked(self):
        self.sync()
        self.a["parts"]["EQ-HEAD"]["components"][0]["qty"] += 1
        plan = plan_sync(self.api, self.a)
        self.assertIn("changed_without_revision", {i["reason"] for i in plan["issues"]})
        before = len(self.api.writes)
        with self.assertRaisesRegex(ValueError, "blocked"):
            apply_sync(self.api, self.a, plan["approval"])
        self.assertEqual(len(self.api.writes), before)

    def test_unknown_erp_item_blocks_entire_graph(self):
        del self.api.docs[("Item", "EQ-M6-BOLT")]
        plan = plan_sync(self.api, self.a)
        self.assertIn({"part": "EQ-M6-BOLT", "reason": "unknown_item"}, plan["issues"])
        with self.assertRaises(ValueError):
            apply_sync(self.api, self.a, plan["approval"])
        self.assertEqual(self.api.writes, [])

    def test_wrong_units_disabled_and_variant_mismatch(self):
        for fields in ({"stock_uom": "Kg"}, {"disabled": 1}, {"variant_of": "WRONG"}, {"attributes": []}):
            with self.subTest(fields=fields):
                api = MemoryERP()
                api.docs[("Item", "EQ-FILLER-BALM")].update(fields)
                self.assertTrue(plan_sync(api, self.a)["issues"])

    def test_stale_approval_cannot_write(self):
        token = plan_sync(self.api, self.a)["approval"]
        self.a["parts"]["EQ-HEAD"]["revision"] = "B"
        with self.assertRaisesRegex(ValueError, "Approval"):
            apply_sync(self.api, self.a, token)
        self.assertEqual(self.api.writes, [])

    def test_stale_erp_approval_and_wrong_hash_cannot_write(self):
        token = plan_sync(self.api, self.a)["approval"]
        self.sync()
        for approval in (token, "wrong", ""):
            with self.subTest(approval=approval), self.assertRaisesRegex(ValueError, "Approval"):
                apply_sync(self.api, self.a, approval)

    def test_manual_bom_edit_is_detected_even_with_intact_source_hash(self):
        result = self.sync()
        self.api.docs[("BOM", result["boms"][0]["bom"])]["items"][0]["qty"] = 99
        self.assertIn("unmanaged_or_modified_bom", {i["reason"] for i in plan_sync(self.api, self.a)["issues"]})

    def test_unmanaged_and_duplicate_boms_block(self):
        result = self.sync()
        key = ("BOM", result["boms"][0]["bom"])
        doc = deepcopy(self.api.docs[key])
        doc["name"] = "DUPLICATE"
        self.api.docs[("BOM", doc["name"])] = doc
        self.assertIn("duplicate_revision", {i["reason"] for i in plan_sync(self.api, self.a)["issues"]})
        doc[FIELD] = ""
        self.assertIn("unmanaged_or_modified_bom", {i["reason"] for i in plan_sync(self.api, self.a)["issues"]})

    def test_interrupted_submission_resumes_existing_draft(self):
        submit = self.api.submit
        self.api.submit = lambda doc: (_ for _ in ()).throw(ERPError(503, "Unavailable"))
        with self.assertRaises(ERPError):
            self.sync()
        self.assertEqual(len(self.api.writes), 1)
        self.api.submit = submit
        self.assertEqual(plan_sync(self.api, self.a)["actions"][0]["action"], "resume_draft")
        self.sync()
        self.assertEqual(sum(w[0] == "create" for w in self.api.writes), 3)

    def test_parent_cannot_silently_switch_child_revision(self):
        result = self.sync()
        self.sync(self.b)
        parent = next(r["bom"] for r in result["boms"] if r["part"] == "EQ-FILLER-BALM")
        doc = self.api.docs[("BOM", parent)]
        meta = metadata(doc)
        new_head = next(a["bom"] for a in plan_sync(self.api, self.b)["actions"] if a["part"] == "EQ-HEAD")
        meta["child_boms"]["EQ-HEAD"] = new_head
        doc[FIELD] = json.dumps(meta)
        next(r for r in doc["items"] if r["item_code"] == "EQ-HEAD")["bom_no"] = new_head
        self.assertIn("child_revision_mismatch", {i["reason"] for i in plan_sync(self.api, self.a)["issues"]})

    def test_access_denied_is_not_retried_or_treated_as_missing(self):
        self.api.get = lambda *args: (_ for _ in ()).throw(ERPError(403, "PermissionError"))
        with self.assertRaises(ERPError) as caught:
            plan_sync(self.api, self.a)
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(self.api.writes, [])

    def test_missing_setup_is_review_issue(self):
        del self.api.docs[("Custom Field", "BOM-" + FIELD)]
        self.assertEqual(plan_sync(self.api, self.a)["issues"][0]["reason"], "setup_required")

    def test_catalog_validation_happens_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text('[{"part":"MALFORMED","components":[]}]')
            with self.assertRaises(ValueError):
                setup_catalog(None, path)
