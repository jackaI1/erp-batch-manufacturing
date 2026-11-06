"""Actual draft creation through official MCP client and pinned ERPNext REST."""
import asyncio
import json
import os
from pathlib import Path
import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from erp_demo.api import Client
from erp_demo.intake import setup_orders
from erp_demo.workflow import seed

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.getenv('ERP_INTEGRATION') == '1','requires real ERPNext')
class IntakeIntegration(unittest.TestCase):
    def test_mcp_creates_real_draft_and_holds_bad_price(self):
        api = Client.from_env()
        from erp_demo.api import ERPError
        original_request = api.request
        def diagnostic(method, path, *args, **kwargs):
            try:
                return original_request(method, path, *args, **kwargs)
            except ERPError as error:
                if error.status != 404:
                    print(f'Synthetic intake diagnostic: {method} {path}: {error.server_messages}', flush=True)
                raise
        api.request = diagnostic
        seed(api)
        setup_orders(api)
        record = json.loads((ROOT/'examples/intake/recordings/clean.json').read_text())
        data = record['response']['content'][0]['input']

        async def run():
            parameters = StdioServerParameters(command=sys.executable,args=['-m','erp_demo.mcp_server'],
                         env={**os.environ,'INTAKE_INBOX':str(ROOT/'examples/intake')},cwd=str(ROOT))
            async with stdio_client(parameters) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    result = await client.call_tool('create_draft_sales_order',{'document':'clean.eml','extraction':data})
                    self.assertFalse(result.isError, str(result))
                    draft = json.loads(result.content[0].text)
                    self.assertEqual(draft['status'],'draft',draft)
                    actual = api.get('Sales Order',draft['document']['name'])
                    self.assertEqual(actual['docstatus'],0)
                    self.assertEqual(actual['customer'],'SYN-CUSTOMER')
                    self.assertEqual(actual['po_no'],'SYN-PO-1')
                    self.assertEqual(actual['items'][0]['qty'],2)
                    self.assertEqual(actual['items'][0]['rate'],12.5)
                    again = await client.call_tool('create_draft_sales_order',{'document':'clean.eml','extraction':data})
                    self.assertTrue(again.isError)
                    data['lines'][0]['unit_price']['value']='0.01'
                    held = await client.call_tool('create_draft_sales_order',{'document':'clean.eml','extraction':data})
                    self.assertEqual(json.loads(held.content[0].text)['status'],'review')
                    self.assertEqual(len(api.list('Sales Order',{'po_no':'SYN-PO-1'})),1)
                    (ROOT/'artifacts').mkdir(exist_ok=True)
                    (ROOT/'artifacts/intake-draft.json').write_text(json.dumps(actual,indent=2))
        asyncio.run(run())
