"""Opt-in real openMAINT REST creation, round trips, migration and work orders."""
import os
import unittest
from datetime import date


@unittest.skipUnless(os.getenv('MAINT_INTEGRATION') == '1', 'requires real openMAINT')
class OpenMaintIntegration(unittest.TestCase):
    def test_fleet_extension_round_trip(self):
        from maintenance.client import Client
        from maintenance.seed import setup, fixture, migrate, generate_work_orders
        from maintenance.fleet import audit, forecast
        with Client.from_env() as api:
            setup(api)
            source = fixture()
            result = migrate(api, source['assets'])
            self.assertTrue(result['ok'], result)
            again = migrate(api, source['assets'])
            self.assertEqual(again['created'], 0)
            for plan in source['plans']:
                api.ensure_card('FleetPlan', plan)
            assets, plans = api.cards('FleetAsset'), api.cards('FleetPlan')
            self.assertEqual(len(assets), 8)
            self.assertEqual(len(plans), 6)
            first = generate_work_orders(api, date(2026, 11, 1))
            second = generate_work_orders(api, date(2026, 11, 1))
            self.assertGreaterEqual(len(first), 3)
            self.assertEqual({r['_id'] for r in first}, {r['_id'] for r in second})
            orders = api.cards('FleetWorkOrder')
            self.assertTrue(all(r['Status'] == 'open' for r in orders))
            self.assertTrue(all(r['PlanCode'] and r['AssetCode'] for r in orders))
            self.assertIn('no_plan', {f['kind'] for f in audit(assets, plans, orders, date(2026, 11, 1))})
            self.assertEqual(len(forecast(assets, plans, date(2026, 11, 1))), 2)
            # Mismatches are held, never silently overwritten by migration.
            bad = [dict(row, Serial='BAD') if row['Code'] == 'TUG-01' else row for row in source['assets']]
            rejected = migrate(api, bad)
            self.assertFalse(rejected['ok'])
            self.assertEqual(api.cards('FleetAsset')[0]['Serial'], assets[0]['Serial'])
