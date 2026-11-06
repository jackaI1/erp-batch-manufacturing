"""python -m maintenance wait|demo; all generated data is synthetic."""
import argparse
import json
import time
from datetime import date
from pathlib import Path
from urllib.error import URLError
from .client import Client, APIError
from .fleet import audit, forecast
from .seed import setup, fixture, migrate, generate_work_orders
from .migration import read_legacy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['wait', 'demo'])
    parser.add_argument('--output', default='artifacts/maintenance')
    args = parser.parse_args()
    if args.command == 'wait':
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            try:
                with Client.from_env() as api:
                    api.request('GET', 'classes')
                print('Real openMAINT REST API ready')
                return
            except (APIError, URLError, TimeoutError, ConnectionError):
                time.sleep(5)
        raise SystemExit('openMAINT did not become ready within 15 minutes')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    today = date(2026, 11, 1)  # frozen comparison date, not a claim about current compliance
    with Client.from_env() as api:
        setup(api)
        source = fixture()
        legacy = Path(__file__).parents[1] / 'examples/maintenance/legacy-assets.csv'
        migration = migrate(api, read_legacy(legacy))
        if not migration['ok']:
            raise SystemExit('Migration reconciliation failed; review existing records')
        for plan in source['plans']:
            api.ensure_card('FleetPlan', dict(plan, Description=plan['Task']))
        orders = generate_work_orders(api, today)
        assets, plans = api.cards('FleetAsset'), api.cards('FleetPlan')
        result = dict(label='Synthetic fleet, frozen as-of 2026-11-01; illustrative survey dates', migration=migration, assets=assets, plans=plans, work_orders=orders, audit=audit(assets, plans, orders, today), forecast=forecast(assets, plans, today))
        (output / 'fleet-report.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(dict(assets=len(assets), plans=len(plans), due_work_orders=len(orders), findings=len(result['audit']), migration_ok=migration['ok'])))


if __name__ == '__main__':
    main()
