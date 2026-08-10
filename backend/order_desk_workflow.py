"""
Order Desk workflow helpers — stage gates, daily source freezes, response SLA,
locks, and enrichment. Request Center `order_requests` remain authoritative.

Additive / backward-compatible: old records without new fields still load.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

NMTS_TZ = ZoneInfo('Asia/Kolkata')

CANCELLATION_REASONS = (
    'Wrong Part',
    'Wrong Qty',
    'Duplicate Entry',
    'Purchased Outside',
    'No Longer Required',
    'Other',
    'Cancelled – No Response',
)

# Internal / detailed statuses
REQUEST_STATUS_READY = 'Ready to Send'
REQUEST_STATUS_SENT = 'Request Sent'
REQUEST_STATUS_AWAITING = 'Awaiting Response'
REQUEST_STATUS_ACCEPTED = 'Accepted'
REQUEST_STATUS_PARTIAL = 'Partially Accepted'
REQUEST_STATUS_REJECTED = 'Rejected'
REQUEST_STATUS_REJECTED_TODAY = 'Rejected Today'
REQUEST_STATUS_EXPIRED = 'Response Time Expired'
REQUEST_STATUS_CANCEL_NO_RESP = 'Cancelled – No Response'
REQUEST_STATUS_CANCEL_REQ = 'Cancellation Requested'
REQUEST_STATUS_CANCELLED = 'Cancelled'
REQUEST_STATUS_COMPLETED = 'Completed'
REQUEST_STATUS_FACTORY = 'No Further Stock Available'
REQUEST_STATUS_REMAINING = 'Remaining Qty'
REQUEST_STATUS_TO_PROCESS = 'To Process'
REQUEST_STATUS_BRANCH_EXHAUSTED = 'Branch Exhausted'
REQUEST_STATUS_DEALER_EXHAUSTED = 'Dealer Exhausted'

# Primary filter-facing statuses
PRIMARY_FILTER_STATUSES = (
    'All',
    'To Process',
    'Request Sent',
    'Accepted',
    'Rejected',
    'Completed',
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def business_date_key(dt: datetime = None) -> str:
    value = dt or datetime.now(NMTS_TZ)
    if value.tzinfo is None:
        value = value.replace(tzinfo=NMTS_TZ)
    else:
        value = value.astimezone(NMTS_TZ)
    return value.strftime('%Y%m%d')


def _clean(value: Any) -> str:
    return str(value or '').strip()


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value).replace('Z', '+00:00')
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def allocation_level(source: dict, order: dict) -> str:
    explicit = _clean(source.get('level') or source.get('source_type')).lower()
    if explicit in ('branch', 'dealer'):
        return explicit
    order_dealer = _clean(order.get('dealer_name')).lower()
    source_dealer = _clean(source.get('dealer_name') or source.get('source_dealer')).lower()
    if order_dealer and source_dealer and order_dealer == source_dealer:
        return 'branch'
    if source_dealer and order_dealer and source_dealer != order_dealer:
        return 'dealer'
    return 'branch'


def source_key(dealer: str, branch: str) -> Tuple[str, str]:
    return (_clean(dealer).lower(), _clean(branch).lower())


def freeze_key(part_number: str, brand: str, dealer: str, branch: str, date_key: str) -> str:
    return '|'.join([
        _clean(part_number).upper(),
        _clean(brand).lower(),
        _clean(dealer).lower(),
        _clean(branch).lower(),
        _clean(date_key),
    ])


def response_time_minutes_for_lines(line_item_count: int) -> int:
    n = int(line_item_count or 0)
    if n <= 20:
        return 30
    if n <= 50:
        return 45
    return 60


def reminder_offsets_minutes(sla_minutes: int) -> Tuple[int, int]:
    """Return (first_reminder, urgent_reminder) offsets from send time."""
    if sla_minutes <= 30:
        return 15, 25
    if sla_minutes <= 45:
        return 20, 35
    return 30, 50


def compute_response_schedule(line_item_count: int, sent_at: datetime = None) -> dict:
    sent = sent_at or _now_utc()
    minutes = response_time_minutes_for_lines(line_item_count)
    r1, r2 = reminder_offsets_minutes(minutes)
    deadline = sent + timedelta(minutes=minutes)
    return {
        'line_item_count': int(line_item_count or 0),
        'response_time_minutes': minutes,
        'request_sent_at': sent.isoformat(),
        'response_deadline': deadline.isoformat(),
        'reminder_at': (sent + timedelta(minutes=r1)).isoformat(),
        'urgent_reminder_at': (sent + timedelta(minutes=r2)).isoformat(),
        'response_status': 'awaiting',
    }


def map_request_center_status(status: str, requested_qty: float = 0, accepted_qty: float = 0) -> str:
    status = _clean(status)
    if status == 'Requested':
        return REQUEST_STATUS_SENT
    if status == 'Approved':
        if accepted_qty and requested_qty and accepted_qty < requested_qty:
            return REQUEST_STATUS_PARTIAL
        return REQUEST_STATUS_ACCEPTED
    if status == 'Partially Approved':
        return REQUEST_STATUS_PARTIAL
    if status == 'Rejected':
        return REQUEST_STATUS_REJECTED
    if status == 'Cancelled':
        return REQUEST_STATUS_CANCELLED
    if status == 'Completed':
        return REQUEST_STATUS_COMPLETED
    if status in ('Dispatched', 'Received'):
        return status
    if status == REQUEST_STATUS_CANCEL_REQ:
        return REQUEST_STATUS_CANCEL_REQ
    if status == REQUEST_STATUS_EXPIRED:
        return REQUEST_STATUS_EXPIRED
    return status or REQUEST_STATUS_READY


def email_status_label(raw: Any) -> str:
    value = _clean(raw).lower()
    if value in ('sent', 'email sent'):
        return 'Email Sent'
    if value in ('failed', 'skipped', 'email failed'):
        return 'Email Failed'
    if value in ('pending', '', 'email pending'):
        return 'Email Pending'
    return _clean(raw) or 'Email Pending'


def primary_filter_status(detail_status: str) -> str:
    s = _clean(detail_status)
    if s in (REQUEST_STATUS_ACCEPTED, REQUEST_STATUS_PARTIAL, 'Dispatched', 'Received'):
        if s == REQUEST_STATUS_PARTIAL:
            return 'Accepted'  # partial still shows under Accepted primary filter with row detail
        return 'Accepted'
    if s in (REQUEST_STATUS_REJECTED, REQUEST_STATUS_REJECTED_TODAY):
        return 'Rejected'
    if s in (REQUEST_STATUS_COMPLETED,):
        return 'Completed'
    if s in (REQUEST_STATUS_SENT, REQUEST_STATUS_AWAITING, REQUEST_STATUS_EXPIRED):
        return 'Request Sent'
    if s in (REQUEST_STATUS_CANCELLED, REQUEST_STATUS_CANCEL_NO_RESP, REQUEST_STATUS_CANCEL_REQ):
        return 'Completed' if s != REQUEST_STATUS_CANCEL_REQ else 'To Process'
    return 'To Process'


def compact_requested_from(history_rows: List[dict], allocations: List[dict], order: dict) -> str:
    parts = []
    seen = set()

    def add(dealer: str, branch: str, qty: float, level: str):
        key = (dealer.lower(), branch.lower(), round(qty, 4))
        if key in seen or qty <= 0:
            return
        seen.add(key)
        if level == 'dealer':
            parts.append(f'{dealer} / {branch} - {int(qty) if qty == int(qty) else qty}')
        else:
            parts.append(f'{branch} - {int(qty) if qty == int(qty) else qty}')

    for row in history_rows or []:
        dealer = _clean(row.get('source_dealer') or row.get('supplying_dealer') or row.get('dealer_name'))
        branch = _clean(row.get('source_branch') or row.get('supplying_branch') or row.get('branch'))
        qty = _f(row.get('requested_qty'))
        level = _clean(row.get('source_type') or row.get('level')).lower() or allocation_level(
            {'dealer_name': dealer, 'branch': branch}, order
        )
        add(dealer, branch, qty, 'dealer' if 'dealer' in level else 'branch')

    for alloc in allocations or []:
        if alloc.get('request_no') or alloc.get('request_number'):
            continue
        dealer = _clean(alloc.get('dealer_name') or alloc.get('source_dealer'))
        branch = _clean(alloc.get('branch') or alloc.get('source_branch'))
        qty = _f(alloc.get('request_qty') or alloc.get('requested_qty'))
        level = allocation_level(alloc, order)
        add(dealer, branch, qty, level)

    return ' | '.join(parts)


def aging_value(entry: dict, aging_type: str) -> float:
    if aging_type == 'sales':
        return float(entry.get('sales_aging_days') or entry.get('sales_aging') or 0)
    return float(entry.get('purchase_aging_days') or entry.get('purchase_aging') or entry.get('aging_days') or 0)


def source_passes_aging(entry: dict, aging_type: str, min_days: float) -> bool:
    if not min_days or float(min_days) <= 0:
        return True
    return aging_value(entry, aging_type) >= float(min_days)


async def load_today_freezes(db, part_numbers: List[str], brand: str, date_key: str = None) -> Set[str]:
    """Return set of freeze_key strings for today's freezes covering these parts."""
    date_key = date_key or business_date_key()
    parts = [_clean(p).upper() for p in part_numbers if p]
    if not parts:
        return set()
    cursor = db.source_rejection_freezes.find({
        'business_date_key': date_key,
        'part_number_norm': {'$in': parts},
        **({'brand_name_norm': _clean(brand).lower()} if brand else {}),
    }, {'_id': 0})
    keys = set()
    async for doc in cursor:
        keys.add(freeze_key(
            doc.get('part_number'), doc.get('brand_name'),
            doc.get('supplying_dealer'), doc.get('supplying_branch'),
            doc.get('business_date_key') or date_key,
        ))
    return keys


async def record_rejection_freeze(db, req: dict, order: dict = None):
    """Freeze Part + Source for the IST business day. Idempotent."""
    date_key = business_date_key()
    part = _clean(req.get('part_number'))
    brand = _clean(req.get('requesting_brand') or (order or {}).get('brand_name') or req.get('supplying_brand'))
    dealer = _clean(req.get('supplying_dealer'))
    branch = _clean(req.get('supplying_branch'))
    if not part or not dealer or not branch:
        return None
    key = freeze_key(part, brand, dealer, branch, date_key)
    existing = await db.source_rejection_freezes.find_one({'freeze_key': key}, {'_id': 0, 'id': 1})
    if existing:
        return existing
    doc = {
        'id': str(uuid.uuid4()),
        'freeze_key': key,
        'part_number': part,
        'part_number_norm': part.upper(),
        'brand_name': brand,
        'brand_name_norm': brand.lower(),
        'supplying_dealer': dealer,
        'supplying_branch': branch,
        'business_date_key': date_key,
        'order_id': req.get('order_id'),
        'order_request_id': req.get('id'),
        'request_number': req.get('request_number'),
        'frozen_at': _now_iso(),
        'expires_next_business_date': True,
    }
    try:
        await db.source_rejection_freezes.insert_one(dict(doc))
    except Exception:
        # Unique index race — treat as already frozen
        return await db.source_rejection_freezes.find_one({'freeze_key': key}, {'_id': 0})
    return doc


def is_source_frozen(freezes: Set[str], part: str, brand: str, dealer: str, branch: str, date_key: str = None) -> bool:
    date_key = date_key or business_date_key()
    return freeze_key(part, brand, dealer, branch, date_key) in freezes


def evaluate_group_timer(header: dict, now: datetime = None) -> dict:
    """Derive response timer fields for a request_headers document."""
    now = now or _now_utc()
    deadline = _parse_iso(header.get('response_deadline'))
    sent_at = _parse_iso(header.get('request_sent_at') or header.get('created_at'))
    minutes = int(header.get('response_time_minutes') or 0)
    line_count = int(header.get('line_item_count') or len(header.get('items') or []) or 0)
    if not minutes and line_count:
        minutes = response_time_minutes_for_lines(line_count)
    if not deadline and sent_at and minutes:
        deadline = sent_at + timedelta(minutes=minutes)

    status_raw = _clean(header.get('status'))
    terminal = status_raw in ('Approved', 'Partially Approved', 'Rejected', 'Cancelled', 'Completed', 'Dispatched', 'Received')
    response_status = _clean(header.get('response_status') or 'awaiting')
    remaining_seconds = None
    cancel_allowed = False
    if terminal:
        response_status = 'responded' if status_raw not in ('Cancelled',) else 'cancelled'
    elif deadline:
        remaining_seconds = max(0, int((deadline - now).total_seconds()))
        if remaining_seconds <= 0:
            response_status = 'expired'
            cancel_allowed = True
        else:
            response_status = 'awaiting'
            cancel_allowed = False
    else:
        cancel_allowed = False

    return {
        'line_item_count': line_count or header.get('line_item_count') or 0,
        'response_time_minutes': minutes or header.get('response_time_minutes'),
        'request_sent_at': header.get('request_sent_at') or header.get('created_at'),
        'response_deadline': deadline.isoformat() if deadline else header.get('response_deadline'),
        'response_status': response_status,
        'remaining_seconds': remaining_seconds,
        'cancel_allowed': cancel_allowed,
        'reminder_at': header.get('reminder_at'),
        'urgent_reminder_at': header.get('urgent_reminder_at'),
    }


def eligible_pool(item: dict, order: dict, level: str, freezes: Set[str], aging_type: str, min_aging_days: float) -> List[dict]:
    """Eligible sources for a stage after aging + freeze + net qty filters."""
    pool_field = 'same_dealer_sources' if level == 'branch' else 'other_dealer_sources'
    part = _clean(item.get('part_number'))
    brand = _clean(order.get('brand_name'))
    excluded = {source_key(x.get('dealer_name'), x.get('branch')) for x in (item.get('excluded_sources') or [])}
    # Also exclude sources with active pending request or accepted full
    result = []
    for s in item.get(pool_field) or []:
        dealer = _clean(s.get('dealer_name'))
        branch = _clean(s.get('branch'))
        if source_key(dealer, branch) in excluded:
            continue
        if is_source_frozen(freezes, part, brand, dealer, branch):
            continue
        if not source_passes_aging(s, aging_type, min_aging_days):
            continue
        net = _f(s.get('net_available_qty', s.get('available_qty')))
        if net <= 0:
            continue
        result.append({
            **s,
            'source_frozen_today': False,
            'rejected_today': False,
            'level': level,
            'source_type': level,
        })
    return result


def annotate_sources_with_freeze(sources: List[dict], item: dict, order: dict, freezes: Set[str], level: str) -> List[dict]:
    part = _clean(item.get('part_number'))
    brand = _clean(order.get('brand_name'))
    out = []
    for s in sources or []:
        frozen = is_source_frozen(freezes, part, brand, s.get('dealer_name'), s.get('branch'))
        out.append({
            **s,
            'level': level,
            'source_type': level,
            'source_frozen_today': frozen,
            'rejected_today': frozen,
            'selection_disabled': frozen,
        })
    return out


def compute_stage_flags(item: dict, order: dict, freezes: Set[str],
                        branch_aging_type='purchase', branch_min_aging=0,
                        dealer_aging_type='purchase', dealer_min_aging=0) -> dict:
    remaining = _f(item.get('remaining_qty'))
    # If remaining not yet set, approximate from required - accepted later by caller
    branch_pool = eligible_pool(item, order, 'branch', freezes, branch_aging_type, branch_min_aging)
    dealer_pool = eligible_pool(item, order, 'dealer', freezes, dealer_aging_type, dealer_min_aging)

    has_pending = any(
        _clean(a.get('status')).lower() in ('request sent', 'requested', 'awaiting response')
        or (a.get('request_no') and _clean(a.get('request_status')).lower() in ('request sent', 'awaiting response'))
        for a in (item.get('allocations') or [])
    )

    if remaining <= 0 and _f(item.get('accepted_qty')) > 0:
        branch_stage_status = 'complete'
        dealer_stage_status = 'complete'
        factory_stage_status = 'locked'
        next_source_allowed = False
        active_stage = 'complete'
    elif branch_pool or (remaining > 0 and not item.get('branch_stage_exhausted')):
        # Branch still has eligible sources OR not yet marked exhausted
        if branch_pool:
            branch_stage_status = 'open'
            dealer_stage_status = 'locked'
            factory_stage_status = 'locked'
            active_stage = 'branch'
            next_source_allowed = not has_pending and remaining > 0
        else:
            branch_stage_status = 'exhausted'
            if dealer_pool:
                dealer_stage_status = 'open'
                factory_stage_status = 'locked'
                active_stage = 'dealer'
                next_source_allowed = not has_pending and remaining > 0
            else:
                dealer_stage_status = 'exhausted'
                factory_stage_status = 'open' if remaining > 0 else 'locked'
                active_stage = 'factory' if remaining > 0 else 'complete'
                next_source_allowed = False
    else:
        branch_stage_status = 'exhausted'
        if dealer_pool:
            dealer_stage_status = 'open'
            factory_stage_status = 'locked'
            active_stage = 'dealer'
            next_source_allowed = not has_pending and remaining > 0
        else:
            dealer_stage_status = 'exhausted'
            factory_stage_status = 'open' if remaining > 0 else 'locked'
            active_stage = 'factory' if remaining > 0 else 'complete'
            next_source_allowed = False

    # Explicit exhaustion flags from prior computations win for dealer unlock
    if item.get('branch_stage_exhausted') and remaining > 0:
        branch_stage_status = 'exhausted'
        if dealer_pool:
            dealer_stage_status = 'open'
            factory_stage_status = 'locked'
            active_stage = 'dealer'
            next_source_allowed = not has_pending
        elif item.get('dealer_stage_exhausted') or not dealer_pool:
            dealer_stage_status = 'exhausted'
            factory_stage_status = 'open'
            active_stage = 'factory'
            next_source_allowed = False

    if item.get('dealer_stage_exhausted') and remaining > 0 and branch_stage_status == 'exhausted':
        dealer_stage_status = 'exhausted'
        factory_stage_status = 'open'
        active_stage = 'factory'
        next_source_allowed = False

    return {
        'branch_stage_status': branch_stage_status,
        'dealer_stage_status': dealer_stage_status,
        'factory_stage_status': factory_stage_status,
        'active_stage': active_stage,
        'next_source_allowed': next_source_allowed,
        'eligible_branch_count': len(branch_pool),
        'eligible_dealer_count': len(dealer_pool),
    }


def compute_item_workflow(item: dict, order: dict, item_requests: List[dict],
                          header_emails: Dict[str, dict] = None,
                          header_timers: Dict[str, dict] = None) -> dict:
    header_emails = header_emails or {}
    header_timers = header_timers or {}
    required = _f(item.get('required_qty'))
    history = []
    accepted_total = 0.0
    active_requested = 0.0
    has_sent = False
    has_partial = False
    has_rejected = False
    has_cancel_req = False
    any_completed = False
    pending_timer = None
    qty_locked = False
    source_locked = False

    for req in item_requests or []:
        requested = _f(req.get('requested_qty'))
        accepted = _f(req.get('accepted_qty', req.get('approved_qty')))
        status = _clean(req.get('status'))
        ui_status = map_request_center_status(status, requested, accepted)
        if item.get('cancellation_status') == 'pending' and req.get('id') == item.get('cancellation_request_id'):
            ui_status = REQUEST_STATUS_CANCEL_REQ
            has_cancel_req = True

        email_meta = header_emails.get(req.get('request_number') or '', {})
        timer_meta = header_timers.get(req.get('request_number') or '', {})
        email_status = email_status_label(
            req.get('email_status') or email_meta.get('email_status') or ('sent' if email_meta.get('email_sent') else 'pending')
        )
        level = allocation_level(
            {'dealer_name': req.get('supplying_dealer'), 'branch': req.get('supplying_branch'),
             'level': req.get('source_type') or req.get('level')},
            order,
        )
        if status == 'Requested' and timer_meta.get('response_status') == 'expired':
            ui_status = REQUEST_STATUS_EXPIRED
        elif status == 'Requested' and timer_meta.get('response_status') == 'awaiting':
            ui_status = REQUEST_STATUS_AWAITING
        if status == 'Cancelled' and _clean(req.get('cancellation_reason')).startswith('Cancelled'):
            ui_status = REQUEST_STATUS_CANCEL_NO_RESP

        history.append({
            'source_type': level.title(),
            'source_name': _clean(req.get('supplying_branch')) if level == 'branch'
                else f"{_clean(req.get('supplying_dealer'))} / {_clean(req.get('supplying_branch'))}",
            'dealer_name': _clean(req.get('supplying_dealer')),
            'branch_name': _clean(req.get('supplying_branch')),
            'source_dealer': _clean(req.get('supplying_dealer')),
            'source_branch': _clean(req.get('supplying_branch')),
            'requested_qty': requested,
            'accepted_qty': accepted,
            'remaining_qty': max(0.0, requested - accepted),
            'request_no': req.get('request_number'),
            'request_id': req.get('id'),
            'request_status': ui_status,
            'email_status': email_status,
            'remarks': req.get('approval_remarks') or req.get('remarks') or '',
            'status_raw': status,
            'level': level,
            'response_deadline': timer_meta.get('response_deadline'),
            'response_status': timer_meta.get('response_status'),
            'remaining_seconds': timer_meta.get('remaining_seconds'),
            'cancel_allowed': timer_meta.get('cancel_allowed', False),
            'response_time_minutes': timer_meta.get('response_time_minutes'),
            'line_item_count': timer_meta.get('line_item_count'),
        })

        if status in ('Approved', 'Partially Approved', 'Dispatched', 'Received', 'Completed'):
            accepted_total += accepted
            if accepted > 0:
                qty_locked = True
                source_locked = True
        if status == 'Requested':
            has_sent = True
            active_requested += requested
            qty_locked = True
            source_locked = True
            if timer_meta:
                pending_timer = timer_meta
        if status == 'Approved' and accepted < requested:
            has_partial = True
        if status == 'Rejected':
            has_rejected = True
        if status == 'Completed':
            any_completed = True

    remaining = max(0.0, required - accepted_total - active_requested)

    unsent_alloc_qty = 0.0
    for alloc in item.get('allocations') or []:
        if alloc.get('request_no') or alloc.get('request_number') or _clean(alloc.get('status')).lower() in (
            'requested', 'request sent', 'awaiting response', 'accepted', 'partially accepted',
            'rejected', 'cancelled', 'completed', 'response time expired',
        ):
            continue
        unsent_alloc_qty += _f(alloc.get('request_qty'))

    factory_order_qty = _f(item.get('factory_order_qty'))
    if item.get('cancellation_status') == 'approved' or _clean(item.get('request_status')) == REQUEST_STATUS_CANCELLED:
        request_status = REQUEST_STATUS_CANCELLED
    elif item.get('cancellation_status') == 'pending' or has_cancel_req:
        request_status = REQUEST_STATUS_CANCEL_REQ
    elif pending_timer and pending_timer.get('response_status') == 'expired':
        request_status = REQUEST_STATUS_EXPIRED
    elif has_sent and active_requested > 0:
        request_status = REQUEST_STATUS_AWAITING if (pending_timer or True) else REQUEST_STATUS_SENT
    elif factory_order_qty > 0 and remaining > 0 and not unsent_alloc_qty and not active_requested:
        request_status = REQUEST_STATUS_FACTORY
    elif any_completed and remaining <= 0 and active_requested <= 0:
        request_status = REQUEST_STATUS_COMPLETED
    elif accepted_total >= required and remaining <= 0 and active_requested <= 0:
        request_status = REQUEST_STATUS_ACCEPTED if not has_partial else REQUEST_STATUS_PARTIAL
    elif has_partial and remaining > 0:
        request_status = REQUEST_STATUS_PARTIAL
    elif has_rejected and remaining > 0 and accepted_total <= 0 and not unsent_alloc_qty:
        request_status = REQUEST_STATUS_REJECTED
    elif remaining > 0 and unsent_alloc_qty > 0:
        request_status = REQUEST_STATUS_READY
    elif remaining > 0:
        request_status = REQUEST_STATUS_TO_PROCESS
    elif remaining <= 0 and accepted_total > 0:
        request_status = REQUEST_STATUS_ACCEPTED
    else:
        request_status = item.get('request_status') or item.get('status') or REQUEST_STATUS_TO_PROCESS

    if item.get('no_further_stock') and remaining > 0:
        request_status = REQUEST_STATUS_FACTORY
        factory_order_qty = remaining

    # Row display helpers
    display_status = request_status
    if request_status == REQUEST_STATUS_PARTIAL:
        display_status = f'Accepted {int(accepted_total) if accepted_total == int(accepted_total) else accepted_total} / Remaining {int(remaining) if remaining == int(remaining) else remaining}'
    elif request_status == REQUEST_STATUS_AWAITING and pending_timer and pending_timer.get('remaining_seconds') is not None:
        mins_left = max(0, (pending_timer['remaining_seconds'] + 59) // 60)
        display_status = f'Awaiting Response – {mins_left} min left'

    requested_from = compact_requested_from(history, item.get('allocations') or [], order)
    cancel_allowed = bool(pending_timer and pending_timer.get('cancel_allowed'))
    # Accepted qty never cancellable via no-response path
    if accepted_total >= required:
        cancel_allowed = False

    return {
        'accepted_qty': accepted_total,
        'remaining_qty': remaining,
        'factory_order_qty': factory_order_qty if request_status == REQUEST_STATUS_FACTORY else (factory_order_qty or 0),
        'request_status': request_status,
        'display_status': display_status,
        'filter_status': primary_filter_status(request_status),
        'request_history': history,
        'requested_from': requested_from,
        'allocated_qty': accepted_total + active_requested + unsent_alloc_qty,
        'has_unsent_allocations': unsent_alloc_qty > 0,
        'unsent_allocation_qty': unsent_alloc_qty,
        'source_type_summary': _source_type_summary(history, item.get('allocations') or [], order),
        'qty_locked': qty_locked,
        'source_locked': source_locked,
        'lock_reason': 'Request Sent – Awaiting Response' if has_sent and active_requested > 0 else (
            'Accepted – Final' if accepted_total > 0 and remaining <= 0 else ''
        ),
        'response_deadline': (pending_timer or {}).get('response_deadline'),
        'response_status': (pending_timer or {}).get('response_status'),
        'response_time_minutes': (pending_timer or {}).get('response_time_minutes'),
        'line_item_count': (pending_timer or {}).get('line_item_count'),
        'remaining_seconds': (pending_timer or {}).get('remaining_seconds'),
        'cancel_allowed': cancel_allowed,
        'pending_request_number': next(
            (h['request_no'] for h in history if h.get('status_raw') == 'Requested'),
            None,
        ),
    }


def _source_type_summary(history, allocations, order) -> str:
    types = set()
    for row in history or []:
        t = _clean(row.get('source_type') or row.get('level')).lower()
        if t:
            types.add('Branch' if 'branch' in t else 'Dealer')
    for alloc in allocations or []:
        types.add('Branch' if allocation_level(alloc, order) == 'branch' else 'Dealer')
    if not types:
        return ''
    if types == {'Branch'}:
        return 'Branch'
    if types == {'Dealer'}:
        return 'Dealer'
    return 'Branch+Dealer'


def compute_order_stage(enriched_items: List[dict]) -> dict:
    """Order-level active stage from item stage flags."""
    if not enriched_items:
        return {'active_stage': 'branch', 'branch_stage_status': 'open',
                'dealer_stage_status': 'locked', 'factory_stage_status': 'locked'}
    if any(i.get('branch_stage_status') == 'open' for i in enriched_items):
        return {'active_stage': 'branch', 'branch_stage_status': 'open',
                'dealer_stage_status': 'locked', 'factory_stage_status': 'locked'}
    if any(i.get('dealer_stage_status') == 'open' for i in enriched_items):
        return {'active_stage': 'dealer', 'branch_stage_status': 'exhausted',
                'dealer_stage_status': 'open', 'factory_stage_status': 'locked'}
    if any(i.get('factory_stage_status') == 'open' for i in enriched_items):
        return {'active_stage': 'factory', 'branch_stage_status': 'exhausted',
                'dealer_stage_status': 'exhausted', 'factory_stage_status': 'open'}
    return {'active_stage': 'complete', 'branch_stage_status': 'complete',
            'dealer_stage_status': 'complete', 'factory_stage_status': 'locked'}


async def enrich_order_items(db, order: dict, items: List[dict],
                             branch_aging_type='purchase', branch_min_aging=0,
                             dealer_aging_type='purchase', dealer_min_aging=0) -> List[dict]:
    if not items:
        return []
    order_id = order.get('id')
    requests = await db.order_requests.find({'order_id': order_id}, {'_id': 0}).to_list(20000)
    by_item: Dict[str, List[dict]] = {}
    request_numbers = set()
    for req in requests:
        by_item.setdefault(req.get('order_item_id'), []).append(req)
        if req.get('request_number'):
            request_numbers.add(req.get('request_number'))

    headers = {}
    header_timers = {}
    if request_numbers:
        async for h in db.request_headers.find(
            {'request_number': {'$in': list(request_numbers)}},
            {'_id': 0},
        ):
            headers[h.get('request_number')] = h
            header_timers[h.get('request_number')] = evaluate_group_timer(h)

    freezes = await load_today_freezes(
        db, [i.get('part_number') for i in items], order.get('brand_name') or '',
    )

    enriched = []
    for item in items:
        wf = compute_item_workflow(item, order, by_item.get(item.get('id'), []), headers, header_timers)
        merged = {**item, **wf}
        stages = compute_stage_flags(
            merged, order, freezes,
            branch_aging_type, branch_min_aging,
            dealer_aging_type, dealer_min_aging,
        )
        # Annotate availability pools
        same = annotate_sources_with_freeze(item.get('same_dealer_sources') or [], item, order, freezes, 'branch')
        other = annotate_sources_with_freeze(item.get('other_dealer_sources') or [], item, order, freezes, 'dealer')
        # Apply aging visibility flags (still shown but marked ineligible if below cutoff)
        for s in same:
            s['aging_eligible'] = source_passes_aging(s, branch_aging_type, branch_min_aging) and not s.get('source_frozen_today')
            s['selection_disabled'] = bool(s.get('source_frozen_today') or not source_passes_aging(s, branch_aging_type, branch_min_aging) or wf.get('qty_locked') and wf.get('remaining_qty', 1) <= 0)
        for s in other:
            s['aging_eligible'] = source_passes_aging(s, dealer_aging_type, dealer_min_aging) and not s.get('source_frozen_today')
            s['selection_disabled'] = bool(s.get('source_frozen_today') or not source_passes_aging(s, dealer_aging_type, dealer_min_aging) or stages.get('dealer_stage_status') == 'locked')

        normalized_allocs = []
        for alloc in item.get('allocations') or []:
            level = allocation_level(alloc, order)
            normalized_allocs.append({
                **alloc,
                'source_type': level,
                'level': level,
                'source_branch': alloc.get('branch') or alloc.get('source_branch'),
                'source_dealer': alloc.get('dealer_name') or alloc.get('source_dealer'),
                'requested_qty': alloc.get('request_qty') or alloc.get('requested_qty'),
                'locked': bool(alloc.get('request_no') or alloc.get('request_number')),
            })

        # Mark branch/dealer exhausted when pools empty and remaining > 0
        if wf['remaining_qty'] > 0 and stages['eligible_branch_count'] == 0 and (item.get('same_dealer_sources') or item.get('availability_checked_at')):
            stages['branch_stage_status'] = 'exhausted' if stages['branch_stage_status'] != 'complete' else stages['branch_stage_status']
        if wf['remaining_qty'] > 0 and stages['branch_stage_status'] == 'exhausted' and stages['eligible_dealer_count'] == 0:
            stages['dealer_stage_status'] = 'exhausted'
            stages['factory_stage_status'] = 'open'
            stages['active_stage'] = 'factory'
            wf['factory_order_qty'] = wf['remaining_qty']
            if wf['request_status'] in (REQUEST_STATUS_TO_PROCESS, REQUEST_STATUS_REMAINING, REQUEST_STATUS_REJECTED):
                wf['request_status'] = REQUEST_STATUS_FACTORY
                wf['display_status'] = REQUEST_STATUS_FACTORY
                wf['filter_status'] = 'To Process'

        enriched.append({
            **merged,
            **stages,
            **wf,
            'same_dealer_sources': same,
            'other_dealer_sources': other,
            'allocations': normalized_allocs,
            'balance_qty': wf['remaining_qty'],
            'rejected_today_sources': [
                {'dealer_name': s.get('dealer_name'), 'branch': s.get('branch')}
                for s in (same + other) if s.get('rejected_today')
            ],
            'source_frozen_today': any(s.get('source_frozen_today') for s in (same + other)),
        })

    order_stage = compute_order_stage(enriched)
    for row in enriched:
        row['order_active_stage'] = order_stage['active_stage']
        row['order_branch_stage_status'] = order_stage['branch_stage_status']
        row['order_dealer_stage_status'] = order_stage['dealer_stage_status']
        row['order_factory_stage_status'] = order_stage['factory_stage_status']
    return enriched


async def append_order_audit(db, order: dict, action: str, current_user, details: Optional[dict] = None):
    doc = {
        'id': str(uuid.uuid4()),
        'order_id': order.get('id'),
        'order_number': order.get('order_number'),
        'action': action,
        'performed_by': getattr(current_user, 'id', None) or (current_user or {}).get('id'),
        'performed_user_name': getattr(current_user, 'username', None) or (current_user or {}).get('username'),
        'details': details or {},
        'created_at': _now_iso(),
    }
    await db.order_activity.insert_one(doc)
    return doc


async def sync_order_item_after_request_decision(db, req: dict, now: str = None):
    """Preserve accepted qty + history; apply daily freeze on reject; unlock remaining only."""
    now = now or _now_iso()
    item_id = req.get('order_item_id')
    if not item_id:
        return
    item = await db.order_items.find_one({'id': item_id}, {'_id': 0})
    if not item:
        return
    order = await db.order_headers.find_one({'id': item.get('order_id')}, {'_id': 0}) or {}
    item_requests = await db.order_requests.find({'order_item_id': item_id}, {'_id': 0}).to_list(5000)
    wf = compute_item_workflow(item, order, item_requests)

    status = _clean(req.get('status'))
    accepted = _f(req.get('accepted_qty', req.get('approved_qty')))
    requested = _f(req.get('requested_qty'))
    failed_source = {
        'dealer_name': req.get('supplying_dealer', ''),
        'branch': req.get('supplying_branch', ''),
    }

    if status == 'Rejected':
        await record_rejection_freeze(db, req, order)

    update: Dict[str, Any] = {
        'accepted_qty': wf['accepted_qty'],
        'remaining_qty': wf['remaining_qty'],
        'request_status': wf['request_status'],
        'updated_at': now,
    }

    kept_allocs = []
    for alloc in item.get('allocations') or []:
        same = source_key(alloc.get('dealer_name'), alloc.get('branch')) == source_key(
            failed_source['dealer_name'], failed_source['branch']
        )
        already_sent = bool(alloc.get('request_no') or alloc.get('request_number'))
        if same and not already_sent:
            continue
        if same and already_sent:
            alloc = {
                **alloc,
                'accepted_qty': accepted,
                'remaining_qty': max(0.0, requested - accepted),
                'request_status': map_request_center_status(status, requested, accepted),
                'status': map_request_center_status(status, requested, accepted),
                'locked': accepted > 0,
            }
        kept_allocs.append(alloc)

    update['allocations'] = kept_allocs
    update['allocated_qty'] = sum(_f(a.get('request_qty')) for a in kept_allocs)

    needs_retry = wf['remaining_qty'] > 0 and status in ('Rejected', 'Cancelled', 'Approved') and (
        status != 'Approved' or accepted < requested
    )
    if needs_retry:
        update['retry_required'] = True
        update['status'] = 'Pending Retry'
        await db.order_items.update_one(
            {'id': item_id},
            {'$set': update, '$addToSet': {'excluded_sources': failed_source}},
        )
    else:
        if wf['remaining_qty'] <= 0:
            update['retry_required'] = False
            update['status'] = 'Accepted' if wf['request_status'] == REQUEST_STATUS_ACCEPTED else item.get('status')
        await db.order_items.update_one({'id': item_id}, {'$set': update})

    # Recompute stage exhaustion after decision
    refreshed = await db.order_items.find_one({'id': item_id}, {'_id': 0})
    if not refreshed:
        return
    freezes = await load_today_freezes(db, [refreshed.get('part_number')], order.get('brand_name') or '')
    stages = compute_stage_flags(refreshed, order, freezes)
    stage_update = {
        'branch_stage_exhausted': stages['branch_stage_status'] == 'exhausted',
        'dealer_stage_exhausted': stages['dealer_stage_status'] == 'exhausted',
        'enquiry_stage': stages['active_stage'],
        'updated_at': now,
    }
    if stages['active_stage'] == 'factory' and _f(refreshed.get('remaining_qty')) > 0:
        stage_update.update({
            'no_further_stock': True,
            'factory_order_qty': _f(refreshed.get('remaining_qty')),
            'request_status': REQUEST_STATUS_FACTORY,
            'status': REQUEST_STATUS_FACTORY,
        })
    await db.order_items.update_one({'id': item_id}, {'$set': stage_update})


def filter_unsent_allocations(item: dict, order: dict, level: str, existing_requests: List[dict]) -> List[dict]:
    blocked = set()
    for req in existing_requests or []:
        if _clean(req.get('status')) in ('Rejected', 'Cancelled'):
            continue
        blocked.add(source_key(req.get('supplying_dealer'), req.get('supplying_branch')))

    result = []
    for source in item.get('allocations') or []:
        if allocation_level(source, order) != level:
            continue
        if _f(source.get('request_qty')) <= 0:
            continue
        key = source_key(source.get('dealer_name'), source.get('branch'))
        if key in blocked:
            continue
        if source.get('request_no') or source.get('request_number'):
            continue
        result.append(source)
    return result


def mark_allocations_sent(item: dict, pairs_sources: List[dict], request_number: str, now: str) -> List[dict]:
    sent_keys = {source_key(s.get('dealer_name'), s.get('branch')) for s in pairs_sources}
    updated = []
    for alloc in item.get('allocations') or []:
        if source_key(alloc.get('dealer_name'), alloc.get('branch')) in sent_keys:
            updated.append({
                **alloc,
                'request_no': request_number,
                'request_number': request_number,
                'request_status': REQUEST_STATUS_AWAITING,
                'status': REQUEST_STATUS_AWAITING,
                'sent_at': now,
                'locked': True,
            })
        else:
            updated.append(alloc)
    return updated


async def ensure_order_desk_indexes(db):
    """Additive indexes for freeze + deadline lookups. Safe to call repeatedly."""
    try:
        await db.source_rejection_freezes.create_index([('freeze_key', 1)], unique=True)
        await db.source_rejection_freezes.create_index([
            ('business_date_key', 1), ('part_number_norm', 1), ('brand_name_norm', 1),
        ])
        await db.request_headers.create_index([('response_deadline', 1), ('response_status', 1)])
        await db.request_headers.create_index([('status', 1), ('response_deadline', 1)])
    except Exception:
        pass
