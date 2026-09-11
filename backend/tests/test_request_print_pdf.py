"""Focused tests: Request Center Print PDF used as the request email attachment."""
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import notifications
import request_print


PRINT_MARKERS = [
    'PARTS TRANSFER REQUEST',
    'STOCK SOURCE (FROM)',
    'STOCK DESTINATION (TO)',
    'REQUEST NO',
    'REFERENCE NO (ORDER NO)',
    'REQUEST QTY',
    'ACCEPT QTY',
    'STATUS / REMARKS',
    'REQUESTED BY',
    'RECEIVED BY',
    'APPROVED BY',
    'DISPATCHED BY',
    'Please verify part number, accepted quantity and LOC before dispatch.',
]

OLD_TEMPLATE_MARKERS = [
    'Part Name / Description',
    'Avail. Qty',
    'Requested To / Approved By (Signature)',
]

GROUP = {
    'request_number': 'RQHY2609110001',
    'order_number': 'ORHY2609110001',
    'created_at': '2026-09-11T10:00:00+00:00',
    'requested_at': '2026-09-11T10:00:00+00:00',
    'status': 'Requested',
    'requested_user_name': 'Test User',
    'requested_user_id': 'U1',
    'requesting_brand': 'Hyundai',
    'requesting_dealer': 'Test Dealer',
    'requesting_branch': 'Vanagaram',
    'supplying_brand': 'Hyundai',
    'supplying_dealer': 'Test Dealer',
    'supplying_branch': 'Koyambedu',
    'receiver_users': [{'name': 'Branch User', 'id': 'U2'}],
    'total_items': 1,
    'total_qty': 2,
    'total_value': 250,
    'items': [{
        'part_number': 'P-PRINT-1',
        'description': 'Print Layout Part',
        'requested_qty': 2,
        'accepted_qty': 0,
        'available_qty_at_request': 9,
        'value': 250,
        'purchase_aging_days_at_request': 90,
        'sales_aging_days_at_request': 40,
        'loc_at_request': 'A1',
        'status': 'Requested',
        'remarks': '',
    }],
    'pdf_filename': 'RQHY2609110001.pdf',
}


def _pdf_haystack(pdf: bytes) -> str:
    parts = []
    for match in re.finditer(rb'\((?:\\.|[^\\)]){1,180}\)', pdf):
        raw = match.group(0)[1:-1].decode('latin-1', errors='ignore')
        if len(raw) > 120:
            continue
        parts.append(raw.replace('\\(', '(').replace('\\)', ')').replace('\\n', ' '))
    return ' '.join(parts)


def test_print_html_is_request_center_print_layout():
    html = request_print.build_request_print_html(GROUP)
    for marker in PRINT_MARKERS:
        assert marker in html, marker
    assert 'P-PRINT-1' in html
    assert 'Print Layout Part' in html
    for marker in OLD_TEMPLATE_MARKERS:
        assert marker not in html, marker


def test_email_pdf_uses_print_layout_not_old_template():
    pdf = notifications.build_request_pdf(GROUP)
    assert pdf[:4] == b'%PDF'
    hay = _pdf_haystack(pdf)
    for marker in PRINT_MARKERS:
        assert marker in hay, marker
    assert 'P-PRINT-1' in hay
    for marker in OLD_TEMPLATE_MARKERS:
        assert marker not in hay, marker


def test_print_columns_match_request_center():
    assert request_print.print_column_headers() == [
        'S.No', 'PART NUMBER', 'PART DESCRIPTION', 'LOC', 'REQUEST QTY',
        'ACCEPT QTY', 'PURCHASE AGING', 'SALES AGING', 'STATUS / REMARKS',
    ]
