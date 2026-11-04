import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlparse, parse_qs

from erp_demo.api import Client, ERPError
from erp_demo.__main__ import init
from erp_demo.workflow import positive_number, manufacture, reconcile


class QuantityValidation(unittest.TestCase):
    def test_invalid_quantities_are_rejected_before_network_calls(self):
        for value in (-1, 0, math.inf, -math.inf, math.nan):
            with self.subTest(value=value), self.assertRaises(ValueError):
                manufacture(None, "lotion", value, "A", "demo")
        for value in (-1, math.inf, math.nan):
            with self.subTest(value=value), self.assertRaises(ValueError):
                reconcile(None, "RM-OIL-A", value)

    def test_invalid_ph_rejected_before_network_calls(self):
        for ph in (math.inf, math.nan, -1, 15):
            with self.subTest(ph=ph), self.assertRaises(ValueError):
                manufacture(None, "lotion", 10, "A", "test", ph=ph)

    def test_fractional_and_zero_physical_counts(self):
        self.assertEqual(positive_number("0.125"), 0.125)
        self.assertEqual(positive_number(0, allow_zero=True), 0)

    def test_invalid_formula_lot_and_run_rejected_before_network_calls(self):
        for formula, lot, run in [("unknown", "A", "test"), ("balm", "C", "test"),
                                  ("balm", "A", "../oops"), ("balm", "A", "")]:
            with self.subTest(formula=formula, lot=lot, run=run), self.assertRaises(ValueError):
                manufacture(None, formula, 10, lot, run)


class CredentialTests(unittest.TestCase):
    def test_init_preserves_existing_credentials_and_file_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            init(path)
            original = path.read_text()
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(len(original.splitlines()), 2)
            with self.assertRaises(FileExistsError):
                init(path)
            self.assertEqual(path.read_text(), original)

    def test_remote_credentials_require_https(self):
        with self.assertRaises(ValueError):
            Client("http://example.com", token="private")
        with self.assertRaises(ValueError):
            Client("file:///tmp/test", token="private")

    def test_credentials_required(self):
        with self.assertRaises(ValueError):
            Client("http://localhost:8080")


class ClientHTTPTests(unittest.TestCase):
    """Transport contract only; manufacturing is tested on real ERPNext in CI."""
    def setUp(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path.endswith("/redirect"):
                    self.send_response(302)
                    self.send_header("Location", "/api/resource/Item/denied")
                    self.end_headers()
                    return
                if parsed.path.endswith("/denied"):
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b'{"exc_type":"PermissionError","secret":"never-print-this"}')
                    return
                if parsed.path.endswith("/missing"):
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b'{"exc_type":"DoesNotExistError"}')
                    return
                self.send_response(200)
                self.end_headers()
                if parsed.path == "/api/resource/Item":
                    start = int(parse_qs(parsed.query)["limit_start"][0])
                    data = [{"name": str(i)} for i in range(start, min(start + 200, 201))]
                else:
                    data = {"path": parsed.path, "authorization": self.headers.get("Authorization")}
                self.wfile.write(json.dumps({"data": data}).encode())

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"data": data}).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever)
        self.thread.start()
        self.api = Client(f"http://127.0.0.1:{self.server.server_port}", token="example:test")
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_all_pages_are_fetched(self):
        rows = self.api.list("Item")
        self.assertEqual(len(rows), 201)
        self.assertEqual(rows[-1]["name"], "200")

    def test_resource_names_are_encoded_as_one_path_segment(self):
        result = self.api.get("Work Order", "LOT/1")
        self.assertEqual(result["path"], "/api/resource/Work%20Order/LOT%2F1")
        self.assertEqual(result["authorization"], "token example:test")

    def test_redirects_are_not_followed_with_credentials(self):
        with self.assertRaises(ERPError) as context:
            self.api.get("Item", "redirect")
        self.assertEqual(context.exception.status, 302)

    def test_errors_hide_response_body(self):
        with self.assertRaises(ERPError) as context:
            self.api.get("Item", "denied")
        self.assertEqual(context.exception.status, 403)
        self.assertNotIn("never-print-this", str(context.exception))

    def test_seed_does_not_treat_access_denied_as_missing(self):
        with self.assertRaises(ERPError) as context:
            self.api.ensure("Item", "denied", item_code="replacement")
        self.assertEqual(context.exception.status, 403)

    def test_missing_master_can_be_created(self):
        result = self.api.ensure("Item", "missing", item_code="new")
        self.assertEqual(result["item_code"], "new")
