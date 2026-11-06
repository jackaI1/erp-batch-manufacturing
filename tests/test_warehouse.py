"""Warehouse calculations are independent of a running server."""
import csv
import io
import unittest

from erp_demo import warehouse


class WarehouseMathTests(unittest.TestCase):
    def test_ledger_excludes_cancelled_and_tracks_both_transfer_legs(self):
        rows = [
            {"item_code": "DEMO", "warehouse": "A", "actual_qty": 100},
            {"item_code": "DEMO", "warehouse": "A", "actual_qty": -20},
            {"item_code": "DEMO", "warehouse": "B", "actual_qty": 20},
            {"item_code": "DEMO", "warehouse": "A", "actual_qty": 4, "is_cancelled": 1},
        ]
        self.assertEqual(warehouse.ledger_balances(rows), {("DEMO", "A"): 80, ("DEMO", "B"): 20})

    def test_kpis_count_variance_capacity_and_measured_duration(self):
        balances = {("DEMO", "A"): 80, ("DEMO", "B"): 20}
        counts = {("DEMO", "A"): 79, ("DEMO", "B"): 20}
        result = warehouse.kpis(balances, counts, {"A": 100, "B": 50}, [90, 30])
        self.assertEqual(result["stock_accuracy_pct"], 99)
        self.assertEqual(result["mean_pick_seconds"], 60)
        self.assertAlmostEqual(result["bin_utilization_pct"], 100 / 150 * 100)
        self.assertEqual(result["counted_positions"], 2)

    def test_empty_counts_are_not_perfect_accuracy(self):
        result = warehouse.kpis({}, {}, {"A": 10}, [])
        self.assertIsNone(result["stock_accuracy_pct"])
        self.assertIsNone(result["mean_pick_seconds"])

    def test_zero_ledger_with_positive_count_is_zero_accuracy(self):
        self.assertEqual(warehouse.kpis({("X", "A"): 0}, {("X", "A"): 1}, {"A": 10}, [])[
            "stock_accuracy_pct"], 0)

    def test_invalid_measurements_fail_before_export(self):
        for balances, counts, caps, durations in [
            ({("X", "A"): float("nan")}, {}, {"A": 1}, []),
            ({}, {("X", "A"): -1}, {"A": 1}, []),
            ({}, {}, {"A": 0}, []),
            ({}, {}, {"A": 1}, [-1]),
            ({}, {}, {"A": 1}, [float("inf")]),
        ]:
            with self.subTest(balances=balances, counts=counts, caps=caps):
                with self.assertRaises(ValueError):
                    warehouse.kpis(balances, counts, caps, durations)

    def test_counts_cannot_silently_include_unknown_positions(self):
        with self.assertRaises(ValueError):
            warehouse.kpis({("X", "A"): 10}, {("X", "B"): 10}, {"A": 20}, [])

    def test_csv_is_flat_and_contains_units_and_synthetic_label(self):
        report = {"stock_accuracy_pct": 99, "mean_pick_seconds": 60, "bin_utilization_pct": 50,
                  "counted_positions": 2}
        output = io.StringIO()
        warehouse.write_kpis(output, report)
        rows = list(csv.DictReader(io.StringIO(output.getvalue())))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["dataset"], "synthetic")
        self.assertEqual({r["unit"] for r in rows}, {"percent", "seconds", "positions"})
        self.assertEqual(next(r for r in rows if r["metric"] == "mean_pick_seconds")["basis"],
                         "scripted API workflow")

    def test_timestamps_require_timezone_and_chronological_order(self):
        self.assertEqual(warehouse.pick_seconds("2026-11-06T12:00:00+00:00", "2026-11-06T12:01:30+00:00"), 90)
        for start, end in [("2026-11-06T12:00:00", "2026-11-06T12:01:30"),
                           ("2026-11-06T12:01:30Z", "2026-11-06T12:00:00Z")]:
            with self.assertRaises(ValueError):
                warehouse.pick_seconds(start, end)

    def test_putaway_identifies_native_stock_entry_detail_rows(self):
        class NativeContract:
            def call(inner, method, args):
                self.assertEqual(method, warehouse.PUTAWAY_METHOD)
                self.assertEqual(args["items"][0].get("doctype"), "Stock Entry Detail")
                return [dict(args["items"][0], qty=80, transfer_qty=80, t_warehouse=warehouse.BIN_A),
                        dict(args["items"][0], qty=40, transfer_qty=40, t_warehouse=warehouse.BIN_B)]
            def create(inner, doctype, **fields):
                self.assertEqual(doctype, "Stock Entry")
                self.assertEqual([row["t_warehouse"] for row in fields["items"]],
                                 [warehouse.BIN_A, warehouse.BIN_B])
                return dict(fields, name="SYNTHETIC-PUTAWAY", docstatus=0)
            def submit(inner, doc):
                return dict(doc, docstatus=1)
        result = warehouse.putaway(NativeContract())
        self.assertEqual(result["docstatus"], 1)

    def test_overflow_is_rejected_before_creating_transfer(self):
        class Full:
            def call(self, method, args):
                return [{"item_code": warehouse.ITEM, "qty": 3, "t_warehouse": warehouse.BIN_A}]
            def create(self, *args, **kwargs):
                raise AssertionError("must not write partial allocation")
        with self.assertRaisesRegex(ValueError, "allocate"):
            warehouse.putaway(Full(), 10)

    def test_transfer_rejects_equal_warehouses_and_bad_quantities(self):
        for quantity, source, target in [(0, "A", "B"), (1, "A", "A"), (float("nan"), "A", "B")]:
            with self.assertRaises(ValueError):
                warehouse.transfer(None, quantity, source, target)
