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

    def test_live_decisions_use_document_labels_separate_from_replay_faults(self):
        import json
        from pathlib import Path
        from unittest.mock import patch
        from erp_demo.models import ReplayAdapter
        base = Path(__file__).resolve().parents[1]/'examples/intake'
        labels = json.loads((base/'labels.json').read_text())
        adapters = [ReplayAdapter(base/'recordings'/(row['id']+'.json')) for row in labels]
        with patch('erp_demo.intake_benchmark.ModelAdapter',side_effect=adapters):
            report = run('ollama','synthetic-contract')
        weak = next(r for r in report['rows'] if r['id']=='weak')
        self.assertEqual(weak['expected_status'],'ready')
