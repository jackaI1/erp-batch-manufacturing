"""CMDBuild REST v3 client used with a real pinned openMAINT application.

The fleet is an explicit custom data-model extension, not a bundled workflow.
"""
import json
import os
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class APIError(RuntimeError):
    def __init__(self, status, detail='request rejected'):
        self.status = status
        super().__init__(f'openMAINT HTTP {status}: {detail}')


class Client:
    def __init__(self, base, username, password):
        parsed = urlparse(base)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username:
            raise ValueError('Expected http(s) openMAINT URL without credentials')
        if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1'):
            raise ValueError('Remote openMAINT requires HTTPS')
        self.base = base.rstrip('/') + '/services/rest/v3'
        self.opener = build_opener(NoRedirect())
        self.session = None
        response = self.request('POST', 'sessions', dict(username=username, password=password, scope='service'), params={'scope': 'service', 'returnId': 'true'})
        self.session = response['data']['_id']

    @classmethod
    def from_env(cls):
        return cls(os.getenv('MAINT_URL', 'http://127.0.0.1:8090/cmdbuild'), os.getenv('MAINT_USER', 'admin'), os.getenv('MAINT_PASSWORD', 'admin'))

    def request(self, method, path, data=None, params=None):
        url = self.base + '/' + path
        if params:
            url += '?' + urlencode(params)
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if self.session:
            headers['CMDBuild-Authorization'] = self.session
        request = Request(url, method=method, headers=headers, data=None if data is None else json.dumps(data, allow_nan=False).encode())
        try:
            with self.opener.open(request, timeout=90) as response:
                payload = json.load(response)
        except HTTPError as error:
            # Never echo response bodies containing user records or credentials.
            raise APIError(error.code) from None
        if not payload.get('success', True):
            raise APIError(400, 'API reported failure')
        return payload

    def close(self):
        if self.session:
            token, self.session = self.session, None
            self.request('DELETE', 'sessions/' + quote(str(token), safe=''))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def cards(self, class_name):
        rows, offset = [], 0
        while True:
            response = self.request('GET', 'classes/' + quote(class_name, safe='') + '/cards', params={'limit': 100, 'start': offset})
            page = response['data']
            rows.extend(page)
            if len(page) < 100:
                return rows
            offset += len(page)

    def ensure_card(self, class_name, card):
        found = [r for r in self.cards(class_name) if r['Code'] == card['Code']]
        if len(found) > 1:
            raise ValueError('Duplicate card Code; review before writing')
        if found:
            return found[0]
        return self.request('POST', 'classes/' + quote(class_name, safe='') + '/cards', card)['data']
