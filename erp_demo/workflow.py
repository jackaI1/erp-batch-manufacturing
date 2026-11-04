"""Business actions go through REST; ERPNext owns every stock/accounting entry."""
import math
import re
from datetime import date, timedelta

COMPANY = "Demo Compounding"
ABBR = "DCP"
RAW = "Raw Materials - DCP"
WIP = "Work In Progress - DCP"
FINISHED = "Finished Goods - DCP"
RAW_ITEMS = {
    "RM-WATER": ("Purified water", 1),
    "RM-GLYCERIN": ("Glycerin", 5),
    "RM-OIL": ("Sunflower oil", 8),
    "RM-EMULSIFIER": ("Emulsifying wax", 12),
    "RM-PRESERVATIVE": ("Preservative blend", 20),
    "RM-WAX": ("Beeswax", 15),
}
FORMULAS = {
    "lotion": {"item": "FG-LOTION", "name": "Demo lotion", "ingredients": {
        "RM-WATER": 7, "RM-GLYCERIN": 0.5, "RM-OIL": 2,
        "RM-EMULSIFIER": 0.4, "RM-PRESERVATIVE": 0.1}},
    "balm": {"item": "FG-BALM", "name": "Demo balm", "ingredients": {
        "RM-OIL": 8, "RM-WAX": 2}},
}


def positive_number(value, allow_zero=False):
    number = float(value)
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        raise ValueError("Quantity must be finite and " + ("nonnegative" if allow_zero else "positive"))
    return number


def seed(api):
    """Create missing master data and one opening receipt; never replenish on rerun."""
    api.ensure("Company", COMPANY, company_name=COMPANY, abbr=ABBR,
                         default_currency="CAD", country="Canada",
                         chart_of_accounts="Standard", create_chart_of_accounts_based_on="Standard Template")
    api.update("Stock Settings", "Stock Settings", use_serial_batch_fields=1)
    api.update("Manufacturing Settings", "Manufacturing Settings", make_serial_no_batch_from_work_order=0)
    for name in ("Raw Materials", "Work In Progress", "Finished Goods"):
        api.ensure("Warehouse", f"{name} - {ABBR}", warehouse_name=name, company=COMPANY,
                   parent_warehouse=f"All Warehouses - {ABBR}")
    for name in ("pH", "Appearance"):
        api.ensure("Quality Inspection Parameter", name, parameter=name)
    api.ensure("Quality Inspection Template", "Demo lotion release", quality_inspection_template_name="Demo lotion release",
               item_quality_inspection_parameter=[
                   {"specification": "pH", "numeric": 1, "min_value": 5, "max_value": 6},
                   {"specification": "Appearance", "value": "Uniform"}])
    api.ensure("Quality Inspection Template", "Demo balm release", quality_inspection_template_name="Demo balm release",
               item_quality_inspection_parameter=[{"specification": "Appearance", "value": "Uniform"}])
    for code, (name, rate) in RAW_ITEMS.items():
        api.ensure("Item", code, item_code=code, item_name=name, item_group="Raw Material",
                   stock_uom="Kg", is_stock_item=1, has_batch_no=1, create_new_batch=0,
                   valuation_rate=rate)
    for formula in FORMULAS.values():
        api.ensure("Item", formula["item"], item_code=formula["item"], item_name=formula["name"],
                   item_group="Products", stock_uom="Kg", is_stock_item=1, has_batch_no=1,
                   create_new_batch=0, quality_inspection_template=formula["name"] + " release")
    batches = []
    for code in RAW_ITEMS:
        for suffix in ("A", "B"):
            batch = f"{code}-{suffix}"
            api.ensure("Batch", batch, batch_id=batch, item=code,
                       manufacturing_date=date.today().isoformat(),
                       expiry_date=(date.today() + timedelta(days=365)).isoformat(),
                       description="Generated demo supplier lot; no real supplier data")
            batches.append(batch)
    receipts = api.list("Stock Entry", {"remarks": "demo-opening-stock-v1", "docstatus": ["!=", 2]})
    if not receipts:
        receipt = api.create("Stock Entry", stock_entry_type="Material Receipt", company=COMPANY,
                             remarks="demo-opening-stock-v1", items=[
                                 {"item_code": code, "qty": 100, "uom": "Kg", "conversion_factor": 1,
                                  "t_warehouse": RAW, "batch_no": f"{code}-{suffix}",
                                  "use_serial_batch_fields": 1, "basic_rate": rate}
                                 for code, (_, rate) in RAW_ITEMS.items() for suffix in ("A", "B")])
    else:
        receipt = api.get("Stock Entry", receipts[0]["name"])
    if receipt["docstatus"] == 0:
        receipt = api.submit(receipt)
    boms = {}
    for key, formula in FORMULAS.items():
        found = api.list("BOM", {"item": formula["item"], "docstatus": ["!=", 2]})
        bom = api.get("BOM", found[0]["name"]) if found else api.create(
            "BOM", item=formula["item"], company=COMPANY, quantity=10, is_active=1, is_default=1,
            currency="CAD", rm_cost_as_per="Valuation Rate", items=[
                {"item_code": code, "qty": qty, "uom": "Kg", "rate": RAW_ITEMS[code][1]}
                for code, qty in formula["ingredients"].items()])
        if bom["docstatus"] == 0:
            bom = api.submit(bom)
        boms[key] = bom["name"]
    return {"boms": boms, "batches": batches, "opening_receipt": receipt["name"]}


def manufacture(api, formula, kg, lot, run_id):
    kg = positive_number(kg)
    if formula not in FORMULAS or lot not in ("A", "B"):
        raise ValueError("Choose lotion/balm and raw lot A/B")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,40}", run_id):
        raise ValueError("Run ID must be 1-40 letters, numbers or hyphens")
    recipe = FORMULAS[formula]
    batch = f"{recipe['item']}-{run_id}"
    if api.list("Batch", {"name": batch}):
        raise ValueError("Run ID already exists; inspect its documents before starting another run")
    bom = api.list("BOM", {"item": recipe["item"], "docstatus": 1, "is_active": 1, "is_default": 1})
    if len(bom) != 1:
        raise ValueError("Run seed first; exactly one default BOM is required")
    wo = api.create("Work Order", company=COMPANY, production_item=recipe["item"],
                    bom_no=bom[0]["name"], qty=kg, source_warehouse=RAW, wip_warehouse=WIP,
                    fg_warehouse=FINISHED, skip_transfer=1, from_wip_warehouse=0,
                    use_multi_level_bom=0)
    wo = api.submit(wo)
    api.create("Batch", batch_id=batch, item=recipe["item"],
               manufacturing_date=date.today().isoformat(),
               reference_doctype="Work Order", reference_name=wo["name"])
    entry = api.call("erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry",
                     {"work_order_id": wo["name"], "purpose": "Manufacture", "qty": kg})
    for row in entry["items"]:
        row["serial_and_batch_bundle"] = None
        row["use_serial_batch_fields"] = 1
        row["batch_no"] = batch if row.get("is_finished_item") else f"{row['item_code']}-{lot}"
    # Generated docs may include local/unsaved names; let REST assign durable names.
    for key in ("name", "doctype", "__islocal", "__unsaved"):
        entry.pop(key, None)
    entry = api.create("Stock Entry", **entry)
    template = api.get("Quality Inspection Template", f"Demo {formula} release")
    readings = []
    for parameter in template["item_quality_inspection_parameter"]:
        reading = {k: parameter[k] for k in ("specification", "numeric", "min_value", "max_value", "value") if k in parameter}
        if parameter["specification"] == "pH":
            reading["reading_1"] = "5.5"
        else:
            reading["reading_value"] = "Uniform"
        readings.append(reading)
    qi = api.create("Quality Inspection", inspection_type="In Process", reference_type="Stock Entry",
                    reference_name=entry["name"], item_code=recipe["item"], batch_no=batch,
                    sample_size=1, inspected_by="Administrator", quality_inspection_template=template["name"],
                    readings=readings)
    qi = api.submit(qi)
    if qi["status"] != "Accepted":
        raise ValueError("Quality inspection rejected; leave manufacturing entry in draft")
    for row in entry["items"]:
        if row.get("is_finished_item"):
            row["quality_inspection"] = qi["name"]
    entry = api.update("Stock Entry", entry["name"], items=entry["items"])
    entry = api.submit(entry)
    return {"work_order": wo["name"], "stock_entry": entry["name"], "batch": batch,
            "inspection": qi["name"]}


def batch_balance(api, batch):
    """Sum signed quantities from submitted stock ledger batch bundles."""
    item = api.get("Batch", batch)["item"]
    total = 0.0
    for ledger in api.list("Stock Ledger Entry", {"item_code": item, "is_cancelled": 0},
                           ["name", "actual_qty", "batch_no", "serial_and_batch_bundle"]):
        bundle = ledger.get("serial_and_batch_bundle")
        if bundle:
            doc = api.get("Serial and Batch Bundle", bundle)
            if doc["docstatus"] == 1:
                total += sum(float(e["qty"]) for e in doc["entries"] if e.get("batch_no") == batch)
        elif ledger.get("batch_no") == batch:
            total += float(ledger["actual_qty"])
    return round(total, 6)


def entry_lots(api, entry):
    lots = []
    for row in entry["items"]:
        bundle = row.get("serial_and_batch_bundle")
        if not bundle:
            raise ValueError(f"Missing ERPNext batch bundle on {entry['name']}")
        doc = api.get("Serial and Batch Bundle", bundle)
        if doc["docstatus"] != 1:
            raise ValueError("Traceability requires submitted batch bundles")
        for lot in doc["entries"]:
            lots.append({"batch": lot["batch_no"], "item": row["item_code"],
                         "kg": abs(float(lot["qty"])), "finished": bool(row.get("is_finished_item"))})
    return lots


def recall(api, ingredient_batch):
    """One-stage compounding recall, derived from posted manufacture bundles."""
    api.get("Batch", ingredient_batch)
    result = []
    for ref in api.list("Stock Entry", {"company": COMPANY, "purpose": "Manufacture", "docstatus": 1}):
        entry = api.get("Stock Entry", ref["name"])
        lots = entry_lots(api, entry)
        consumed = sum(lot["kg"] for lot in lots if not lot["finished"] and lot["batch"] == ingredient_batch)
        if consumed:
            for output in (lot for lot in lots if lot["finished"]):
                result.append({"ingredient_batch": ingredient_batch, "consumed_kg": consumed,
                               "finished_batch": output["batch"], "produced_kg": output["kg"],
                               "work_order": entry["work_order"], "stock_entry": entry["name"]})
    return sorted(result, key=lambda row: row["finished_batch"])


def reconcile(api, batch, counted_kg):
    count = positive_number(counted_kg, allow_zero=True)
    item = api.get("Batch", batch)["item"]
    if item not in RAW_ITEMS:
        raise ValueError("This count command is for raw material lots in Raw Materials")
    before = batch_balance(api, batch)
    if math.isclose(before, count, abs_tol=1e-6):
        raise ValueError("Count matches the ledger; no reconciliation is needed")
    company = api.get("Company", COMPANY)
    doc = api.create("Stock Reconciliation", company=COMPANY, purpose="Stock Reconciliation",
                     expense_account=company["stock_adjustment_account"],
                     cost_center=company["cost_center"],
                     items=[{"item_code": item, "warehouse": RAW, "batch_no": batch,
                             "use_serial_batch_fields": 1, "qty": count,
                             "valuation_rate": RAW_ITEMS[item][1]}])
    doc = api.submit(doc)
    return {"document": doc["name"], "batch": batch, "before_kg": before,
            "counted_kg": count, "difference_kg": round(count - before, 6)}
