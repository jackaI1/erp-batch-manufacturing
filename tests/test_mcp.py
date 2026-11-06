"""Real SDK stdio session and HTTP ERP contract; no server left behind."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from urllib.parse import urlparse, unquote

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


class ERPContract(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        doctype = unquote(urlparse(self.path).path).split('/')[-1]
        data = {'Customer': [{'name':'SYN-CUSTOMER','default_currency':'CAD'}],
                'Item': [{'item_code':'FG-LOTION','stock_uom':'Kg'}],
                'Item Price': [{'price_list_rate':12.5,'currency':'CAD','uom':'Kg'}],
                'Sales Order': []}[doctype]
        self.respond({'data':data})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.writes.append(body)
        self.respond({'data':{'name':'SYN-SO-1',**body}})
    def respond(self, data):
        payload = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def contract_server():
    with ThreadingHTTPServer(('127.0.0.1',0), ERPContract) as server:
        server.writes = []
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try: yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive(), 'owned contract fixture failed to stop'


async def session(url):
    env = {**os.environ, 'ERP_URL':url, 'ERP_TOKEN':'synthetic-contract-token',
           'INTAKE_INBOX':str(ROOT/'examples/intake')}
    params = StdioServerParameters(command=sys.executable, args=['-m','erp_demo.mcp_server'],env=env,cwd=str(ROOT))
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as client:
            await client.initialize()
            tools = await client.list_tools()
            assert {t.name for t in tools.tools} == {'search_items','get_customer','create_draft_sales_order'}
            result = await client.call_tool('search_items',{'query':'FG-LOTION'})
            assert not result.isError, result
            customer = await client.call_tool('get_customer',{'code':'SYN-CUSTOMER'})
            assert not customer.isError, customer
            record = json.loads((ROOT/'examples/intake/recordings/clean.json').read_text())
            data = record['response']['content'][0]['input']
            draft = await client.call_tool('create_draft_sales_order',{'document':'clean.eml','extraction':data})
            assert not draft.isError, draft
            body = json.loads(draft.content[0].text)
            assert body['status'] == 'draft', body
            blocked = await client.call_tool('create_draft_sales_order',{'document':'../README.md','extraction':data})
            assert blocked.isError, blocked
            data['lines'][0]['unit_price']['value'] = '0.01'
            held = await client.call_tool('create_draft_sales_order',{'document':'clean.eml','extraction':data})
            assert json.loads(held.content[0].text)['status'] == 'review', held
            return body


class MCPTests(unittest.TestCase):
    def test_real_sdk_session_writes_only_grounded_draft_and_closes(self):
        with contract_server() as server:
            result = asyncio.run(session(f'http://127.0.0.1:{server.server_port}'))
            self.assertEqual(result['document']['docstatus'],0)
            self.assertEqual(len(server.writes),1)
            self.assertEqual(server.writes[0]['po_no'],'SYN-PO-1')
