"""Actual adapter wire contract on an owned loopback HTTP service."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest

from erp_demo.intake import load_document
from erp_demo.models import ModelAdapter, ReplayAdapter

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def endpoint(backend, response):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, {k.lower():v for k,v in self.headers.items()}, body))
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    with ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try: yield f'http://127.0.0.1:{server.server_port}', requests
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


class ModelHTTPTests(unittest.TestCase):
    def test_anthropic_messages_tool_and_ollama_chat_wire_formats(self):
        extraction = json.loads((ROOT/'examples/intake/recordings/clean.json').read_text())['response']['content'][0]['input']
        for backend,path in [('anthropic','/v1/messages'),('ollama','/api/chat')]:
            response = ({'content':[{'type':'tool_use','name':'extract_order','input':extraction}]} if backend=='anthropic'
                        else {'message':{'tool_calls':[{'function':{'name':'extract_order','arguments':extraction}}]}})
            with self.subTest(backend=backend), endpoint(backend,response) as (url,received):
                result = ModelAdapter(backend,model='synthetic-model',key='synthetic-key',url=url+path).extract({1:'source'})
                self.assertEqual(result.customer.value,'SYN-CUSTOMER')
                route,headers,payload = received[0]
                self.assertEqual(route,path)
                self.assertEqual(payload['model'],'synthetic-model')
                if backend=='anthropic':
                    self.assertEqual(headers['x-api-key'],'synthetic-key')
                    self.assertEqual(headers['anthropic-version'],'2023-06-01')
                    self.assertEqual(payload['tool_choice']['name'],'extract_order')
                else:
                    self.assertNotIn('x-api-key',headers)
                    self.assertFalse(payload['stream'])

    def test_real_recorded_flat_model_response_is_rejected(self):
        adapter = ReplayAdapter(ROOT/'examples/intake/recordings/ollama-invalid-flat.json')
        with self.assertRaises(ValueError):
            adapter.extract(load_document(ROOT/'examples/intake/clean.eml'))

    def test_successful_real_recording_replays_with_grounded_evidence(self):
        from erp_demo.intake import review
        from erp_demo.intake_benchmark import ReferenceCatalog
        pages = load_document(ROOT/'examples/intake/clean.eml')
        extracted = ReplayAdapter(ROOT/'examples/intake/recordings/ollama-live-clean.json').extract(pages)
        self.assertEqual(review(extracted.model_dump(),pages,ReferenceCatalog())['status'],'ready')
