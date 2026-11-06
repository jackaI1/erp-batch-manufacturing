"""Grounded intake: reject model inventions before touching ERP records."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from erp_demo.intake import Extraction, load_document, review, draft_order
from erp_demo.models import ModelAdapter

ROOT = Path(__file__).resolve().parents[1]


def field(value, text=None, page=1, confidence=0.99):
    return dict(value=value, text=text or str(value), page=page, confidence=confidence)


def order():
    return dict(customer=field('SYN-CUSTOMER'), purchase_order=field('SYN-PO-1'),
                currency=field('CAD'), lines=[dict(item=field('FG-LOTION'),
                quantity=field('2'), unit_price=field('12.50'))])


class Catalog:
    def __init__(self):
        self.writes = []
        self.rate = '12.50'
    def get_customer(self, code):
        return {'name': code, 'currency': 'CAD'} if code == 'SYN-CUSTOMER' else None
    def search_items(self, code):
        return [{'item_code': 'FG-LOTION', 'price': self.rate, 'currency': 'CAD', 'uom': 'Kg'}] if code == 'FG-LOTION' else []
    def create_draft(self, data):
        self.writes.append(data)
        return {'name': 'SO-TEST', 'docstatus': 0, **data}


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.pages = {1: 'Customer: SYN-CUSTOMER\nPO: SYN-PO-1\nCurrency: CAD\nFG-LOTION | 2 | 12.50'}
        self.erp = Catalog()

    def test_schema_requires_every_field_evidence_and_no_extra_keys(self):
        for mutate in ('missing', 'extra', 'bool', 'nan'):
            data = order()
            if mutate == 'missing': del data['lines'][0]['item']['text']
            if mutate == 'extra': data['submit'] = True
            if mutate == 'bool': data['lines'][0]['quantity']['value'] = True
            if mutate == 'nan': data['customer']['confidence'] = float('nan')
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                Extraction.model_validate(data)

    def test_clean_draft_maps_decimal_fields_without_submitting(self):
        result = draft_order(order(), self.pages, self.erp)
        self.assertEqual(result['status'], 'draft')
        self.assertEqual(self.erp.writes[0]['items'][0]['qty'], 2)
        self.assertEqual(self.erp.writes[0]['items'][0]['rate'], 12.5)
        self.assertEqual(self.erp.writes[0]['docstatus'], 0)

    def test_unknown_price_confidence_source_and_currency_never_write(self):
        mutations = [('customer', 'OTHER'), ('item', 'UNKNOWN'), ('unit_price', '12.51'),
                     ('quantity', '-2'), ('currency', 'USD')]
        for name, value in mutations:
            data = order()
            (data[name] if name in data else data['lines'][0][name])['value'] = value
            with self.subTest(field=name):
                self.assertEqual(draft_order(data, self.pages, self.erp)['status'], 'review')
        data = order()
        data['lines'][0]['quantity']['confidence'] = 0.1
        self.assertEqual(draft_order(data, self.pages, self.erp)['status'], 'review')
        data = order()
        data['lines'][0]['item']['text'] = 'FG-LOTION not in this source'
        self.assertEqual(draft_order(data, self.pages, self.erp)['status'], 'review')
        self.assertEqual(self.erp.writes, [])

    def test_substring_numeric_evidence_is_not_enough(self):
        data = order()
        data['lines'][0]['quantity'] = field('2', text='12.50')
        self.assertTrue(review(data, self.pages, self.erp)['issues'])

    def test_changed_catalog_is_rechecked_at_write_time(self):
        self.assertFalse(review(order(), self.pages, self.erp)['issues'])
        self.erp.rate = '15'
        self.assertEqual(draft_order(order(), self.pages, self.erp)['status'], 'review')
        self.assertEqual(self.erp.writes, [])

    def test_missing_line_is_held_by_count_check(self):
        pages = dict(self.pages)
        pages[1] += '\nFG-BALM | 1 | 15.00'
        self.assertTrue(any('line count' in x for x in review(order(), pages, self.erp)['issues']))

    def test_reads_email_pdf_and_pdf_attachment_with_pages(self):
        base = ROOT / 'examples/intake'
        self.assertIn('SYN-PO-1', load_document(base/'clean.eml')[1])
        self.assertEqual(len(load_document(base/'two-page.pdf')), 2)
        self.assertIn('FG-BALM', load_document(base/'two-page.pdf')[2])
        pages = load_document(base/'attachment.eml')
        self.assertIn('FG-BALM', pages[max(pages)])

    def test_model_envelopes_use_same_schema_and_reject_plain_prose(self):
        for backend in ['anthropic', 'ollama']:
            adapter = ModelAdapter(backend, model='test', key='synthetic')
            envelope = ({'content':[{'type':'tool_use','name':'extract_order','input':order()}]}
                        if backend == 'anthropic' else
                        {'message':{'tool_calls':[{'function':{'name':'extract_order','arguments':order()}}]}})
            with patch.object(adapter, '_post', return_value=envelope) as send:
                actual = adapter.extract(self.pages)
                self.assertEqual(actual.customer.value, 'SYN-CUSTOMER')
                payload = send.call_args.args[0]
                self.assertTrue(payload['tools'])
            with patch.object(adapter, '_post', return_value={'content': []}):
                with self.assertRaises(ValueError): adapter.extract(self.pages)

    def test_recording_bound_to_source_hash(self):
        from erp_demo.models import ReplayAdapter
        adapter = ReplayAdapter(ROOT/'examples/intake/recordings/clean.json')
        self.assertEqual(adapter.extract(load_document(ROOT/'examples/intake/clean.eml')).customer.value, 'SYN-CUSTOMER')
        with self.assertRaisesRegex(ValueError, 'source'):
            adapter.extract({1:'different source'})

class OmissionAndIdentityRegressionTests(unittest.TestCase):
    def test_unrecognized_pipe_rows_block_omitted_unknown_item(self):
        pages = {1: 'Customer: SYN-CUSTOMER\nPO: SYN-PO-1\nCurrency: CAD\nFG-LOTION | 2 | 12.50\nFG_OIL | 5 | 1.00'}
        erp = Catalog()
        self.assertEqual(draft_order(order(),pages,erp)['status'],'review')
        self.assertEqual(erp.writes,[])

    def test_customer_and_po_substrings_do_not_identify_source_headers(self):
        for header,value in [('Customer','NOT-SYN-CUSTOMER'),('PO','NOT-SYN-PO-1')]:
            pages = {1:'Customer: SYN-CUSTOMER\nPO: SYN-PO-1\nCurrency: CAD\nFG-LOTION | 2 | 12.50'}
            original = 'SYN-CUSTOMER' if header=='Customer' else 'SYN-PO-1'
            pages[1] = pages[1].replace(header+': '+original,header+': '+value)
            erp = Catalog()
            self.assertEqual(draft_order(order(),pages,erp)['status'],'review')
            self.assertEqual(erp.writes,[])

class NumericRangeRegressionTests(unittest.TestCase):
    def test_finite_decimal_that_overflows_float_is_held(self):
        data = order()
        data['lines'][0]['quantity'] = field('1e9999')
        pages = {1:'Customer: SYN-CUSTOMER\nPO: SYN-PO-1\nCurrency: CAD\nFG-LOTION | 1e9999 | 12.50'}
        erp = Catalog()
        self.assertEqual(draft_order(data,pages,erp)['status'],'review')
        self.assertEqual(erp.writes,[])

class ModelSchemaRegressionTests(unittest.TestCase):
    def test_tool_schema_expands_references_for_local_templates(self):
        payload = ModelAdapter('ollama',model='synthetic').payload({1:'synthetic'})
        schema = payload['tools'][0]['function']['parameters']
        self.assertEqual(schema['properties']['customer'].get('type'),'object')
        self.assertNotIn('$ref',json.dumps(schema))

class EmptySiteSetupTests(unittest.TestCase):
    def test_setup_supplies_customer_roots_on_wizardless_site(self):
        from erp_demo.intake import setup_orders
        class EmptySite:
            def __init__(self): self.rows = {}
            def ensure(self,kind,name,**fields):
                for field,target in [('parent_customer_group','Customer Group'),('customer_group','Customer Group'),('territory','Territory')]:
                    if fields.get(field) and (target,fields[field]) not in self.rows:
                        raise ValueError('missing required linked master data')
                self.rows[kind,name]=fields
            def list(self,*args): return []
            def create(self,kind,**fields): self.rows[kind,fields['item_code']]=fields
        api = EmptySite()
        setup_orders(api)
        self.assertIn(('Customer','SYN-CUSTOMER'),api.rows)

class DraftWarehouseRegressionTests(unittest.TestCase):
    def test_stock_order_lines_include_the_demo_finished_goods_warehouse(self):
        from erp_demo.workflow import FINISHED
        erp = Catalog()
        pages = {1:'Customer: SYN-CUSTOMER\nPO: SYN-PO-1\nCurrency: CAD\nFG-LOTION | 2 | 12.50'}
        draft_order(order(),pages,erp)
        self.assertEqual(erp.writes[0]['items'][0]['warehouse'],FINISHED)
