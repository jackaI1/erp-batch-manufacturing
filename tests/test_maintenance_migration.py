"""Old-format CSV is checked before any API write."""
import tempfile
from pathlib import Path
import unittest
from maintenance.migration import read_legacy


class MigrationTests(unittest.TestCase):
    def test_repository_legacy_csv_maps_eight_assets(self):
        rows = read_legacy('examples/maintenance/legacy-assets.csv')
        self.assertEqual(len(rows), 8)
        engine = next(r for r in rows if r['Code'] == 'TUG-ENGINE')
        self.assertEqual(engine['ParentCode'], 'TUG-PROP')
        self.assertEqual(engine['RunningHours'], 1200)

    def test_missing_columns_duplicate_keys_and_invalid_meter_are_rejected(self):
        source = Path('examples/maintenance/legacy-assets.csv').read_text()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.csv'
            for value in ('asset_no\nTUG-01\n', source + source.splitlines()[1] + '\n', source.replace(',1200,', ',nan,', 1)):
                path.write_text(value)
                with self.assertRaises(ValueError):
                    read_legacy(path)
