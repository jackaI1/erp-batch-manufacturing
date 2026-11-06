"""Source document -> recorded real model output -> MCP -> OData draft contract."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import unittest
from urllib.parse import parse_qs, urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from erp_demo.intake import load_document
from erp_demo.models import ReplayAdapter

ROOT = Path(__file__).resolve().parents[1]
COMPANY = '00000000-0000-0000-0000-000000000001'
CUSTOMER = '00000000-0000-0000-0000-000000000002'
ITEM = '00000000-0000-0000-0000-000000000003'
ORDER = '00000000-0000-0000-0000-000000000004'
PREFIX = '/api/v2.0/companies(' + COMPANY + ')'


@contextmanager
def odata_contract():
    state = dict(requests=[], drafts=[], lines=[], rate=12.5)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def respond(self,body,status=200):
            payload=json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def handle_request(self):
            parsed=urlparse(self.path)
            query=parse_qs(parsed.query)
            body=json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.headers.get('Content-Length') else None
            state['requests'].append((self.command,parsed.path,query,body))
            if self.headers.get('Authorization') != 'Bearer synthetic-odata-token':
                return self.respond({},401)
            path=parsed.path
            if self.command=='GET' and path==PREFIX+'/customers':
                rows=[dict(id=CUSTOMER,number='SYN-CUSTOMER',blocked=' ',currencyCode='')]
                return self.respond({'value':rows})
            if self.command=='GET' and path==PREFIX+'/items':
                query_text=query.get('$filter',[''])[0]
                rows=([dict(id=ITEM,number='FG-LOTION',blocked=False,baseUnitOfMeasureCode='Kg',
                            unitPrice=state['rate'],priceIncludesTax=False)] if 'FG-LOTION' in query_text else [])
                return self.respond({'value':rows})
            if self.command=='GET' and path==PREFIX+'/salesOrders':
                return self.respond({'value':state['drafts']})
            if self.command=='POST' and path==PREFIX+'/salesOrders':
                data=dict(id=ORDER,status='Draft',pricesIncludeTax=False,**body)
                state['drafts'].append(data)
                return self.respond(data,201)
            if self.command=='POST' and path==PREFIX+'/salesOrders('+ORDER+')/salesOrderLines':
                state['lines'].append(body)
                return self.respond(dict(id=ITEM,**body),201)
            if self.command=='GET' and path==PREFIX+'/salesOrders('+ORDER+')':
                return self.respond(dict(state['drafts'][0],salesOrderLines=state['lines'],**{'@odata.etag':'W/"1"'}))
            return self.respond({},404)
        do_GET=handle_request
        do_POST=handle_request
    with ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
        port=server.server_port
        state['ownership']={'owner':'test_mcp_odata','pid':os.getpid(),'started_at':datetime.now(timezone.utc).isoformat(),'port':port,
                            'cleanup':'server.shutdown(); thread.join(); server.server_close()'}
        thread=threading.Thread(target=server.serve_forever)
        thread.start()
        try: yield f'http://127.0.0.1:{port}/api/v2.0',state
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1',port)) != 0


class ODataMCPTests(unittest.TestCase):
    def test_actual_model_recording_is_reviewed_through_mcp_before_odata_write(self):
        async def exercise(url,state):
            env={**os.environ,'INTAKE_ERP':'business-central','BC_API_URL':url,'BC_COMPANY_ID':COMPANY,
                 'BC_TOKEN':'synthetic-odata-token','BC_BASE_CURRENCY':'CAD',
                 'INTAKE_INBOX':str(ROOT/'examples/intake')}
            params=StdioServerParameters(command=sys.executable,args=['-m','erp_demo.mcp_server'],env=env,cwd=str(ROOT))
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    tools=await client.list_tools()
                    self.assertEqual({x.name for x in tools.tools},{'search_items','get_customer','create_draft_sales_order'})
                    for name in ['unknown','price','weak']:
                        data=ReplayAdapter(ROOT/'examples/intake/recordings'/(name+'.json')).extract(load_document(ROOT/'examples/intake'/(name+'.eml')))
                        held=await client.call_tool('create_draft_sales_order',{'document':name+'.eml','extraction':data.model_dump()})
                        self.assertFalse(held.isError,str(held))
                        self.assertEqual(json.loads(held.content[0].text)['status'],'review')
                        self.assertEqual(state['drafts'],[])
                    data=ReplayAdapter(ROOT/'examples/intake/recordings/ollama-live-clean.json').extract(load_document(ROOT/'examples/intake/clean.eml'))
                    result=await client.call_tool('create_draft_sales_order',{'document':'clean.eml','extraction':data.model_dump()})
                    self.assertFalse(result.isError,str(result))
                    decoded=json.loads(result.content[0].text)
                    self.assertEqual(decoded['status'],'draft',decoded)
                    self.assertEqual(decoded['document']['status'],'Draft')
                    self.assertEqual(decoded['document']['id'],ORDER)
                    repeated=await client.call_tool('create_draft_sales_order',{'document':'clean.eml','extraction':data.model_dump()})
                    self.assertTrue(repeated.isError)
        with odata_contract() as (url,state):
            asyncio.run(exercise(url,state))
            self.assertEqual(len(state['drafts']),1)
            self.assertEqual(state['drafts'][0]['customerId'],CUSTOMER)
            self.assertEqual(state['drafts'][0]['externalDocumentNumber'],'SYN-PO-1')
            self.assertEqual(state['drafts'][0]['currencyCode'],'')
            self.assertNotIn('company',state['drafts'][0])
            self.assertNotIn('docstatus',state['drafts'][0])
            self.assertEqual(state['lines'],[{'itemId':ITEM,'lineType':'Item','quantity':2.0,'unitPrice':12.5,'unitOfMeasureCode':'Kg'}])
            self.assertTrue(all(r[1].startswith(PREFIX) for r in state['requests']))
            self.assertEqual(sum(r[0]=='POST' for r in state['requests']),2)
            self.assertTrue(any('$expand' in r[2] for r in state['requests']))

    def test_cli_selects_reviewed_odata_destination_and_holds_conflicts(self):
        with odata_contract() as (url,state):
            env={**os.environ,'INTAKE_ERP':'erpnext','BC_API_URL':url,'BC_COMPANY_ID':COMPANY,
                 'BC_TOKEN':'synthetic-odata-token','BC_BASE_CURRENCY':'CAD'}
            command=[sys.executable,'-m','erp_demo.intake_cli','examples/intake/clean.eml',
                     '--erp','business-central','--replay','examples/intake/recordings/ollama-live-clean.json',
                     '--create-draft']
            state['rate']=13
            held=subprocess.run(command,cwd=ROOT,env=env,check=True,capture_output=True,text=True,timeout=30)
            self.assertEqual(json.loads(held.stdout)['status'],'review')
            self.assertEqual(state['drafts'],[])
            state['rate']=12.5
            created=subprocess.run(command,cwd=ROOT,env=env,check=True,capture_output=True,text=True,timeout=30)
            self.assertEqual(json.loads(created.stdout)['document']['status'],'Draft')
            self.assertEqual(len(state['drafts']),1)

    def test_unknown_erp_configuration_does_not_fall_back(self):
        from erp_demo.order_backends import from_env
        with self.assertRaisesRegex(ValueError,'INTAKE_ERP'):
            from_env('unknown')
