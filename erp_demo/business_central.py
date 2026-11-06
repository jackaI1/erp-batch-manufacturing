"""Business Central API v2.0 adapter, verified against a local HTTP contract.

This module does not imply access to, or validation against, a live tenant.
Writes are deliberately never retried: inspect a returned partial-order ID after
failure before deciding whether to complete or remove the draft.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import os
from urllib.error import HTTPError
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse
from urllib.request import Request, build_opener
from uuid import UUID

from .api import NoRedirect


class BusinessCentralError(RuntimeError):
    """Safe HTTP status only; response bodies can contain customer data."""
    def __init__(self, status):
        self.status = status
        super().__init__(f'Business Central HTTP {status}; request was not retried')


class PartialOrderError(RuntimeError):
    def __init__(self, order_id):
        self.order_id = order_id
        super().__init__(f'Draft {order_id} may be incomplete; inspect it before retrying')


def _guid(value):
    return str(UUID(str(value)))


def _literal(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 250 or any(ord(c) < 32 for c in value):
        raise ValueError('Expected nonempty text, at most 250 characters')
    return "'" + value.replace("'", "''") + "'"


def _number(value, *, positive):
    if isinstance(value, bool):
        raise ValueError('Expected a finite numeric quantity or price')
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (positive and result == 0) or result > Decimal('1e12'):
            raise ValueError
        return float(result)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('Expected a finite positive quantity or nonnegative price') from None


class BusinessCentral:
    """Use a base ending /api/v2.0 and a company GUID, not a company name.

    HTTPS is required except for the localhost contract server. Bearer tokens are
    supplied by the caller; this adapter does not mint or persist OAuth tokens.
    """
    def __init__(self, base_url, company_id, token):
        parsed = urlparse(base_url)
        if (parsed.scheme not in ('https', 'http') or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                not parsed.path.rstrip('/').endswith('/api/v2.0') or
                (parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'))):
            raise ValueError('Use an HTTPS API v2.0 base URL (HTTP only for localhost)')
        if not token or any(c in token for c in '\r\n'):
            raise ValueError('A bearer token is required')
        self.base_url = base_url.rstrip('/')
        self.company_url = self.base_url + '/companies(' + _guid(company_id) + ')'
        self.origin = (parsed.scheme, parsed.hostname, parsed.port)
        self.token = token
        self.opener = build_opener(NoRedirect())

    @classmethod
    def from_env(cls):
        return cls(os.environ['BC_API_URL'], os.environ['BC_COMPANY_ID'], os.environ['BC_TOKEN'])

    def _request(self, method, url, body=None, etag=None):
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json',
                   'Content-Type': 'application/json', 'Prefer': 'odata.maxpagesize=200'}
        if etag:
            headers['If-Match'] = etag
        request = Request(url, method=method, headers=headers,
                          data=json.dumps(body, allow_nan=False).encode() if body is not None else None)
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read(8 * 1024 * 1024 + 1)
                if len(data) > 8 * 1024 * 1024:
                    raise ValueError('Business Central page exceeded 8 MiB')
                result = json.loads(data)
                if response.headers.get('ETag') and '@odata.etag' not in result:
                    result['@odata.etag'] = response.headers['ETag']
                return result
        except HTTPError as error:
            status = error.code
            error.close()
            raise BusinessCentralError(status) from None

    def _collection(self, resource, params):
        collection_url = self.company_url + '/' + resource
        url = collection_url + '?' + urlencode(params)
        seen, rows = set(), []
        for _ in range(100):
            parsed_url = urlparse(url)
            identity = (parsed_url.path, tuple(sorted(parse_qsl(parsed_url.query))))
            if identity in seen:
                raise ValueError('Business Central returned a paging loop')
            seen.add(identity)
            page = self._request('GET', url)
            if not isinstance(page.get('value'), list):
                raise ValueError('Business Central collection is missing value rows')
            rows.extend(page['value'])
            next_link = page.get('@odata.nextLink')
            if not next_link:
                return rows
            candidate = urljoin(url, next_link)
            parsed = urlparse(candidate)
            # Pagination is only allowed on the exact collection we requested.
            # Reject encoded traversal too, and never forward a token to another host.
            if ((parsed.scheme, parsed.hostname, parsed.port) != self.origin or
                    parsed.username or parsed.password or parsed.fragment or
                    unquote(parsed.path) != urlparse(collection_url).path):
                raise ValueError('Unsafe Business Central nextLink')
            url = candidate
        raise ValueError('Business Central exceeded 100 pages; narrow the filter')

    def search_items(self, query):
        return self._collection('items', {'$filter': 'contains(number,' + _literal(query) + ')'})

    def get_customer(self, number):
        rows = self._collection('customers', {'$filter': 'number eq ' + _literal(number)})
        # Do not trust a partial or ambiguous match from any upstream API.
        matches = [row for row in rows if row.get('number') == number]
        if len(matches) > 1:
            raise ValueError('Ambiguous customer number; human review required')
        return matches[0] if matches else None

    def get_order(self, order_id):
        return self._request('GET', self.company_url + '/salesOrders(' + _guid(order_id) + ')?' +
                             urlencode({'$expand': 'salesOrderLines'}))

    def create_draft_sales_order(self, customer_id, purchase_order, lines, *, order_date=None):
        """Create an unposted header and lines. Review fields before calling.

        API header/line requests are separate transactions. A partial failure
        raises PartialOrderError with the draft ID; no automatic compensation or
        retry risks overwriting a person's concurrent changes.
        """
        customer_id = _guid(customer_id)
        _literal(purchase_order)
        if not isinstance(lines, list) or not lines:
            raise ValueError('At least one reviewed line is required')
        validated = []
        for line in lines:
            validated.append({'itemId': _guid(line['item_id']), 'lineType': 'Item',
                              'quantity': _number(line['quantity'], positive=True),
                              'unitPrice': _number(line['unit_price'], positive=False)})
        order_date = date.fromisoformat(order_date).isoformat() if order_date else date.today().isoformat()
        header = self._request('POST', self.company_url + '/salesOrders', {
            'customerId': customer_id, 'externalDocumentNumber': purchase_order, 'orderDate': order_date})
        order_id = _guid(header['id'])
        try:
            for line in validated:
                self._request('POST', self.company_url + '/salesOrders(' + order_id + ')/salesOrderLines', line)
            return self.get_order(order_id)
        except Exception as error:
            raise PartialOrderError(order_id) from error

    def update_order(self, order_id, fields, etag):
        if not isinstance(etag, str) or not etag.strip() or etag.strip() == '*' or any(c in etag for c in '\r\n'):
            raise ValueError('Supply the exact ETag from get_order; wildcard writes are disabled')
        if not isinstance(fields, dict) or not fields:
            raise ValueError('Provide the changed order fields')
        return self._request('PATCH', self.company_url + '/salesOrders(' + _guid(order_id) + ')', fields, etag)
