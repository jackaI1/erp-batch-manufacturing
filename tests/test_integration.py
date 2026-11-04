"""Real ERPNext contract: intentionally opt in to a disposable demo site."""
import os
import unittest
from uuid import uuid4


@unittest.skipUnless(os.getenv("ERP_INTEGRATION") == "1", "requires real ERPNext")
class ManufacturingIntegration(unittest.TestCase):
    def test_manufacture_recall_inspection_and_count(self):
        from erp_demo.api import Client
        from erp_demo.workflow import seed, manufacture, recall, batch_balance, reconcile, trace

        from erp_demo.api import ERPError
        api = Client.from_env()
        request = api.request
        def diagnostic_request(method, path, *args, **kwargs):
            try:
                return request(method, path, *args, **kwargs)
            except ERPError as error:
                # This opt-in test uses only synthetic demo records. Normal CLI errors stay private.
                if error.status != 404:
                    print(f"Synthetic-site diagnostic: {method} {path}: {error.server_messages}", flush=True)
                raise
        api.request = diagnostic_request
        first = seed(api)
        second = seed(api)
        self.assertEqual(first, second)
        self.assertEqual(len(first["boms"]), 2)
        self.assertEqual(len(first["batches"]), 12)
        receipt = api.get("Stock Entry", first["opening_receipt"])
        receipt_timestamp = receipt["posting_date"] + " " + receipt["posting_time"]
        self.assertAlmostEqual(batch_balance(api, "RM-OIL-A"), 100)
        run = uuid4().hex[:8]
        # A rejected sample must leave its manufacturing entry unposted.
        with self.assertRaisesRegex(ValueError, "Quality inspection rejected"):
            manufacture(api, "lotion", 1, "A", f"{run}-rejected", ph=7.2)
        self.assertAlmostEqual(batch_balance(api, "RM-OIL-A"), 100)
        rejected = api.list("Quality Inspection", {"batch_no": f"FG-LOTION-{run}-rejected"}, ["status", "reference_name"])
        self.assertEqual(rejected[0]["status"], "Rejected")
        rejected_entry = api.get("Stock Entry", rejected[0]["reference_name"])
        self.assertEqual(rejected_entry["docstatus"], 0)
        # The ERP must enforce the same release rule even when bypassing our CLI.
        with self.assertRaises(ERPError):
            api.submit(rejected_entry)
        self.assertAlmostEqual(batch_balance(api, "RM-OIL-A"), 100)
        lotion = manufacture(api, "lotion", 10, "A", f"{run}-lotion")
        balm = manufacture(api, "balm", 5, "A", f"{run}-balm-a")
        other = manufacture(api, "balm", 5, "B", f"{run}-balm-b")
        for result, qty in [(lotion, 10), (balm, 5), (other, 5)]:
            wo = api.get("Work Order", result["work_order"])
            self.assertEqual(wo["status"], "Completed")
            self.assertAlmostEqual(wo["produced_qty"], qty)
            movement = api.get("Stock Entry", result["stock_entry"])
            self.assertEqual(movement["docstatus"], 1)
            self.assertGreaterEqual(movement["posting_date"] + " " + movement["posting_time"], receipt_timestamp)
            qi = api.get("Quality Inspection", result["inspection"])
            self.assertEqual(qi["docstatus"], 1)
            self.assertEqual(qi["status"], "Accepted")
            self.assertEqual(qi["batch_no"], result["batch"])
            self.assertAlmostEqual(batch_balance(api, result["batch"]), qty)
        ancestry = trace(api, lotion["batch"])
        self.assertEqual(len(ancestry), 1)
        self.assertEqual({row["batch"]: row["kg"] for row in ancestry[0]["ingredients"]}, {
            "RM-WATER-A": 7, "RM-GLYCERIN-A": 0.5, "RM-OIL-A": 2,
            "RM-EMULSIFIER-A": 0.4, "RM-PRESERVATIVE-A": 0.1})
        report = recall(api, "RM-OIL-A")
        self.assertEqual({r["finished_batch"] for r in report}, {lotion["batch"], balm["batch"]})
        self.assertEqual({r["consumed_kg"] for r in report}, {2.0, 4.0})
        self.assertNotIn(other["batch"], {r["finished_batch"] for r in report})
        self.assertEqual(recall(api, "RM-WAX-A")[0]["finished_batch"], balm["batch"])
        self.assertAlmostEqual(batch_balance(api, "RM-OIL-A"), 94)
        for batch, expected in {"RM-WATER-A": 93, "RM-GLYCERIN-A": 99.5,
                                "RM-EMULSIFIER-A": 99.6, "RM-PRESERVATIVE-A": 99.9,
                                "RM-WAX-A": 99, "RM-WAX-B": 99}.items():
            self.assertAlmostEqual(batch_balance(api, batch), expected)
        # Running setup again must not reset the stock already consumed.
        self.assertEqual(seed(api), first)
        self.assertAlmostEqual(batch_balance(api, "RM-OIL-A"), 94)
        adjustment = reconcile(api, "RM-OIL-A", 93.75)
        self.assertAlmostEqual(adjustment["difference_kg"], -0.25)
        self.assertEqual(api.get("Stock Reconciliation", adjustment["document"])["docstatus"], 1)
        self.assertAlmostEqual(batch_balance(api, "RM-OIL-A"), 93.75)
        self.assertEqual(recall(api, "RM-OIL-A"), report)
        with self.assertRaises(ValueError):
            manufacture(api, "lotion", 0, "A", "bad")
        with self.assertRaises(ValueError):
            reconcile(api, "RM-OIL-A", -1)
        # ERPNext itself must reject a valid request larger than available lot stock.
        from erp_demo.api import ERPError
        with self.assertRaises(ERPError):
            manufacture(api, "balm", 1000, "B", f"{run}-shortage")
        self.assertAlmostEqual(batch_balance(api, "RM-OIL-B"), 96)
        print("ERPNext verified: 3 completed work orders, 20 kg, 12 raw lots, selective recall, -0.25 kg count")
