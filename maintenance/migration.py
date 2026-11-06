"""Explicit legacy-column mapping; reject bad rows before touching openMAINT."""
import csv
from .fleet import validate_asset

MAPPING = {'asset_no': 'Code', 'asset_type': 'Kind', 'vessel_no': 'Vessel', 'parent_no': 'ParentCode', 'serial_no': 'Serial', 'meter_hours': 'RunningHours', 'hours_per_day': 'DailyHours', 'replacement_cad': 'ReplacementCost'}


def read_legacy(path):
    with open(path, newline='', encoding='utf-8-sig') as file:
        reader = csv.DictReader(file)
        if set(MAPPING) - set(reader.fieldnames or []):
            raise ValueError('Legacy file is missing required columns')
        assets = []
        for row in reader:
            mapped = {dest: row[src].strip() for src, dest in MAPPING.items()}
            for field in ('RunningHours', 'DailyHours', 'ReplacementCost'):
                mapped[field] = int(mapped[field])
            assets.append(mapped)
    if len({a['Code'] for a in assets}) != len(assets):
        raise ValueError('Duplicate legacy asset numbers')
    for asset in assets:
        validate_asset(asset, assets)
    return assets
