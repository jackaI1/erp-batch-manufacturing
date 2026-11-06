"""Native ERPNext putaway, batch picking, transfer and stock-ledger contract."""
import csv
import json
import os
import unittest
from pathlib import Path


@unittest.skipUnless(os.getenv("ERP_INTEGRATION") == "1", "requires real ERPNext")
class WarehouseIntegration(unittest.TestCase):
    def test_native_putaway_pick_transfer_and_ledger_kpis(self):
        from erp_demo.api import Client, ERPError
        from erp_demo.workflow import seed as workshop_seed
        from erp_demo import warehouse as wh

        api = Client.from_env()
        original_request = api.request
        def diagnostic(method, path, *args, **kwargs):
            try:
                return original_request(method, path, *args, **kwargs)
            except ERPError as error:
                if error.status != 404:
                    print(f"Synthetic warehouse diagnostic: {method} {path}: {error.server_messages}", flush=True)
                raise
        api.request = diagnostic
        workshop_seed(api)
        first = wh.seed(api)
        self.assertEqual(wh.seed(api), first)
        self.assertEqual(len(first["putaway_rules"]), 2)
        for rule in first["putaway_rules"]:
            self.assertEqual(api.get("Putaway Rule", rule)["item_code"], wh.ITEM)
        report = wh.demo(api, "artifacts")
        self.assertEqual(api.get("Stock Entry", report["putaway"])["docstatus"], 1)
        allocation = api.get("Stock Entry", report["putaway"])
        self.assertEqual({row["t_warehouse"]: row["qty"] for row in allocation["items"]},
                         {wh.BIN_A: 80, wh.BIN_B: 40})
        self.assertTrue(all(row["batch_no"] == wh.LOT for row in allocation["items"]))
        picked = api.get("Pick List", report["pick"]["pick_list"])
        self.assertEqual(picked["docstatus"], 1)
        self.assertEqual(picked["purpose"], "Material Transfer")
        self.assertEqual(picked["locations"][0]["batch_no"], wh.LOT)
        self.assertEqual(picked["locations"][0]["picked_qty"], 10)
        movement = api.get("Stock Entry", report["pick"]["stock_entry"])
        self.assertEqual(movement["docstatus"], 1)
        self.assertEqual(movement["pick_list"], picked["name"])
        self.assertEqual(movement["items"][0]["t_warehouse"], wh.DISPATCH)
        balances = {row["warehouse"]: row["kg"] for row in report["balances_kg"]}
        self.assertEqual(balances, {wh.STAGING: 0, wh.BIN_A: 70, wh.BIN_B: 40, wh.DISPATCH: 10})
        # ERPNext maintains an item/warehouse Bin for each physical leaf warehouse.
        native_bins = api.list("Bin", {"item_code": wh.ITEM}, ["warehouse", "actual_qty"])
        self.assertEqual({row["warehouse"]: row["actual_qty"] for row in native_bins}, balances)
        self.assertAlmostEqual(report["kpis"]["stock_accuracy_pct"], 119 / 120 * 100)
        self.assertAlmostEqual(report["kpis"]["bin_utilization_pct"], 110 / 140 * 100)
        self.assertGreater(report["kpis"]["mean_pick_seconds"], 0)
        self.assertEqual(report["kpis"]["counted_positions"], 4)
        with Path("artifacts/warehouse-kpis.csv").open() as stream:
            csv_rows = list(csv.DictReader(stream))
        self.assertEqual(len(csv_rows), 4)
        self.assertTrue(all(row["dataset"] == "synthetic" for row in csv_rows))
        with self.assertRaisesRegex(ValueError, "already started"):
            wh.demo(api, "artifacts")
        with self.assertRaises(ValueError):
            wh.putaway(api, 1000)  # only 30 kg free; must not post a partial allocation
        self.assertEqual(wh.report(api, {}, [])["balances_kg"], report["balances_kg"])
        # Even when our preflight is bypassed, ERPNext enforces batch availability.
        with self.assertRaises(ERPError):
            wh.transfer(api, 1000, wh.BIN_A, wh.DISPATCH)
        self.assertEqual(wh.report(api, {}, [])["balances_kg"], report["balances_kg"])
        print("Warehouse verified: 120 kg, 4 leaf warehouses, 2 putaway rules, native pick list, 99.1667% count accuracy")
