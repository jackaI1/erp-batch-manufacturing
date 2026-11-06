"""Real loopback HTTP round trip: session headers, paging and logout."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest
from urllib.error import HTTPError
from maintenance.client import Client, APIError


class ClientTests(unittest.TestCase):
    def test_session_pagination_and_logout(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def respond(self, data):
                body = json.dumps({'success': True, 'data': data}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(('login', payload['scope']))
                self.respond({'_id': 'test-session'})
            def do_GET(self):
                self.assert_auth()
                from urllib.parse import parse_qs, urlsplit
                start = int(parse_qs(urlsplit(self.path).query)['start'][0])
                self.respond([{'Code': str(i)} for i in range(start, min(start + 100, 101))])
            def assert_auth(self):
                calls.append(('header', self.headers.get('CMDBuild-Authorization')))
            def do_DELETE(self):
                calls.append(('logout', self.path))
                self.respond(None)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with Client('http://127.0.0.1:' + str(server.server_port), 'synthetic', 'synthetic') as api:
                rows = api.cards('FleetAsset')
                self.assertEqual(len(rows), 101)
                self.assertEqual(rows[-1]['Code'], '100')
            self.assertEqual(calls[0], ('login', 'service'))
            self.assertEqual(calls[1:3], [('header', 'test-session')] * 2)
            self.assertTrue(calls[-1][1].endswith('/sessions/test-session'))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def test_remote_plaintext_rejected(self):
        with self.assertRaises(ValueError):
            Client('http://example.com/cmdbuild', 'a', 'b')
