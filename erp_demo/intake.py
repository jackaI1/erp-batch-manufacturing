"""Grounded order intake. Documents and model output are untrusted proposals."""
from datetime import date, timedelta
from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from pypdf import PdfReader

from .intake_review import review_order
from .workflow import COMPANY


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class SourceField(StrictModel):
    value: StrictStr = Field(min_length=1, max_length=200)
    page: StrictInt = Field(ge=1)
    text: StrictStr = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1, strict=True)


class Line(StrictModel):
    item: SourceField
    quantity: SourceField
    unit_price: SourceField


class Extraction(StrictModel):
    customer: SourceField
    purchase_order: SourceField
    currency: SourceField
    lines: list[Line] = Field(min_length=1, max_length=100)


def load_document(path):
    """Read text PDFs and plain email; attachments receive consecutive page IDs.

    A scanned/image-only PDF is held for transcription, never silently skipped.
    Limits bound memory, not adversarial PDF parser isolation; use synthetic files.
    """
    path = Path(path)
    if path.stat().st_size > 5_000_000:
        raise ValueError('Document exceeds 5 MB limit')
    pages = {}

    def add_pdf(data):
        pdf = PdfReader(BytesIO(data))
        if pdf.is_encrypted or len(pdf.pages) > 50:
            raise ValueError('Encrypted PDF or more than 50 pages; manual review required')
        for page in pdf.pages:
            text = page.extract_text() or ''
            if not text.strip():
                raise ValueError('Image-only PDF page; manual transcription required')
            pages[len(pages) + 1] = text

    if path.suffix.lower() == '.pdf':
        add_pdf(path.read_bytes())
    elif path.suffix.lower() == '.eml':
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        for part in message.walk():
            if part.get_content_type() == 'application/pdf':
                add_pdf(part.get_payload(decode=True))
            elif part.get_content_type() == 'text/plain':
                text = part.get_content()
                if text.strip():
                    pages[len(pages) + 1] = text
        if not pages:
            raise ValueError('Email has no plain text or PDF; manual review required')
    else:
        raise ValueError('Only .eml and .pdf are supported')
    if sum(map(len, pages.values())) > 100_000 or len(pages) > 50:
        raise ValueError('Source too long; split and review manually')
    return pages


def review(data, pages, erp):
    try:
        extraction = Extraction.model_validate(data)
    except ValueError as exc:
        return {'status': 'review', 'issues': ['schema: ' + str(exc)], 'order': data}
    order = extraction.model_dump()
    customer = erp.get_customer(extraction.customer.value)
    catalog = {}
    issues = []
    for line in extraction.lines:
        matches = [x for x in erp.search_items(line.item.value) if x['item_code'] == line.item.value]
        if len(matches) == 1 and matches[0].get('price') is not None:
            catalog[line.item.value] = matches[0]['price']
            if matches[0].get('currency') != extraction.currency.value:
                issues.append(f'{line.item.value}: ERP price currency conflict')
        else:
            issues.append(f'{line.item.value}: unknown item or ambiguous/missing ERP price')
    issues += review_order(order, pages, {extraction.customer.value} if customer else set(), catalog)
    # Supported headers are exact field values, not arbitrary substrings of IDs.
    for key, header in [('customer', 'Customer'), ('purchase_order', 'PO'), ('currency', 'Currency')]:
        candidates = {match.strip() for text in pages.values()
                      for match in re.findall(r'^' + header + r':\s*([^\n\r]+)', text, re.MULTILINE)}
        if candidates != {getattr(extraction, key).value}:
            issues.append(key + ': source header mismatch or ambiguity; manual review required')
    currency = extraction.currency
    if currency.text not in pages.get(currency.page, '') or currency.value not in currency.text or currency.confidence < .9:
        issues.append('currency: unverifiable source or low confidence')
    if customer and customer.get('currency') != currency.value:
        issues.append('customer: currency conflict')
    # Whole numeric token matching stops "2" from being grounded in "12.50".
    for i, line in enumerate(extraction.lines):
        for name in ('quantity', 'unit_price'):
            field = getattr(line, name)
            if not re.search(r'(?<![\w.,+-])' + re.escape(field.value) + r'(?![\w.,])', field.text):
                issues.append(f'lines[{i}].{name}: numeric token not grounded')
    # Independent completeness check for the supported synthetic pipe-table format.
    source_rows = [line.strip() for text in pages.values() for line in text.splitlines()
                   if '|' in line and [x.strip().lower() for x in line.split('|')]
                   != ['item', 'quantity', 'unit price']]
    if not source_rows or len(source_rows) != len(extraction.lines):
        issues.append('source line count cannot be reconciled; manual review required')
    else:
        # Bind all three fields to the same source row, not merely any page token.
        for i, (line, row) in enumerate(zip(extraction.lines, source_rows)):
            if [line.item.value, line.quantity.value, line.unit_price.value] != [x.strip() for x in row.split('|')]:
                issues.append(f'lines[{i}]: source row mismatch')
    return {'status': 'review' if issues else 'ready', 'issues': issues, 'order': order}


def draft_order(data, pages, erp):
    checked = review(data, pages, erp)
    if checked['issues']:
        return checked
    order = Extraction.model_validate(data)
    draft = erp.create_draft({
        'customer': order.customer.value, 'po_no': order.purchase_order.value,
        'currency': order.currency.value, 'company': COMPANY, 'docstatus': 0,
        'transaction_date': date.today().isoformat(),
        'delivery_date': (date.today() + timedelta(days=14)).isoformat(),
        'items': [{'item_code': line.item.value, 'qty': float(line.quantity.value),
                   'rate': float(line.unit_price.value)} for line in order.lines]})
    return {'status': 'draft', 'issues': [], 'document': draft}


class ERPNextOrders:
    """Only writes drafts. Explicit CAD selling price list prevents price guessing."""
    def __init__(self, api):
        self.api = api

    def get_customer(self, code):
        found = self.api.list('Customer', {'name': code, 'disabled': 0},
                              ['name', 'customer_name', 'default_currency'])
        if len(found) != 1:
            return None
        return {**found[0], 'currency': found[0].get('default_currency')}

    def search_items(self, query):
        result = []
        for item in self.api.list('Item', {'item_code': ['like', '%' + query + '%'], 'disabled': 0,
                                           'is_sales_item': 1}, ['item_code', 'item_name', 'stock_uom']):
            prices = self.api.list('Item Price', {'item_code': item['item_code'], 'price_list': 'Synthetic CAD'},
                                   ['price_list_rate', 'currency', 'valid_from', 'valid_upto', 'uom', 'customer'])
            today = date.today().isoformat()
            prices = [p for p in prices if not p.get('customer') and p.get('uom') == item['stock_uom']
                      and (not p.get('valid_from') or p['valid_from'] <= today)
                      and (not p.get('valid_upto') or p['valid_upto'] >= today)]
            result.append({**item, 'uom': item['stock_uom'],
                           'price': str(prices[0]['price_list_rate']) if len(prices) == 1 else None,
                           'currency': prices[0]['currency'] if len(prices) == 1 else None})
        return result

    def create_draft(self, data):
        previous = self.api.list('Sales Order', {'customer': data['customer'], 'po_no': data['po_no'],
                                                 'docstatus': ['!=', 2]})
        if previous:
            raise ValueError('Purchase order already exists; inspect it before retrying')
        return self.api.create('Sales Order', selling_price_list='Synthetic CAD',
                               price_list_currency='CAD', **data)


def setup_orders(api):
    # This demo bypasses the setup wizard, which normally creates sales roots.
    api.ensure('Customer Group', 'All Customer Groups', customer_group_name='All Customer Groups', is_group=1)
    api.ensure('Territory', 'All Territories', territory_name='All Territories', is_group=1)
    api.ensure('Customer Group', 'Synthetic customers', customer_group_name='Synthetic customers',
               parent_customer_group='All Customer Groups')
    api.ensure('Customer', 'SYN-CUSTOMER', customer_name='SYN-CUSTOMER', customer_type='Company',
               customer_group='Synthetic customers', territory='All Territories', default_currency='CAD')
    api.ensure('Price List', 'Synthetic CAD', price_list_name='Synthetic CAD', currency='CAD', selling=1)
    for code, rate in [('FG-LOTION', 12.5), ('FG-BALM', 15)]:
        prices = api.list('Item Price', {'item_code': code, 'price_list': 'Synthetic CAD'})
        if not prices:
            api.create('Item Price', item_code=code, price_list='Synthetic CAD', price_list_rate=rate,
                       currency='CAD', uom='Kg')
