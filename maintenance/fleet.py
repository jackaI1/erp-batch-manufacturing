"""Deterministic maintenance decisions; no inferred meter readings or survey rules."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from math import ceil, isfinite


def number(value, name, minimum=0):
    if isinstance(value, bool):
        raise ValueError(name + ' must be a finite number')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(name + ' must be a finite number') from None
    if not isfinite(result) or result < minimum:
        raise ValueError(name + ' must be finite and >= ' + str(minimum))
    return result


def validate_asset(asset, assets):
    if not asset.get('Code') or asset.get('Kind') not in ('vessel', 'system', 'component'):
        raise ValueError('Asset requires Code and vessel/system/component Kind')
    number(asset.get('RunningHours'), 'RunningHours')
    number(asset.get('ReplacementCost'), 'ReplacementCost')
    index = {row['Code']: row for row in assets}
    if asset['Kind'] != 'vessel':
        parent = index.get(asset.get('ParentCode'))
        if not parent or parent.get('Vessel') != asset.get('Vessel'):
            raise ValueError('Missing parent or different vessel')
        visited = {asset['Code']}
        while parent:
            if parent['Code'] in visited:
                raise ValueError('Asset hierarchy cycle')
            visited.add(parent['Code'])
            parent = index.get(parent.get('ParentCode'))
    if asset.get('Vessel') not in index or index[asset['Vessel']].get('Kind') != 'vessel':
        raise ValueError('Missing vessel')


def due(plan, asset, today):
    reasons = []
    interval = number(plan.get('IntervalHours', 0) or 0, 'IntervalHours')
    if interval:
        last = number(plan.get('LastHours'), 'LastHours')
        current = number(asset.get('RunningHours'), 'RunningHours')
        if current < last:
            raise ValueError('Meter rolled back; review readings')
        if current >= last + interval:
            reasons.append('hours')
    if plan.get('NextDate') and date.fromisoformat(plan['NextDate'][:10]) <= today:
        reasons.append('calendar')
    if not interval and not plan.get('NextDate'):
        raise ValueError('Plan needs a running-hours or calendar trigger')
    return reasons


def audit(assets, plans, work_orders, today):
    findings = []
    index = {a['Code']: a for a in assets}
    serials = Counter(a.get('Serial') for a in assets if a.get('Serial'))
    tasks = Counter((p.get('AssetCode'), p.get('Task')) for p in plans)
    for asset in assets:
        code = asset['Code']
        try:
            validate_asset(asset, assets)
        except ValueError as error:
            findings.append(dict(kind='missing_data', asset=code, detail=str(error)))
        if not asset.get('Serial'):
            findings.append(dict(kind='missing_data', asset=code, detail='Serial missing'))
        elif serials[asset['Serial']] > 1:
            findings.append(dict(kind='duplicate_asset', asset=code, detail=asset['Serial']))
        if not any(p.get('AssetCode') == code for p in plans):
            findings.append(dict(kind='no_plan', asset=code, detail='No explicit maintenance plan'))
    for plan in plans:
        code = plan.get('AssetCode')
        if code not in index:
            findings.append(dict(kind='missing_data', asset=code, detail='Plan links unknown asset'))
            continue
        if not plan.get('FailureCode'):
            findings.append(dict(kind='missing_failure_code', asset=code, detail=plan['Code']))
        if tasks[(code, plan.get('Task'))] > 1:
            findings.append(dict(kind='conflicting_task', asset=code, detail=plan.get('Task')))
        try:
            reasons = due(plan, index[code], today)
            if reasons:
                findings.append(dict(kind='overdue', asset=code, detail=plan['Code'], triggers=reasons))
        except ValueError as error:
            findings.append(dict(kind='missing_data', asset=code, detail=str(error)))
    for order in work_orders:
        if order.get('Status') != 'closed' and not order.get('FailureCode'):
            findings.append(dict(kind='missing_failure_code', asset=order.get('AssetCode'), detail=order['Code']))
    return findings


def reconcile(source, destination):
    """Compare business fields, retaining duplicate detection and unexpected rows."""
    fields = ('Code', 'Kind', 'Vessel', 'ParentCode', 'Serial', 'RunningHours', 'ReplacementCost')
    a = {r['Code']: r for r in source}
    b = {r['Code']: r for r in destination}
    duplicates = sorted(k for k, n in Counter(r['Code'] for r in destination).items() if n > 1)
    mismatched = []
    for code in sorted(a.keys() & b.keys()):
        changes = {f: {'source': a[code].get(f), 'destination': b[code].get(f)} for f in fields if str(a[code].get(f) or '') != str(b[code].get(f) or '') and not (f in ('RunningHours', 'ReplacementCost') and float(a[code].get(f, 0)) == float(b[code].get(f, 0)))}
        if changes:
            mismatched.append(dict(Code=code, fields=changes))
    missing, unexpected = sorted(a.keys() - b.keys()), sorted(b.keys() - a.keys())
    return dict(ok=not (missing or unexpected or mismatched or duplicates), source_count=len(source), destination_count=len(destination), missing=missing, unexpected=unexpected, mismatched=mismatched, duplicates=duplicates)


def forecast(assets, plans, today, days=365):
    """Simple undiscounted budget; hours forecast uses explicit DailyHours assumption."""
    end = today + timedelta(days=days)
    index = {a['Code']: a for a in assets}
    result = {}
    for asset in assets:
        vessel = asset['Vessel']
        row = result.setdefault(vessel, dict(vessel=vessel, horizon_days=days, replacement_cost=0, forecast_maintenance_cost=0, surveys=[]))
        row['replacement_cost'] += number(asset.get('ReplacementCost', 0), 'ReplacementCost')
    for plan in plans:
        asset = index.get(plan.get('AssetCode'))
        if not asset:
            continue
        row = result[asset['Vessel']]
        interval = number(plan.get('IntervalHours', 0) or 0, 'IntervalHours')
        events = 0
        if interval:
            daily = number(asset.get('DailyHours', 0) or 0, 'DailyHours')
            remaining = number(plan.get('LastHours'), 'LastHours') + interval - number(asset.get('RunningHours'), 'RunningHours')
            events = max(0, int((daily * days - remaining) // interval) + 1) if daily * days >= remaining else 0
        if plan.get('NextDate'):
            next_date = date.fromisoformat(plan['NextDate'][:10])
            if next_date <= end:
                events = max(events, 1)
                if plan.get('Category') in ('class', 'regulatory'):
                    row['surveys'].append(dict(plan=plan['Code'], due=next_date.isoformat(), category=plan['Category'], overdue=next_date < today))
        row['forecast_maintenance_cost'] += events * number(plan.get('EstimatedCost', 0), 'EstimatedCost')
    return sorted(result.values(), key=lambda r: r['vessel'])
