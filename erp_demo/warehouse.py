"""Synthetic warehouse operations, posted through ERPNext's REST API.

A leaf Warehouse represents a physical storage bin. ERPNext's Bin document is
an item/warehouse balance, not a named shelf. All quantities here use kg.
"""
import argparse
import csv
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

from .workflow import ABBR, COMPANY, positive_number

ITEM = "WH-DEMO-BASE"
LOT = "WH-DEMO-BASE-LOT-A"
STAGING = "WH Receiving - DCP"
STORES = "WH Stores - DCP"
BIN_A = "WH A-01 - DCP"
BIN_B = "WH B-01 - DCP"
DISPATCH = "WH Dispatch - DCP"
CAPACITIES = {BIN_A: 80.0, BIN_B: 60.0}
PUTAWAY_METHOD = "erpnext.stock.doctype.putaway_rule.putaway_rule.apply_putaway_rule"
PICK_METHOD = "erpnext.stock.doctype.pick_list.pick_list.create_stock_entry"


def seed(api):
    """Add dedicated stock without changing the manufacturing ingredients."""
    api.ensure("Stock Entry Type", "Material Transfer", name="Material Transfer",
               purpose="Material Transfer", is_standard=1)
    for name, group, parent in [("WH Receiving", 0, f"All Warehouses - {ABBR}"),
                                ("WH Stores", 1, f"All Warehouses - {ABBR}"),
                                ("WH A-01", 0, STORES), ("WH B-01", 0, STORES),
                                ("WH Dispatch", 0, f"All Warehouses - {ABBR}")]:
        api.ensure("Warehouse", f"{name} - {ABBR}", warehouse_name=name,
                   company=COMPANY, is_group=group, parent_warehouse=parent)
    api.ensure("Item", ITEM, item_code=ITEM, item_name="Synthetic warehouse practice base",
               item_group="Raw Material", stock_uom="Kg", is_stock_item=1,
               has_batch_no=1, create_new_batch=0, valuation_rate=2)
    api.ensure("Batch", LOT, batch_id=LOT, item=ITEM, manufacturing_date=date.today().isoformat(),
               description="Synthetic warehouse training lot; not a real material")
    rules = []
    for priority, (warehouse, capacity) in enumerate(CAPACITIES.items(), 1):
        found = api.list("Putaway Rule", {"item_code": ITEM, "warehouse": warehouse})
        rule = (api.get("Putaway Rule", found[0]["name"]) if found else api.create(
            "Putaway Rule", item_code=ITEM, warehouse=warehouse, company=COMPANY,
            capacity=capacity, priority=priority, uom="Kg", conversion_factor=1))
        if float(rule["capacity"]) != capacity or rule["disable"]:
            raise ValueError("Warehouse putaway rules changed; inspect before rerunning")
        rules.append(rule["name"])
    found = api.list("Stock Entry", {"remarks": "warehouse-opening-v1", "docstatus": ["!=", 2]})
    receipt = (api.get("Stock Entry", found[0]["name"]) if found else api.create(
        "Stock Entry", stock_entry_type="Material Receipt", company=COMPANY,
        remarks="warehouse-opening-v1", items=[dict(_line(120), t_warehouse=STAGING, basic_rate=2)]))
    if receipt["docstatus"] == 0:
        receipt = api.submit(receipt)
    return {"receipt": receipt["name"], "putaway_rules": rules}


def _line(quantity):
    return {"item_code": ITEM, "qty": quantity, "transfer_qty": quantity,
            "uom": "Kg", "stock_uom": "Kg", "conversion_factor": 1,
            "batch_no": LOT, "use_serial_batch_fields": 1}


def _save_stock_entry(api, payload):
    """A failed POST is not retried. Inspect ERP drafts before restarting."""
    fields = {key: value for key, value in payload.items() if key not in (
        "doctype", "name", "owner", "creation", "modified", "modified_by", "docstatus", "__islocal")}
    entry = api.create("Stock Entry", **fields)
    return api.submit(entry)


def transfer(api, quantity, source, target):
    quantity = positive_number(quantity)
    if not source or not target or source == target:
        raise ValueError("Transfer needs two different warehouses")
    return _save_stock_entry(api, {"stock_entry_type": "Material Transfer", "company": COMPANY,
                                  "items": [dict(_line(quantity), s_warehouse=source, t_warehouse=target)]})


def putaway(api, quantity=120):
    """Ask ERPNext to split a receiving transfer using its native capacity rules."""
    quantity = positive_number(quantity)
    items = api.call(PUTAWAY_METHOD, {"doctype": "Stock Entry", "company": COMPANY,
                    "purpose": "Material Transfer", "sync": "true",
                    "items": [dict(_line(quantity), doctype="Stock Entry Detail", s_warehouse=STAGING)]})
    if not items or not math.isclose(sum(float(row["qty"]) for row in items), quantity) or any(
            row.get("t_warehouse") not in CAPACITIES for row in items):
        raise ValueError("Putaway rules cannot allocate the complete quantity; no stock was moved")
    return _save_stock_entry(api, {"stock_entry_type": "Material Transfer", "company": COMPANY,
                                  "remarks": "warehouse-putaway-v1", "items": items})


def pick(api, quantity=10, source=BIN_A):
    """Submit a real pick list and use ERPNext's mapper to create its transfer."""
    quantity = positive_number(quantity)
    if source not in CAPACITIES:
        raise ValueError("Choose a configured storage bin")
    started = datetime.now(timezone.utc).isoformat()
    document = api.create("Pick List", company=COMPANY, purpose="Material Transfer",
                          naming_series="STO-PICK-.YYYY.-", parent_warehouse=STORES,
                          pick_manually=1, scan_mode=1, locations=[dict(
                              _line(quantity), warehouse=source, stock_qty=quantity, picked_qty=quantity)])
    document = api.submit(document)
    payload = api.call(PICK_METHOD, {"pick_list": document})
    if not payload or not payload.get("items"):
        raise ValueError("ERPNext returned no items for the pick list")
    for row in payload["items"]:
        row["t_warehouse"] = DISPATCH
        row["use_serial_batch_fields"] = 1
        # The mapper returns a new Stock Entry row. Its identity must not be
        # confused with the submitted Pick List Item identity in pick_list_item.
        row.pop("name", None)
    payload["remarks"] = "warehouse-pick-v1"
    entry = _save_stock_entry(api, payload)
    completed = datetime.now(timezone.utc).isoformat()
    return {"pick_list": document["name"], "stock_entry": entry["name"],
            "started_at": started, "completed_at": completed,
            "seconds": pick_seconds(started, completed), "timing_basis": "scripted API workflow"}


def ledger_balances(rows):
    balances = {}
    for row in rows:
        if row.get("is_cancelled"):
            continue
        key = (row["item_code"], row["warehouse"])
        qty = float(row["actual_qty"])
        if not math.isfinite(qty):
            raise ValueError("Ledger contains a non-finite quantity")
        balances[key] = balances.get(key, 0.0) + qty
    return balances


def pick_seconds(started_at, completed_at):
    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    finish = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    if start.tzinfo is None or finish.tzinfo is None or finish < start:
        raise ValueError("Pick timestamps must have timezones and finish after start")
    return (finish - start).total_seconds()


def kpis(balances, counts, capacities, durations):
    """Accuracy is weighted absolute count variance; absent counts stay unknown.

    Capacity covers storage bins only. Receiving and dispatch are staging areas.
    A count must include every expected position to represent a full stocktake;
    counted_positions records coverage, allowing partial counts without hiding it.
    """
    balances = {key: float(value) for key, value in balances.items()}
    counts = {key: positive_number(value, allow_zero=True) for key, value in counts.items()}
    capacities = {key: positive_number(value) for key, value in capacities.items()}
    durations = [positive_number(value, allow_zero=True) for value in durations]
    if not all(math.isfinite(value) and value >= -1e-8 for value in balances.values()):
        raise ValueError("Ledger balances must be finite and nonnegative")
    if not set(counts).issubset(balances):
        raise ValueError("Count refers to a position absent from the ledger; inspect it first")
    if counts:
        recorded = sum(balances[key] for key in counts)
        error = sum(abs(balances[key] - count) for key, count in counts.items())
        accuracy = max(0.0, 100 * (1 - error / recorded)) if recorded else (100.0 if error == 0 else 0.0)
    else:
        accuracy = None
    stored = sum(quantity for (_, warehouse), quantity in balances.items() if warehouse in capacities)
    return {"stock_accuracy_pct": accuracy, "counted_positions": len(counts),
            "mean_pick_seconds": sum(durations) / len(durations) if durations else None,
            "bin_utilization_pct": 100 * stored / sum(capacities.values()) if capacities else None}


def write_kpis(output, report):
    writer = csv.DictWriter(output, fieldnames=["dataset", "metric", "value", "unit", "basis"])
    writer.writeheader()
    for key, value in report.items():
        unit = "percent" if key.endswith("_pct") else ("seconds" if key.endswith("_seconds") else "positions")
        basis = {"stock_accuracy_pct": "synthetic count versus posted ledger",
                 "bin_utilization_pct": "posted ledger versus configured storage capacity",
                 "mean_pick_seconds": "scripted API workflow", "counted_positions": "count coverage"}[key]
        writer.writerow({"dataset": "synthetic", "metric": key, "value": value, "unit": unit, "basis": basis})


def report(api, counts, durations):
    rows = api.list("Stock Ledger Entry", {"item_code": ITEM, "is_cancelled": 0},
                    ["item_code", "warehouse", "actual_qty", "is_cancelled"])
    balances = ledger_balances(rows)
    return {"balances_kg": [{"item": key[0], "warehouse": key[1], "kg": value}
                            for key, value in sorted(balances.items())],
            "kpis": kpis(balances, counts, CAPACITIES, durations)}


def demo(api, output):
    """Run once per disposable site; reruns refuse to duplicate an uncertain pick."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    seed(api)
    if api.list("Stock Entry", {"remarks": ["in", ["warehouse-putaway-v1", "warehouse-pick-v1"]],
                               "docstatus": ["!=", 2]}):
        raise ValueError("Warehouse run already started; inspect existing drafts and ledger before repeating")
    allocation = putaway(api)
    picked = pick(api)
    counts = {(ITEM, STAGING): 0, (ITEM, BIN_A): 69, (ITEM, BIN_B): 40, (ITEM, DISPATCH): 10}
    result = report(api, counts, [picked["seconds"]])
    result.update({"dataset": "synthetic", "putaway": allocation["name"], "pick": picked,
                   "count_basis": "labelled synthetic stocktake: A-01 counted 69 kg against 70 kg recorded"})
    (output / "warehouse-report.json").write_text(json.dumps(result, indent=2) + "\n")
    with (output / "warehouse-kpis.csv").open("w", newline="") as stream:
        write_kpis(stream, result["kpis"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="artifacts")
    args = parser.parse_args()
    from .api import Client
    from .workflow import seed as workshop_seed
    api = Client.from_env()
    workshop_seed(api)
    print(json.dumps(demo(api, args.output), indent=2))


if __name__ == "__main__":
    main()
