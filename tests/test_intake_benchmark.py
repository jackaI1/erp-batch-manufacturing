import unittest
from erp_demo.intake_benchmark import run


class ParallelReportTests(unittest.TestCase):
    def test_reference_comparison_counts_omissions_and_holds(self):
        report = run()
        self.assertEqual(report['orders'], 8)
        self.assertEqual(report['held_orders'], 5)
        self.assertEqual(report['accepted_field_accuracy'], 1.0)
        self.assertLess(report['raw_field_accuracy'], 1)
        self.assertTrue(all(row['seconds'] >= 0 for row in report['rows']))
        self.assertEqual(report['status_accuracy'], 1)
