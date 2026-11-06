"""FreeCAD parts lists to immutable, explicitly linked ERPNext BOM revisions."""
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from .api import ERPError
from .workflow import COMPANY

FIELD = "custom_cad_sync"
ATTRIBUTE = "CAD Configuration"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Part numbers, revisions and configurations need 1-80 letters/numbers/._-")
    return value


def load_assembly(path):
    return validate(json.loads(Path(path).read_text()))


def validate(data):
    """Normalize order, reject ambiguous input, and return children before parents."""
    if not isinstance(data, dict) or set(data) != {"schema", "roots", "parts"} or data["schema"] != 1:
        raise ValueError("Expected schema 1 with roots and parts")
    if not isinstance(data["parts"], list) or not data["parts"]:
        raise ValueError("parts must be a nonempty list")
    parts = {}
    for source in data["parts"]:
        if not isinstance(source, dict) or set(source) - {
                "part", "revision", "name", "uom", "template", "configuration", "components"}:
            raise ValueError("Unknown part fields")
        if not {"part", "revision", "name", "uom"} <= source.keys():
            raise ValueError("Each part needs part, revision, name and uom")
        part = dict(source)
        code = identifier(part["part"])
        identifier(part["revision"])
        if code in parts:
            raise ValueError(f"Duplicate part number: {code}")
        if not isinstance(part["name"], str) or not part["name"].strip():
            raise ValueError(f"Missing name: {code}")
        if part["uom"] != "Nos":
            raise ValueError("CAD parts use Nos (whole counted pieces); no inferred unit conversion")
        if bool(part.get("template")) != bool(part.get("configuration")):
            raise ValueError("template and configuration must be supplied together")
        if part.get("template"):
            identifier(part["template"])
            identifier(part["configuration"])
        components = part.setdefault("components", [])
        if not isinstance(components, list):
            raise ValueError(f"components must be a list: {code}")
        seen = set()
        for row in components:
            if not isinstance(row, dict) or set(row) != {"part", "qty"}:
                raise ValueError("Each component needs exactly part and qty")
            identifier(row["part"])
            qty = row["qty"]
            if (isinstance(qty, bool) or not isinstance(qty, (int, float))
                    or not math.isfinite(qty) or qty <= 0 or int(qty) != qty or qty > 1000000):
                raise ValueError("Component quantities must be whole numbers from 1 to 1000000")
            if row["part"] in seen:
                raise ValueError(f"Duplicate component in {code}; aggregate occurrences in FreeCAD first")
            seen.add(row["part"])
        part["components"] = sorted((dict(r, qty=int(r["qty"])) for r in components), key=lambda r: r["part"])
        parts[code] = part
    roots = data["roots"]
    if (not isinstance(roots, list) or not roots or not all(isinstance(r, str) for r in roots)
            or len(set(roots)) != len(roots)):
        raise ValueError("roots must be a nonempty list of unique assembly part numbers")
    done, visiting, order = set(), set(), []

    def walk(code):
        identifier(code)
        if code not in parts:
            raise ValueError(f"Unknown part number: {code}")
        if code in visiting:
            raise ValueError(f"Assembly cycle at {code}")
        if code in done:
            return
        visiting.add(code)
        for row in parts[code]["components"]:
            walk(row["part"])
        visiting.remove(code)
        done.add(code)
        order.append(code)

    for root in sorted(roots):
        walk(root)
        if not parts[root]["components"]:
            raise ValueError("Each root must be an assembly")
    if done != parts.keys():
        raise ValueError("Unreachable parts are not allowed")
    templates = {p["template"] for p in parts.values() if p.get("template")}
    if templates & parts.keys():
        raise ValueError("A template cannot also be a concrete part")
    variants = [(p["template"], p["configuration"]) for p in parts.values() if p.get("template")]
    if len(set(variants)) != len(variants):
        raise ValueError("Two part numbers cannot represent the same template configuration")
    return {"schema": 1, "roots": sorted(roots), "parts": parts, "order": order}


def spec(model, code):
    part = model["parts"][code]
    return {**part, "components": [{**row, "revision": model["parts"][row["part"]]["revision"],
                                   "uom": model["parts"][row["part"]]["uom"]}
                                  for row in part["components"]]}


def reusable_parts(model):
    """Explode quantities along every path, preserving the count per root."""
    usage = {}
    for root in model["roots"]:
        counts = Counter()

        def walk(code, multiplier):
            for row in model["parts"][code]["components"]:
                quantity = multiplier * row["qty"]
                counts[row["part"]] += quantity
                walk(row["part"], quantity)

        walk(root, 1)
        for code, qty in counts.items():
            usage.setdefault(code, {})[root] = qty
    return [{"part": code, "assemblies": quantities, "total_qty": sum(quantities.values())}
            for code, quantities in sorted(usage.items()) if len(quantities) > 1]


def optional_get(api, doctype, name):
    try:
        return api.get(doctype, name)
    except ERPError as error:
        if error.status != 404:
            raise
        return None


def setup_catalog(api, path):
    """Explicitly provision approved master data; never infer it from CAD input."""
    catalog = json.loads(Path(path).read_text())
    if not isinstance(catalog, list) or not catalog:
        raise ValueError("Catalog must be a nonempty list")
    # Reuse the strict part checks without accepting BOMs in a master-data catalog.
    for row in catalog:
        if not isinstance(row, dict) or set(row) - {"part", "name", "uom", "template", "configuration"}:
            raise ValueError("Catalog contains unsupported fields")
    synthetic = {"part": "CATALOG-VALIDATION", "name": "Validation", "revision": "1", "uom": "Nos",
                 "components": [{"part": r.get("part"), "qty": 1} for r in catalog]}
    normalized = validate({"schema": 1, "roots": [synthetic["part"]],
                           "parts": [synthetic] + [dict(r, revision="1") for r in catalog]})
    api.ensure("Custom Field", "BOM-" + FIELD, dt="BOM", fieldname=FIELD,
               label="CAD sync metadata", fieldtype="Long Text", read_only=1, no_copy=1)
    api.ensure("UOM", "Nos", uom_name="Nos", must_be_whole_number=1)
    api.ensure("Item Group", "CAD Equipment", item_group_name="CAD Equipment", parent_item_group="All Item Groups")
    values = sorted({r["configuration"] for r in catalog if r.get("template")})
    if values:
        attribute = optional_get(api, "Item Attribute", ATTRIBUTE)
        if not attribute:
            api.create("Item Attribute", attribute_name=ATTRIBUTE,
                       item_attribute_values=[{"attribute_value": v, "abbr": v} for v in values])
        elif not set(values) <= {v["attribute_value"] for v in attribute["item_attribute_values"]}:
            raise ValueError("Add missing CAD Configuration values in ERPNext before setup")
    for template in sorted({r["template"] for r in catalog if r.get("template")}):
        api.ensure("Item", template, item_code=template, item_name=template, stock_uom="Nos",
                   item_group="CAD Equipment", has_variants=1, variant_based_on="Item Attribute",
                   attributes=[{"attribute": ATTRIBUTE}])
        item = api.get("Item", template)
        if (not item.get("has_variants") or item.get("stock_uom") != "Nos"
                or [r["attribute"] for r in item.get("attributes", [])] != [ATTRIBUTE]):
            raise ValueError(f"Existing template conflicts with catalog: {template}")
    for row in catalog:
        fields = dict(item_code=row["part"], item_name=row["name"], stock_uom=row["uom"],
                      item_group="CAD Equipment", is_stock_item=1)
        if row.get("template"):
            fields.update(variant_of=row["template"], variant_based_on="Item Attribute",
                          attributes=[{"attribute": ATTRIBUTE, "attribute_value": row["configuration"]}])
        api.ensure("Item", row["part"], **fields)
        if item_issue(api.get("Item", row["part"]), normalized["parts"][row["part"]]):
            raise ValueError(f"Existing item conflicts with catalog: {row['part']}")
    return {"catalog_items": len(catalog), "templates": sorted({r["template"] for r in catalog if r.get("template")})}


def item_issue(item, part):
    if item is None:
        return "unknown_item"
    if (item.get("item_code") != part["part"] or item.get("stock_uom") != part["uom"]
            or item.get("disabled") or item.get("has_variants") or not item.get("is_stock_item")):
        return "item_mismatch"
    expected = [(ATTRIBUTE, part["configuration"])] if part.get("template") else []
    actual = sorted((r["attribute"], r.get("attribute_value")) for r in item.get("attributes", []))
    if (item.get("variant_of") or "") != part.get("template", "") or actual != expected:
        return "variant_mismatch"
    return None


def metadata(doc):
    try:
        value = json.loads(doc.get(FIELD) or "null")
        if (not isinstance(value, dict) or value.get("schema") != 1
                or value.get("fingerprint") != digest(value["spec"])):
            return None
        return value
    except (ValueError, KeyError, TypeError):
        return None


def bom_matches(doc, meta):
    """Check the actual ERP rows, not just the stored source hash."""
    try:
        desired = meta["spec"]
        actual = sorted((r["item_code"], float(r["qty"]), r["uom"], r.get("bom_no") or "",
                         int(r.get("do_not_explode", 0)))
                        for r in doc["items"])
        expected = sorted((r["part"], float(r["qty"]), r["uom"], meta["child_boms"].get(r["part"], ""),
                           0 if r["part"] in meta["child_boms"] else 1)
                          for r in desired["components"])
        return (doc["item"] == desired["part"] and doc["company"] == COMPANY
                and float(doc["quantity"]) == 1 and doc.get("uom") == desired["uom"]
                and not doc.get("scrap_items") and not doc.get("process_loss_percentage")
                and doc.get("is_active") and doc["docstatus"] in (0, 1) and actual == expected)
    except (KeyError, TypeError, ValueError):
        return False


def plan_sync(api, model):
    actions, issues, observed = [], [], {}
    if not optional_get(api, "Custom Field", "BOM-" + FIELD):
        issues.append({"reason": "setup_required", "detail": "Run cad-setup with the approved catalog"})
    for code in model["order"]:
        part = model["parts"][code]
        item = optional_get(api, "Item", code)
        reason = item_issue(item, part)
        if reason:
            issues.append({"part": code, "reason": reason})
        # Include stock/configuration state in the approval, without retaining private Item fields.
        observed[code] = {"item": {k: item.get(k) for k in (
            "item_code", "stock_uom", "disabled", "has_variants", "is_stock_item", "variant_of", "attributes")}
            if item else None, "boms": []}
        if not part["components"]:
            continue
        documents = [api.get("BOM", row["name"]) for row in api.list(
            "BOM", {"item": code, "docstatus": ["!=", 2]})]
        matching, previous = [], []
        for doc in documents:
            observed[code]["boms"].append({k: doc.get(k) for k in (
                "name", "modified", "item", "company", "quantity", "uom", "is_active", "docstatus", "items", FIELD)})
            meta = metadata(doc)
            if meta is None or not bom_matches(doc, meta):
                issues.append({"part": code, "bom": doc["name"], "reason": "unmanaged_or_modified_bom"})
                continue
            previous.append(meta["spec"])
            if meta["spec"]["revision"] == part["revision"]:
                matching.append(doc)
        desired = spec(model, code)
        if len(matching) > 1:
            issues.append({"part": code, "reason": "duplicate_revision"})
        if matching:
            doc = matching[0]
            meta = metadata(doc)
            if meta["spec"] != desired:
                issues.append({"part": code, "reason": "changed_without_revision",
                               "stored": meta["spec"], "incoming": desired})
            # A parent must pin the exact child revision, not an ERP default or a different revision.
            for row in desired["components"]:
                child = model["parts"][row["part"]]
                linked = meta["child_boms"].get(row["part"])
                if child["components"]:
                    child_actions = [a for a in actions if a["part"] == row["part"]]
                    if not child_actions or child_actions[0].get("bom") != linked:
                        issues.append({"part": code, "reason": "child_revision_mismatch", "child": row["part"]})
                elif linked:
                    issues.append({"part": code, "reason": "unexpected_child_bom", "child": row["part"]})
            actions.append({"part": code, "revision": part["revision"], "action": "unchanged" if doc["docstatus"] == 1 else "resume_draft",
                            "bom": doc["name"]})
        else:
            actions.append({"part": code, "revision": part["revision"],
                            "action": "new_revision" if previous else "create", "incoming": desired,
                            "previous": previous})
    report = {"actions": actions, "issues": issues, "reusable_parts": reusable_parts(model)}
    report["approval"] = digest({"report": report, "observed": observed,
                                 "parts": model["parts"], "roots": model["roots"]})
    return report


def apply_sync(api, model, approval):
    # Read again at application time so a reviewed plan cannot silently become stale.
    plan = plan_sync(api, model)
    if plan["issues"]:
        raise ValueError("CAD sync blocked: resolve every issue in the plan before applying")
    if not approval or approval != plan["approval"]:
        raise ValueError("Approval does not match current CAD/ERP state; review a fresh cad-plan")
    linked, results = {}, []
    for action in plan["actions"]:
        code = action["part"]
        if action["action"] in ("unchanged", "resume_draft"):
            doc = api.get("BOM", action["bom"])
        else:
            desired = spec(model, code)
            children = {r["part"]: linked[r["part"]] for r in desired["components"] if r["part"] in linked}
            meta = {"schema": 1, "spec": desired, "fingerprint": digest(desired), "child_boms": children}
            doc = api.create("BOM", item=code, company=COMPANY, quantity=1, is_active=1, is_default=0,
                             currency="CAD", rm_cost_as_per="Valuation Rate", **{FIELD: json.dumps(meta, sort_keys=True)},
                             items=[{"item_code": r["part"], "qty": r["qty"], "uom": r["uom"],
                                     "bom_no": children.get(r["part"]), "do_not_explode": 0 if r["part"] in children else 1,
                                     "rate": 0} for r in desired["components"]])
        if doc["docstatus"] == 0:
            doc = api.submit(doc)
        if not bom_matches(doc, metadata(doc)):
            raise RuntimeError("ERP returned an unexpected BOM; inspect partial sync before retrying")
        linked[code] = doc["name"]
        results.append({**{k: action[k] for k in ("part", "revision", "action")}, "bom": doc["name"]})
    return {"boms": results, "reusable_parts": plan["reusable_parts"]}
