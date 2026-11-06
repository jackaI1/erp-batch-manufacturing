"""CPU inference only in opt-in Linux CI; raw response retained as evidence."""
import json
import os
from pathlib import Path
import time
import unittest

from erp_demo.intake import load_document, review
from erp_demo.intake_benchmark import ReferenceCatalog
from erp_demo.models import ModelAdapter, decode_response, source_hash


@unittest.skipUnless(os.getenv('OLLAMA_INTEGRATION') == '1','requires real local Ollama')
class OllamaLive(unittest.TestCase):
    def test_real_model_tool_extraction_is_schema_valid_and_grounded_or_held(self):
        pages = load_document('examples/intake/clean.eml')
        adapter = ModelAdapter('ollama',model=os.environ.get('INTAKE_MODEL','qwen3:1.7b'))
        started = time.perf_counter()
        response = adapter._post(adapter.payload(pages))
        output = Path('artifacts/ollama')
        output.mkdir(parents=True,exist_ok=True)
        (output/'response.json').write_text(json.dumps(dict(backend='ollama',provenance='Real CPU model response in GitHub Actions',
                  model=adapter.model,source_sha256=source_hash(pages),response=response),indent=2))
        extraction = decode_response('ollama',response)
        decision = review(extraction.model_dump(),pages,ReferenceCatalog())
        (output/'review.json').write_text(json.dumps(dict(seconds=time.perf_counter()-started,**decision),indent=2))
        self.assertIn(decision['status'],['ready','review'])
        if decision['status']=='ready':
            self.assertEqual(extraction.customer.value,'SYN-CUSTOMER')
            self.assertEqual(extraction.lines[0].quantity.value,'2')
            self.assertEqual(extraction.lines[0].unit_price.value,'12.50')
