"""Synthetic fleet planning, migration and condition-monitoring contracts."""
import unittest
from datetime import date
from maintenance.fleet import audit, due, forecast, reconcile, validate_asset


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.assets = [dict(Code='TUG-01', Kind='vessel', Vessel='TUG-01', ParentCode='', Serial='S1', RunningHours=1200, ReplacementCost=100000), dict(Code='ENG-01', Kind='component', Vessel='TUG-01', ParentCode='TUG-01', Serial='E1', RunningHours=1200, ReplacementCost=10000)]
        self.plans = [dict(Code='P1', AssetCode='ENG-01', Task='Oil change', FailureCode='LUBE', IntervalHours=250, LastHours=900, NextDate='2026-12-01', EstimatedCost=100)]

    def test_hours_and_date_are_independent_triggers(self):
        self.assertEqual(due(self.plans[0], self.assets[1], date(2026, 11, 1)), ['hours'])
        self.assertEqual(due(self.plans[0], dict(self.assets[1], RunningHours=1000), date(2026, 12, 1)), ['calendar'])
        self.assertEqual(due(self.plans[0], self.assets[1], date(2026, 12, 1)), ['hours', 'calendar'])

    def test_audit_detects_missing_plan_duplicate_serial_missing_code_and_conflict(self):
        assets = self.assets + [dict(self.assets[1], Code='ENG-02')]
        plans = self.plans + [dict(self.plans[0], Code='P2', FailureCode='')]
        findings = audit(assets, plans, [], date(2026, 11, 1))
        kinds = {f['kind'] for f in findings}
        self.assertTrue({'duplicate_asset', 'no_plan', 'missing_failure_code', 'conflicting_task', 'overdue'} <= kinds)

    def test_missing_hierarchy_and_nonfinite_readings_rejected(self):
        with self.assertRaises(ValueError):
            validate_asset(dict(self.assets[1], RunningHours=float('nan')), self.assets)
        with self.assertRaises(ValueError):
            validate_asset(dict(self.assets[1], ParentCode='MISSING'), self.assets)

    def test_migration_reconciliation_detects_value_drift_and_missing_rows(self):
        result = reconcile(self.assets, [dict(self.assets[0], Serial='CHANGED')])
        self.assertEqual(result['missing'], ['ENG-01'])
        self.assertEqual(result['mismatched'][0]['Code'], 'TUG-01')
        self.assertFalse(result['ok'])
        self.assertTrue(reconcile(self.assets, self.assets)['ok'])

    def test_daily_usage_drift_is_not_reconciled_as_equal(self):
        source = [dict(self.assets[0], DailyHours=8)]
        result = reconcile(source, [dict(source[0], DailyHours=24)])
        self.assertFalse(result['ok'])
        self.assertIn('DailyHours', result['mismatched'][0]['fields'])

    def test_negative_daily_usage_is_rejected_before_migration(self):
        with self.assertRaises(ValueError):
            validate_asset(dict(self.assets[1], DailyHours=-1), self.assets)

    def test_forecast_groups_costs_and_surveys_by_vessel(self):
        surveys = [dict(Code='S1', AssetCode='TUG-01', Task='Class survey', FailureCode='SURVEY', NextDate='2026-11-10', EstimatedCost=500, Category='class')]
        result = forecast(self.assets, self.plans + surveys, date(2026, 11, 1), 365)
        self.assertEqual(result[0]['vessel'], 'TUG-01')
        self.assertEqual(result[0]['replacement_cost'], 110000)
        self.assertEqual(result[0]['surveys'][0]['due'], '2026-11-10')
        self.assertGreaterEqual(result[0]['forecast_maintenance_cost'], 600)


if __name__ == '__main__':
    unittest.main()
