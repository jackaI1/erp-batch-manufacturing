"""Small Frappe REST client. Mutations are never retried automatically."""
import json
import os
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener


class ERPError(RuntimeError):
    """ERPNext rejected a request. Response bodies may contain private data."""

    def __init__(self, status, kind):
        self.status = status
        super().__init__(f"ERPNext HTTP {status}: {kind}")


class Client:
    def __init__(self, url, user=None, password=None, token=None):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("ERP_URL must be an http(s) URL")
        if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("Use HTTPS for remote ERPNext sites")
        self.url = url.rstrip("/")
        self.token = token
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        if not token:
            if not user or not password:
                raise ValueError("Set ERP_PASSWORD or ERP_TOKEN; see README")
            self.call("login", {"usr": user, "pwd": password})

    @classmethod
    def from_env(cls):
        return cls(os.getenv("ERP_URL", "http://127.0.0.1:8080"),
                   os.getenv("ERP_USER", "Administrator"), os.getenv("ERP_PASSWORD"),
                   os.getenv("ERP_TOKEN"))

    def request(self, method, path, data=None, params=None):
        url = self.url + "/api/" + path
        if params:
            url += "?" + urlencode(params)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "token " + self.token
        request = Request(url, data=json.dumps(data).encode() if data is not None else None,
                          method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=120) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                kind = json.loads(exc.read()).get("exc_type", "Request rejected")
            except (ValueError, AttributeError):
                kind = "Request rejected"
            raise ERPError(exc.code, kind) from None

    def call(self, method, args=None):
        return self.request("POST", "method/" + method, args or {}).get("message")

    def get(self, doctype, name):
        return self.request("GET", f"resource/{quote(doctype, safe='')}/{quote(name, safe='')}")["data"]

    def list(self, doctype, filters=None, fields=None):
        rows, start = [], 0
        while True:
            page = self.request("GET", "resource/" + quote(doctype, safe=""), params={
                "filters": json.dumps(filters or {}), "fields": json.dumps(fields or ["name"]),
                "limit_page_length": 200, "limit_start": start, "order_by": "creation asc"})["data"]
            rows.extend(page)
            if len(page) < 200:
                return rows
            start += len(page)

    def create(self, doctype, **fields):
        return self.request("POST", "resource/" + quote(doctype, safe=""), fields)["data"]

    def update(self, doctype, name, **fields):
        return self.request("PUT", f"resource/{quote(doctype, safe='')}/{quote(name, safe='')}", fields)["data"]

    def submit(self, doc):
        return self.call("frappe.client.submit", {"doc": doc})

    def ensure(self, doctype, name, **fields):
        try:
            return self.get(doctype, name)
        except ERPError as exc:
            if exc.status != 404:
                raise
        return self.create(doctype, **fields)
