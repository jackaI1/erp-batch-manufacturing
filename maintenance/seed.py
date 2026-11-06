"""Real API schema, legacy migration and idempotent due-work generation."""
import json
from pathlib import Path
from .client import APIError
from .fleet import due, reconcile, validate_asset

SCHEMA = {
    'FleetAsset': {'Kind': 'string', 'Vessel': 'string', 'ParentCode': 'string', 'Serial': 'string', 'RunningHours': 'integer', 'DailyHours': 'integer', 'ReplacementCost': 'integer'},
    'FleetPlan': {'AssetCode': 'string', 'Task': 'string', 'Category': 'string', 'FailureCode': 'string', 'IntervalHours': 'integer', 'LastHours': 'integer', 'NextDate': 'string', 'EstimatedCost': 'integer'},
    'FleetWorkOrder': {'AssetCode': 'string', 'PlanCode': 'string', 'Task': 'string', 'FailureCode': 'string', 'Status': 'string', 'DueDate': 'string', 'Triggers': 'string', 'EstimatedCost': 'integer'},
}


def setup(api):
    classes = {row['name'] for row in api.request('GET', 'classes', params={'scope': 'service', 'limit': 1000})['data']}
    for name, attributes in SCHEMA.items():
        if name not in classes:
            api.request('POST', 'classes', dict(name=name, description=name.replace('Fleet', 'Fleet '), type='standard', parent='Class', active=True, prototype=False), params={'scope': 'service'})
        existing = {a['name'] for a in api.request('GET', 'classes/' + name + '/attributes')['data']}
        for key, kind in attributes.items():
            if key not in existing:
                data = dict(name=key, description=key, type=kind, mode='write', active=True)
                if kind == 'string':
                    data['maxLength'] = 250
                api.request('POST', 'classes/' + name + '/attributes', data)


def fixture():
    return json.loads((Path(__file__).parents[1] / 'examples/maintenance/fleet.json').read_text())


def migrate(api, source):
    """Validate entire input before writes; mismatches are reconciled for review."""
    if len({r['Code'] for r in source}) != len(source):
        raise ValueError('Duplicate legacy codes')
    for asset in source:
        validate_asset(asset, source)
    before = api.cards('FleetAsset')
    known = {r['Code'] for r in before}
    created = 0
    for row in source:
        if row['Code'] not in known:
            api.ensure_card('FleetAsset', dict(row, Description='Synthetic fleet: ' + row['Code']))
            created += 1
    report = reconcile(source, api.cards('FleetAsset'))
    report['created'] = created
    report['policy'] = 'Existing cards are never overwritten; review mismatches and partial writes before retrying.'
    return report


def generate_work_orders(api, today):
    """One open card per plan and maintenance cycle; reruns reuse that card.

    Completing work requires manually recording the new LastHours/NextDate on
    the plan. This deliberately does not simulate the bundled workflow engine.
    """
    assets = {a['Code']: a for a in api.cards('FleetAsset')}
    result = []
    for plan in api.cards('FleetPlan'):
        if plan.get('AssetCode') not in assets:
            raise ValueError('Unknown plan asset')
        triggers = due(plan, assets[plan['AssetCode']], today)
        if not triggers:
            continue
        cycle = str(plan.get('LastHours') or 0) + '-' + str(plan.get('NextDate') or 'hours')
        code = plan['Code'] + '-' + cycle
        if not plan.get('FailureCode'):
            raise ValueError('Missing failure code; review before work-order creation')
        result.append(api.ensure_card('FleetWorkOrder', dict(Code=code, Description='Synthetic work: ' + plan['Task'], AssetCode=plan['AssetCode'], PlanCode=plan['Code'], Task=plan['Task'], FailureCode=plan['FailureCode'], Status='open', DueDate=plan.get('NextDate') or today.isoformat(), Triggers=','.join(triggers), EstimatedCost=plan.get('EstimatedCost', 0))))
    return result
