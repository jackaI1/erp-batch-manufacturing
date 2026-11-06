"""Review extracted purchase-order fields against their source and ERP master data.

This module makes no model calls and performs no ERP writes. Callers must obtain
trusted catalog and price data separately, and have a person resolve all issues.
"""
from decimal import Decimal, InvalidOperation
from math import isfinite


def review_order(order, pages, customers, items, confidence_threshold=0.9):
    """Return review issues; an empty list means this limited gate passed.

    pages maps one-based page numbers to extracted source text. Each field is a
    mapping containing value, page, text and confidence. Items maps exact item
    codes to their expected unit prices; customers is a set of exact IDs.
    """
    if not 0 <= confidence_threshold <= 1:
        raise ValueError("confidence threshold must be between zero and one")
    issues = []

    def check(field, path):
        if not isinstance(field, dict):
            issues.append(f"{path}: missing field")
            return None
        value = field.get("value")
        page, text, score = field.get("page"), field.get("text"), field.get("confidence")
        if value is None or isinstance(value, str) and not value.strip():
            issues.append(f"{path}: missing value")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1 or not isinstance(text, str) or not text.strip() or text not in pages.get(page, ""):
            issues.append(f"{path}: missing or unverifiable source")
        if value is not None and isinstance(text, str) and str(value) not in text:
            issues.append(f"{path}: value not present in source text")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not isfinite(score) or not confidence_threshold <= score <= 1:
            issues.append(f"{path}: low or invalid confidence")
        return value

    customer = check(order.get("customer"), "customer")
    if not isinstance(customer, str) or customer not in customers:
        issues.append("customer: unknown customer")
    check(order.get("purchase_order"), "purchase_order")
    lines = order.get("lines")
    if not isinstance(lines, list) or not lines:
        issues.append("lines: no line items")
        return issues
    for index, line in enumerate(lines):
        path = f"lines[{index}]"
        if not isinstance(line, dict):
            issues.append(f"{path}: invalid line")
            continue
        item = check(line.get("item"), path + ".item")
        qty = check(line.get("quantity"), path + ".quantity")
        price = check(line.get("unit_price"), path + ".unit_price")
        if not isinstance(item, str) or item not in items:
            issues.append(path + ".item: unknown item")
        try:
            q = Decimal(str(qty))
            if not q.is_finite() or q <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            issues.append(path + ".quantity: invalid quantity")
        try:
            p = Decimal(str(price))
            if not p.is_finite() or p < 0:
                raise ValueError
            if isinstance(item, str) and item in items and p != Decimal(str(items[item])):
                issues.append(path + ".unit_price: price conflict")
        except (InvalidOperation, ValueError):
            issues.append(path + ".unit_price: invalid price")
    return issues
