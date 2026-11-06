"""Business Central API v2 contract over a real, bounded localhost server.

The server implements the documented HTTP shape, not a Microsoft tenant.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from datetime import datetime, timezone
import socket
import threading
import unittest
from urllib.parse import parse_qs, urlparse

COMPANY = '00000000-0000-0000-0000-000000000001'
CUSTOMER = '00000000-0000-0000-0000-000000000002'
ITEM = '00000000-0000-0000-0000-000000000003'
ORDER = '00000000-0000-0000-0000-000000000004'


@contextmanager
def contract_server():
    """Own the socket and worker explicitly, including assertion-failure paths."""
    state = {'requests': [], 'next_link': None, 'customers': [{'id': CUSTOMER, 'number': 'SYNTH-01', 'displayName': 'Synthetic customer'}], 'fail_line': False, 'redirect': False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, payload, headers=None):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def handle_request(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            body = json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.headers.get('Content-Length') else None
            state['requests'].append((self.command, parsed.path, query, dict(self.headers), body))
            prefix = '/api/v2.0/companies(' + COMPANY + ')'
            if not parsed.path.startswith(prefix):
                return self.respond(404, {'error': {'code': 'NotFound'}})
            if self.command == 'GET' and parsed.path == prefix + '/items':
                if state['redirect']:
                    return self.respond(302, {}, {'Location': 'https://evil.example/steal'})
                payload = {'value': [{'id': ITEM, 'number': "O'IL", 'unitPrice': 8}]}
                if '$skiptoken' not in query:
                    payload['@odata.nextLink'] = state['next_link'] or prefix + '/items?$skiptoken=second'
                else:
                    payload['value'] = [{'id': '00000000-0000-0000-0000-000000000005', 'number': 'WAX', 'unitPrice': 15}]
                return self.respond(200, payload)
            if self.command == 'GET' and parsed.path == prefix + '/customers':
                return self.respond(200, {'value': state['customers']})
            if self.command == 'GET' and parsed.path == prefix + '/salesOrders(' + ORDER + ')':
                return self.respond(200, {'id': ORDER, 'status': 'Draft', '@odata.etag': 'W/"1"', 'salesOrderLines': [{'itemId': ITEM, 'quantity': 2}]}, {'ETag': 'W/"1"'})
            if self.command == 'POST' and parsed.path == prefix + '/salesOrders':
                return self.respond(201, dict(body, id=ORDER, status='Draft', **{'@odata.etag': 'W/"1"'}))
            if self.command == 'POST' and parsed.path == prefix + '/salesOrders(' + ORDER + ')/salesOrderLines':
                if state['fail_line']:
                    return self.respond(400, {'error': {'code': 'BadRequest'}})
                return self.respond(201, dict(body, id=ITEM))
            if self.command == 'PATCH' and parsed.path == prefix + '/salesOrders(' + ORDER + ')':
                if self.headers.get('If-Match') != 'W/"1"':
                    return self.respond(412, {'error': {'code': 'BadRequest_InvalidToken'}})
                return self.respond(200, dict(body, id=ORDER, **{'@odata.etag': 'W/"2"'}))
            return self.respond(404, {'error': {'code': 'NotFound'}})
        do_GET = handle_request
        do_POST = handle_request
        do_PATCH = handle_request

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_address[1]
    worker = threading.Thread(target=server.serve_forever, name='owned-bc-contract', daemon=True)
    worker.start()
    state['port'] = port
    state['resource'] = {'owner': 'tests.test_business_central.contract_server', 'pid': os.getpid(),
                         'started_at': datetime.now(timezone.utc).isoformat(), 'port': port,
                         'cleanup': 'server.shutdown(); server.server_close(); worker.join(timeout=5)'}
    try:
        yield 'http://127.0.0.1:' + str(port) + '/api/v2.0', state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive(), 'contract server thread leaked'
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', port)) != 0, 'contract server socket leaked'


class BusinessCentralTests(unittest.TestCase):
    def adapter(self, base):
        from erp_demo.business_central import BusinessCentral
        return BusinessCentral(base, COMPANY, token='synthetic-test-token')

    def test_filter_quotes_and_server_driven_paging(self):
        with contract_server() as (base, state):
            rows = self.adapter(base).search_items("O'IL")
            self.assertEqual([r['number'] for r in rows], ["O'IL", 'WAX'])
            first = state['requests'][0]
            self.assertEqual(first[2]['$filter'], ["contains(number,'O''IL')"])
            self.assertEqual(first[3]['Authorization'], 'Bearer synthetic-test-token')
            self.assertIn('$skiptoken', state['requests'][1][2])

    def test_customer_is_exact_and_unknown_returns_none(self):
        with contract_server() as (base, state):
            result = self.adapter(base).get_customer('SYNTH-01')
            self.assertEqual(result['id'], CUSTOMER)
            self.assertEqual(state['requests'][0][2]['$filter'], ["number eq 'SYNTH-01'"])
            self.assertIsNone(self.adapter(base).get_customer('UNKNOWN'))
            state['customers'].append(dict(state['customers'][0]))
            with self.assertRaises(ValueError):
                self.adapter(base).get_customer('SYNTH-01')

    def test_create_draft_then_expand_lines_without_posting(self):
        with contract_server() as (base, state):
            result = self.adapter(base).create_draft_sales_order(CUSTOMER, 'PO-TEST', [{'item_id': ITEM, 'quantity': '2', 'unit_price': '8'}], order_date='2026-11-06')
            self.assertEqual(result['status'], 'Draft')
            self.assertEqual(result['salesOrderLines'][0]['quantity'], 2)
            self.assertEqual([r[0] for r in state['requests']], ['POST', 'POST', 'GET'])
            self.assertEqual(state['requests'][0][4]['externalDocumentNumber'], 'PO-TEST')
            self.assertEqual(state['requests'][1][4]['lineType'], 'Item')
            self.assertEqual(state['requests'][2][2]['$expand'], ['salesOrderLines'])

    def test_etag_update_and_stale_write_is_not_retried(self):
        from erp_demo.business_central import BusinessCentralError
        with contract_server() as (base, state):
            adapter = self.adapter(base)
            updated = adapter.update_order(ORDER, {'externalDocumentNumber': 'PO-EDIT'}, 'W/"1"')
            self.assertEqual(updated['@odata.etag'], 'W/"2"')
            with self.assertRaises(BusinessCentralError) as failure:
                adapter.update_order(ORDER, {'externalDocumentNumber': 'PO-STALE'}, 'W/"old"')
            self.assertEqual(failure.exception.status, 412)
            self.assertEqual(len(state['requests']), 2)
            with self.assertRaises(ValueError):
                adapter.update_order(ORDER, {}, '*')

    def test_nextlink_cannot_exfiltrate_token_or_escape_company(self):
        for link in ['https://evil.example/steal', '//evil.example/steal', '/api/v2.0/companies(other)/items', '/api/v2.0/companies(' + COMPANY + ')/../secret']:
            with self.subTest(link=link), contract_server() as (base, state):
                state['next_link'] = link
                with self.assertRaises(ValueError):
                    self.adapter(base).search_items('oil')
                self.assertEqual(len(state['requests']), 1)

    def test_invalid_line_is_rejected_before_header_write(self):
        with contract_server() as (base, state):
            adapter = self.adapter(base)
            for bad in ['NaN', '-1', '0', 'Infinity']:
                with self.subTest(quantity=bad), self.assertRaises(ValueError):
                    adapter.create_draft_sales_order(CUSTOMER, 'PO-TEST', [{'item_id': ITEM, 'quantity': bad, 'unit_price': '8'}])
            self.assertEqual(state['requests'], [])

    def test_paging_loop_rejected(self):
        with contract_server() as (base, state):
            state['next_link'] = base + '/companies(' + COMPANY + ')/items?$filter=contains%28number%2C%27oil%27%29'
            with self.assertRaises(ValueError):
                self.adapter(base).search_items('oil')
            self.assertEqual(len(state['requests']), 1)

    def test_partial_order_reports_header_id_and_no_retry(self):
        from erp_demo.business_central import PartialOrderError
        with contract_server() as (base, state):
            state['fail_line'] = True
            with self.assertRaises(PartialOrderError) as failure:
                self.adapter(base).create_draft_sales_order(CUSTOMER, 'PO-TEST', [{'item_id': ITEM, 'quantity': 2, 'unit_price': 8}])
            self.assertEqual(failure.exception.order_id, ORDER)
            self.assertEqual([r[0] for r in state['requests']], ['POST', 'POST'])

    def test_redirect_is_rejected_without_forwarding_token(self):
        from erp_demo.business_central import BusinessCentralError
        with contract_server() as (base, state):
            state['redirect'] = True
            with self.assertRaises(BusinessCentralError) as failure:
                self.adapter(base).search_items('oil')
            self.assertEqual(failure.exception.status, 302)
            self.assertEqual(len(state['requests']), 1)

    def test_whitespace_wildcard_etag_never_writes(self):
        with contract_server() as (base, state):
            with self.assertRaises(ValueError):
                self.adapter(base).update_order(ORDER, {'externalDocumentNumber':'PO'}, ' * ')
            self.assertEqual(state['requests'], [])

    def test_transport_and_identifiers_are_validated(self):
        from erp_demo.business_central import BusinessCentral
        for base in ['http://remote.example/api/v2.0', 'https://user:password@example.com/api/v2.0', 'https://example.com/odata']:
            with self.subTest(base=base), self.assertRaises(ValueError):
                BusinessCentral(base, COMPANY, 'test')
        with self.assertRaises(ValueError):
            BusinessCentral('https://example.com/api/v2.0', "bad)/items", 'test')


if __name__ == '__main__':
    unittest.main()
