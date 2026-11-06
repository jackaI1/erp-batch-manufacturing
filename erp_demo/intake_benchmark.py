"""Parallel comparison against hand-keyed synthetic answers, without ERP writes."""
import argparse
import json
from pathlib import Path
import statistics
import time

from .intake import load_document, review
from .models import ModelAdapter, ReplayAdapter

ROOT = Path(__file__).resolve().parents[1] / 'examples/intake'


class ReferenceCatalog:
    """Frozen ERP snapshot for reproducible extraction comparison; not a live ERP."""
    def get_customer(self, code):
        return {'name': code, 'currency': 'CAD'} if code == 'SYN-CUSTOMER' else None

    def search_items(self, code):
        prices = {'FG-LOTION': '12.50', 'FG-BALM': '15.00'}
        return [{'item_code': code, 'price': prices[code], 'currency': 'CAD', 'uom':'Kg'}] if code in prices else []


def flatten(reference):
    fields = {key: reference[key] for key in ('customer', 'purchase_order', 'currency')}
    for i, line in enumerate(reference['lines']):
        fields.update({f'lines[{i}].{key}': value for key, value in line.items()})
    return fields


def values(extraction):
    data = extraction.model_dump()
    return {**{k: data[k]['value'] for k in ('customer','purchase_order','currency')},
            'lines': [{k:f['value'] for k,f in row.items()} for row in data['lines']]}


def run(backend='replay', model=None):
    labels = json.loads((ROOT/'labels.json').read_text())
    rows = []
    for case in labels:
        pages = load_document(ROOT/case['file'])
        adapter = ReplayAdapter(ROOT/'recordings'/(case['id']+'.json')) if backend == 'replay' else ModelAdapter(backend, model=model)
        start = time.perf_counter()
        reference = flatten(case['reference'])
        try:
            extraction = adapter.extract(pages)
            actual = flatten(values(extraction))
            decision = review(extraction.model_dump(), pages, ReferenceCatalog())
            error = None
        except (ValueError, KeyError) as exc:
            actual, decision, error = {}, {'status':'review','issues':['invalid model output']}, str(exc)[:300]
        correct = sum(value == actual.get(key) for key,value in reference.items())
        # Extra fields/lines also count against accuracy.
        total = len(set(reference) | set(actual))
        rows.append(dict(id=case['id'], status=decision['status'], expected_status=case['expected_status'],
                         correct_fields=correct, total_fields=total, seconds=time.perf_counter()-start,
                         issues=decision['issues'], error=error))
    accepted = [r for r in rows if r['status']=='ready']
    return dict(mode='synthetic protocol replay' if backend=='replay' else 'live '+backend,
                provenance=('Hand-authored tool-response fixtures. This tests the pipeline, not model quality.'
                            if backend=='replay' else 'Live model calls; raw responses may vary.'),
                orders=len(rows),held_orders=sum(r['status']=='review' for r in rows),
                abstention_rate=sum(r['status']=='review' for r in rows)/len(rows),
                raw_field_accuracy=sum(r['correct_fields'] for r in rows)/sum(r['total_fields'] for r in rows),
                accepted_field_accuracy=(sum(r['correct_fields'] for r in accepted)/sum(r['total_fields'] for r in accepted)
                                         if accepted else None),
                status_accuracy=sum(r['status']==r['expected_status'] for r in rows)/len(rows),
                mean_seconds=statistics.mean(r['seconds'] for r in rows),
                timing_note='Measured extraction plus review wall time; replay excludes model latency. No human timing or speedup claim.',rows=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=['replay','anthropic','ollama'],default='replay')
    parser.add_argument('--model')
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    report = run(args.backend,args.model)
    result = json.dumps(report,indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result)
    else: print(result)


if __name__ == '__main__': main()
