"""The common intake interface drives real OData-shaped HTTP requests locally."""
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import copy
import json
import os
import re
import socket
import threading
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

COMPANY = '00000000-0000-0000-0000-000000000001'
CUSTOMER = '00000000-0000-0000-0000-000000000002'
ITEM = '00000000-0000-0000-0000-000000000003'
ORDER = '00000000-0000-0000-0000-000000000004'


def generic_order():
    return {'customer': 'SYN-CUSTOMER', 'po_no': 'SYN-PO-1', 'currency': 'CAD',
            'company': 'Demo Compounding', 'docstatus': 0, 'transaction_date': '2026-11-06',
            'delivery_date': '2026-11-20', 'items': [
                {'item_code': 'FG-LOTION', 'qty': 2, 'rate': 12.5, 'warehouse': 'Finished Goods - DCP'}]}


@contextmanager
def orders_contract_server():
    """Only the caller owns this short-lived socket; never detach a fixture."""
    state = {'customers': [{'id': CUSTOMER, 'number': 'SYN-CUSTOMER', 'currencyCode': '', 'blocked': ' '}],
             'items': [{'id': ITEM, 'number': 'FG-LOTION', 'unitPrice': 12.5, 'blocked': False,
                        'baseUnitOfMeasureCode': 'Kg', 'priceIncludesTax': False}],
             'orders': [], 'lines': [], 'requests': [], 'fail_line': False, 'header_prices_include_tax': False}
    prefix = '/api/v2.0/companies(' + COMPANY + ')'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, code, payload):
            encoded = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def handle_request(self):
            uri = urlparse(self.path)
            query = parse_qs(uri.query)
            data = json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.headers.get('Content-Length') else None
            state['requests'].append({'method': self.command, 'path': uri.path, 'query': query,
                                      'body': data, 'authorization': self.headers.get('Authorization')})
            route = uri.path[len(prefix):] if uri.path.startswith(prefix) else None
            filt = query.get('$filter', [''])[0]
            if self.command == 'GET' and route == '/customers':
                matches = re.fullmatch(r"number eq '((?:''|[^'])*)'", filt)
                rows = [row for row in state['customers'] if matches and row['number'] == matches[1].replace("''", "'")]
                return self.respond(200, {'value': rows})
            if self.command == 'GET' and route == '/items':
                matches = re.fullmatch(r"contains\(number,'((?:''|[^'])*)'\)", filt)
                rows = [row for row in state['items'] if matches and matches[1].replace("''", "'") in row['number']]
                return self.respond(200, {'value': rows})
            if self.command == 'GET' and route == '/salesOrders':
                matches = re.fullmatch(r"externalDocumentNumber eq '((?:''|[^'])*)' and customerId eq ([a-f0-9-]+)", filt)
                rows = [row for row in state['orders'] if matches and row['externalDocumentNumber'] == matches[1].replace("''", "'") and row['customerId'] == matches[2]]
                return self.respond(200, {'value': rows})
            if self.command == 'POST' and route == '/salesOrders':
                row = dict(data, id=ORDER, status='Draft')
                if state['header_prices_include_tax'] != 'missing':
                    row['pricesIncludeTax'] = state['header_prices_include_tax']
                state['orders'].append(row)
                return self.respond(201, row)
            if self.command == 'POST' and route == '/salesOrders(' + ORDER + ')/salesOrderLines':
                if state['fail_line']:
                    return self.respond(400, {'error': {'code': 'SyntheticLineRejected'}})
                state['lines'].append(data)
                return self.respond(201, dict(data, id=ITEM))
            if self.command == 'GET' and route == '/salesOrders(' + ORDER + ')':
                return self.respond(200, dict(state['orders'][0], salesOrderLines=state['lines'], **{'@odata.etag': 'W/"1"'}))
            return self.respond(404, {'error': {'code': 'NotFound'}})

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_address[1]
    worker = threading.Thread(target=server.serve_forever, name='owned-reviewed-bc-contract', daemon=True)
    state['resource'] = {'owner': 'tests.test_business_central_orders.orders_contract_server',
                         'pid': os.getpid(), 'started_at': datetime.now(timezone.utc).isoformat(),
                         'port': port, 'cleanup': 'server.shutdown(); server.server_close(); worker.join(timeout=5)'}
    worker.start()
    try:
        yield 'http://127.0.0.1:' + str(port) + '/api/v2.0', state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive(), 'contract worker leaked'
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', port)) != 0, 'contract port leaked'


class BusinessCentralOrdersTests(unittest.TestCase):
    def adapter(self, url, currency='CAD'):
        from erp_demo.business_central import BusinessCentral, BusinessCentralOrders
        return BusinessCentralOrders(BusinessCentral(url, COMPANY, 'synthetic-test-token'), base_currency=currency)

    def test_common_interface_exposes_exact_customer_and_base_price(self):
        with orders_contract_server() as (url, state):
            adapter = self.adapter(url)
            self.assertEqual(adapter.get_customer('SYN-CUSTOMER'), {'name': 'SYN-CUSTOMER', 'currency': 'CAD'})
            self.assertIsNone(adapter.get_customer('UNKNOWN'))
            self.assertEqual(adapter.search_items('FG-LOTION'), [
                {'item_code': 'FG-LOTION', 'price': '12.5', 'currency': 'CAD', 'uom': 'Kg'}])
            state['customers'][0]['currencyCode'] = 'USD'
            self.assertEqual(adapter.get_customer('SYN-CUSTOMER')['currency'], 'USD')

    def test_base_currency_is_explicit_and_required_in_environment(self):
        from erp_demo.business_central import BusinessCentralOrders
        for currency in ['', 'cad', None, '????']:
            with orders_contract_server() as (url, state), self.subTest(currency=currency), self.assertRaises(ValueError):
                self.adapter(url, currency)
        with patch.dict(os.environ, {'BC_API_URL': 'https://example.com/api/v2.0', 'BC_COMPANY_ID': COMPANY, 'BC_TOKEN': 'synthetic'}, clear=True):
            with self.assertRaises(KeyError):
                BusinessCentralOrders.from_env()

    def test_generic_draft_maps_only_business_central_fields_and_base_units(self):
        with orders_contract_server() as (url, state):
            result = self.adapter(url).create_draft(generic_order())
            self.assertEqual(result['status'], 'Draft')
            self.assertEqual(result['salesOrderLines'][0]['itemId'], ITEM)
            self.assertEqual(state['orders'][0], {'customerId': CUSTOMER, 'externalDocumentNumber': 'SYN-PO-1',
                'orderDate': '2026-11-06', 'currencyCode': '', 'id': ORDER, 'status': 'Draft', 'pricesIncludeTax': False})
            self.assertEqual(state['lines'], [{'itemId': ITEM, 'lineType': 'Item', 'quantity': 2,
                'unitPrice': 12.5, 'unitOfMeasureCode': 'Kg'}])
            self.assertNotIn('warehouse', json.dumps(state['lines']))
            self.assertEqual(state['requests'][-1]['query'].get('$expand'), ['salesOrderLines'])

    def test_changed_price_currency_and_blocked_records_cause_no_write(self):
        with orders_contract_server() as (url, state):
            adapter = self.adapter(url)
            self.assertEqual(adapter.search_items('FG-LOTION')[0]['price'], '12.5')
            original = copy.deepcopy(state)
            cases = [('items', 'unitPrice', 99), ('items', 'blocked', True),
                     ('items', 'priceIncludesTax', True), ('items', 'baseUnitOfMeasureCode', ''),
                     ('items', 'baseUnitOfMeasureCode', 'PCS'),
                     ('customers', 'blocked', 'All'), ('customers', 'currencyCode', 'USD')]
            for table, key, value in cases:
                state[table][0][key] = value
                with self.subTest(table=table, key=key), self.assertRaises(ValueError):
                    adapter.create_draft(generic_order())
                state[table] = copy.deepcopy(original[table])
            self.assertFalse(any(row['method'] == 'POST' for row in state['requests']))

    def test_unknown_and_ambiguous_items_and_missing_flags_are_held(self):
        with orders_contract_server() as (url, state):
            adapter = self.adapter(url)
            state['items'].append(dict(state['items'][0]))
            with self.assertRaises(ValueError):
                adapter.create_draft(generic_order())
            state['items'] = []
            with self.assertRaises(ValueError):
                adapter.create_draft(generic_order())
            state['customers'][0].pop('blocked')
            self.assertIsNone(adapter.get_customer('SYN-CUSTOMER'))
            self.assertFalse(any(row['method'] == 'POST' for row in state['requests']))

    def test_invalid_proposal_and_nonbase_currency_never_write(self):
        for key, value in [('docstatus', 1), ('currency', 'USD'), ('items', []), ('po_no', '')]:
            with orders_contract_server() as (url, state), self.subTest(key=key):
                proposal = generic_order()
                proposal[key] = value
                with self.assertRaises(ValueError):
                    self.adapter(url).create_draft(proposal)
                self.assertFalse(any(row['method'] == 'POST' for row in state['requests']))

    def test_exact_sku_and_guid_are_resolved_again_at_write(self):
        with orders_contract_server() as (url, state):
            adapter = self.adapter(url)
            adapter.search_items('FG-LOTION')
            new_id = '00000000-0000-0000-0000-000000000009'
            state['items'][0]['id'] = new_id
            state['items'].append(dict(state['items'][0], number='FG-LOTION-OTHER', id=ITEM))
            adapter.create_draft(generic_order())
            self.assertEqual(state['lines'][0]['itemId'], new_id)

    def test_sequential_duplicate_po_guard_uses_customer_and_escaped_po(self):
        with orders_contract_server() as (url, state):
            adapter = self.adapter(url)
            proposal = generic_order()
            proposal['po_no'] = "SYN'PO"
            adapter.create_draft(proposal)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                adapter.create_draft(proposal)
            self.assertEqual(len(state['orders']), 1)
            reads = [row for row in state['requests'] if row['method'] == 'GET' and row['path'].endswith('/salesOrders')]
            self.assertEqual(reads[0]['query']['$filter'], ["externalDocumentNumber eq 'SYN''PO' and customerId eq " + CUSTOMER])

    def assert_unsafe_header_stops_before_lines(self, prices_include_tax):
        from erp_demo.business_central import PartialOrderError
        with orders_contract_server() as (url, state):
            state['header_prices_include_tax'] = prices_include_tax
            with self.assertRaises(PartialOrderError) as failure:
                self.adapter(url).create_draft(generic_order())
            self.assertEqual(failure.exception.order_id, ORDER)
            self.assertEqual(len(state['orders']), 1)
            self.assertEqual(state['lines'], [])
            writes = [row for row in state['requests'] if row['method'] == 'POST']
            self.assertEqual(len(writes), 1)
            self.assertTrue(writes[0]['path'].endswith('/salesOrders'))
            self.assertNotIn('pricesIncludeTax', writes[0]['body'])

    def test_tax_inclusive_header_preserves_recovery_id_without_line_writes(self):
        self.assert_unsafe_header_stops_before_lines(True)

    def test_missing_header_tax_basis_preserves_recovery_id_without_line_writes(self):
        self.assert_unsafe_header_stops_before_lines('missing')

    def test_partial_write_recovery_id_survives_common_adapter(self):
        from erp_demo.business_central import PartialOrderError
        with orders_contract_server() as (url, state):
            state['fail_line'] = True
            with self.assertRaises(PartialOrderError) as failure:
                self.adapter(url).create_draft(generic_order())
            self.assertEqual(failure.exception.order_id, ORDER)
            self.assertEqual(len(state['orders']), 1)
            self.assertEqual(sum(row['method'] == 'POST' for row in state['requests']), 2)


if __name__ == '__main__':
    unittest.main()
