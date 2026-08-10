"""
Order Desk workflow helpers — non-destructive extensions around the existing
availability / auto-suggest / reservation / Request Center flow.

Request Center `order_requests` remain the authoritative request status.
These helpers enrich Order Desk items for UI and keep accepted qty / history
intact across Partial / Reject / Re-Enquire cycles.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


CANCELLATION_REASONS = (
    'Wrong Part',
    'Wrong Qty',
    'Duplicate Entry',
    'Purchased Outside',
    'No Longer Required',
    'Other',
)

# UI-facing Order Desk request statuses (mapped from Request Center where needed)
REQUEST_STATUS_READY = 'Ready to Send'
REQUEST_STATUS_SENT = 'Request Sent'
REQUEST_STATUS_ACCEPTED = 'Accepted'
REQUEST_STATUS_PARTIAL = 'Partially Accepted'
REQUEST_STATUS_REJECTED = 'Rejected'
REQUEST_STATUS_CANCEL_REQ = 'Cancellation Requested'
REQUEST_STATUS_CANCELLED = 'Cancelled'
REQUEST_STATUS_COMPLETED = 'Completed'
REQUEST_STATUS_FACTORY = 'No Further Stock Available'
REQUEST_STATUS_REMAINING = 'Remaining Qty'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or '').strip()


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def allocation_level(source: dict, order: dict) -> str:
    """Classify an allocation as branch (same dealer) or dealer (other dealer)."""
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


def compact_requested_from(history_rows: List[dict], allocations: List[dict], order: dict) -> str:
    """Build compact main-row 'Requested From' text from history + current allocations."""
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
        add(dealer, branch, qty, level)

    for alloc in allocations or []:
        # Prefer history for already-sent rows; only show unsent Selected allocations here
        if alloc.get('request_no') or alloc.get('request_number'):
            continue
        dealer = _clean(alloc.get('dealer_name') or alloc.get('source_dealer'))
        branch = _clean(alloc.get('branch') or alloc.get('source_branch'))
        qty = _f(alloc.get('request_qty') or alloc.get('requested_qty'))
        level = allocation_level(alloc, order)
        add(dealer, branch, qty, level)

    return ' | '.join(parts)


def compute_item_workflow(item: dict, order: dict, item_requests: List[dict], header_emails: Dict[str, dict] = None) -> dict:
    """Derive accepted/remaining/factory/request_status/history for one order item."""
    header_emails = header_emails or {}
    required = _f(item.get('required_qty'))
    history = []
    accepted_total = 0.0
    active_requested = 0.0
    has_sent = False
    has_partial = False
    has_rejected = False
    has_cancel_req = False
    all_terminal_rejected = True
    any_completed = False

    for req in item_requests or []:
        requested = _f(req.get('requested_qty'))
        accepted = _f(req.get('accepted_qty', req.get('approved_qty')))
        status = _clean(req.get('status'))
        ui_status = map_request_center_status(status, requested, accepted)
        if item.get('cancellation_status') == 'pending' and req.get('id') == item.get('cancellation_request_id'):
            ui_status = REQUEST_STATUS_CANCEL_REQ
            has_cancel_req = True

        email_meta = header_emails.get(req.get('request_number') or '', {})
        email_status = email_status_label(
            req.get('email_status') or email_meta.get('email_status') or ('sent' if email_meta.get('email_sent') else 'pending')
        )
        level = allocation_level(
            {'dealer_name': req.get('supplying_dealer'), 'branch': req.get('supplying_branch'),
             'level': req.get('source_type') or req.get('level')},
            order,
        )
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
            'remaining_qty': max(0.0, requested - accepted) if status in ('Approved', 'Partially Approved', 'Rejected', 'Cancelled')
                else max(0.0, requested - accepted),
            'request_no': req.get('request_number'),
            'request_id': req.get('id'),
            'request_status': ui_status,
            'email_status': email_status,
            'remarks': req.get('approval_remarks') or req.get('remarks') or '',
            'status_raw': status,
            'level': level,
        })

        if status not in ('Rejected', 'Cancelled'):
            all_terminal_rejected = False
        if status in ('Approved', 'Partially Approved', 'Dispatched', 'Received', 'Completed'):
            accepted_total += accepted
            # Remaining of a partial still needs fulfilment beyond this request.
        if status == 'Requested':
            has_sent = True
            active_requested += requested
        if status == 'Approved' and accepted < requested:
            has_partial = True
        if status == 'Rejected':
            has_rejected = True
        if status == 'Completed':
            any_completed = True
        if status in ('Approved', 'Dispatched', 'Received', 'Completed') and accepted >= requested:
            pass

    # Count accepted from approved lines only (already summed). Remaining =
    # required - accepted - still-pending requested qty.
    remaining = max(0.0, required - accepted_total - active_requested)

    # Unsent selected allocations count toward "Ready to Send"
    unsent_alloc_qty = 0.0
    for alloc in item.get('allocations') or []:
        if alloc.get('request_no') or alloc.get('request_number') or _clean(alloc.get('status')).lower() in (
            'requested', 'request sent', 'accepted', 'partially accepted', 'rejected', 'cancelled', 'completed',
        ):
            continue
        unsent_alloc_qty += _f(alloc.get('request_qty'))

    factory_order_qty = _f(item.get('factory_order_qty'))
    if item.get('cancellation_status') == 'approved' or _clean(item.get('request_status')) == REQUEST_STATUS_CANCELLED:
        request_status = REQUEST_STATUS_CANCELLED
    elif item.get('cancellation_status') == 'pending' or has_cancel_req:
        request_status = REQUEST_STATUS_CANCEL_REQ
    elif factory_order_qty > 0 and remaining <= factory_order_qty and remaining > 0 and not unsent_alloc_qty and not active_requested:
        request_status = REQUEST_STATUS_FACTORY
    elif any_completed and remaining <= 0 and active_requested <= 0:
        request_status = REQUEST_STATUS_COMPLETED
    elif accepted_total >= required and remaining <= 0 and active_requested <= 0:
        request_status = REQUEST_STATUS_ACCEPTED if not has_partial else REQUEST_STATUS_PARTIAL
    elif has_sent and active_requested > 0:
        request_status = REQUEST_STATUS_SENT
    elif has_partial and remaining > 0:
        request_status = REQUEST_STATUS_PARTIAL
    elif has_rejected and remaining > 0 and accepted_total <= 0 and not unsent_alloc_qty:
        request_status = REQUEST_STATUS_REJECTED
    elif has_rejected and remaining > 0:
        request_status = REQUEST_STATUS_REMAINING if not unsent_alloc_qty else REQUEST_STATUS_READY
    elif remaining > 0 and unsent_alloc_qty > 0:
        request_status = REQUEST_STATUS_READY
    elif remaining > 0 and (item.get('retry_required') or item.get('re_enquire')):
        request_status = REQUEST_STATUS_REMAINING
    elif remaining > 0 and not history:
        request_status = item.get('availability_status') or item.get('status') or 'Order Created'
    elif remaining <= 0 and accepted_total > 0:
        request_status = REQUEST_STATUS_ACCEPTED
    else:
        request_status = item.get('request_status') or item.get('status') or 'Order Created'

    # If branches exhausted flag set and remaining remains with no sources left:
    if item.get('no_further_stock') and remaining > 0:
        request_status = REQUEST_STATUS_FACTORY
        factory_order_qty = remaining

    requested_from = compact_requested_from(history, item.get('allocations') or [], order)

    return {
        'accepted_qty': accepted_total,
        'remaining_qty': remaining,
        'factory_order_qty': factory_order_qty if request_status == REQUEST_STATUS_FACTORY else (factory_order_qty or 0),
        'request_status': request_status,
        'request_history': history,
        'requested_from': requested_from,
        'allocated_qty': accepted_total + active_requested + unsent_alloc_qty,
        'has_unsent_allocations': unsent_alloc_qty > 0,
        'unsent_allocation_qty': unsent_alloc_qty,
        'source_type_summary': _source_type_summary(history, item.get('allocations') or [], order),
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


async def enrich_order_items(db, order: dict, items: List[dict]) -> List[dict]:
    """Attach request history + derived workflow fields without mutating stored schema requirements."""
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
    if request_numbers:
        async for h in db.request_headers.find(
            {'request_number': {'$in': list(request_numbers)}},
            {'_id': 0, 'request_number': 1, 'email_sent': 1, 'email_status': 1, 'email_error': 1},
        ):
            headers[h.get('request_number')] = h

    enriched = []
    for item in items:
        wf = compute_item_workflow(item, order, by_item.get(item.get('id'), []), headers)
        # Normalize allocations with source_type for UI
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
            })
        enriched.append({
            **item,
            **wf,
            'allocations': normalized_allocs,
            'balance_qty': wf['remaining_qty'],
        })
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
    """Preserve accepted qty + history; mark remaining for re-enquiry; never wipe request trail."""
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

    update: Dict[str, Any] = {
        'accepted_qty': wf['accepted_qty'],
        'remaining_qty': wf['remaining_qty'],
        'request_status': wf['request_status'],
        'updated_at': now,
    }

    # Keep previously selected allocations that were already sent; drop only the
    # matching source's unsent draft allocation so Re-Enquire can pick a new one.
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
            }
        kept_allocs.append(alloc)

    update['allocations'] = kept_allocs
    update['allocated_qty'] = sum(_f(a.get('request_qty')) for a in kept_allocs)

    needs_retry = wf['remaining_qty'] > 0 and status in ('Rejected', 'Cancelled', 'Approved') and (
        status != 'Approved' or accepted < requested
    )
    if needs_retry:
        update['retry_required'] = True
        update['re_enquire'] = True
        update['status'] = 'Pending Retry'
        # Exclude this source from Auto Suggest unless business rules require otherwise
        await db.order_items.update_one(
            {'id': item_id},
            {'$set': update, '$addToSet': {'excluded_sources': failed_source}},
        )
    else:
        if wf['remaining_qty'] <= 0:
            update['retry_required'] = False
            update['re_enquire'] = False
            update['status'] = 'Accepted' if wf['request_status'] == REQUEST_STATUS_ACCEPTED else item.get('status')
        await db.order_items.update_one({'id': item_id}, {'$set': update})

    # Detect factory order: remaining qty with no eligible unused branch/dealer sources
    refreshed = await db.order_items.find_one({'id': item_id}, {'_id': 0})
    if refreshed and _f(refreshed.get('remaining_qty')) > 0:
        excluded = {
            source_key(x.get('dealer_name'), x.get('branch'))
            for x in (refreshed.get('excluded_sources') or [])
        }
        branch_pool = [
            s for s in (refreshed.get('same_dealer_sources') or [])
            if source_key(s.get('dealer_name'), s.get('branch')) not in excluded
            and _f(s.get('net_available_qty', s.get('available_qty'))) > 0
        ]
        dealer_pool = [
            s for s in (refreshed.get('other_dealer_sources') or [])
            if source_key(s.get('dealer_name'), s.get('branch')) not in excluded
            and _f(s.get('net_available_qty', s.get('available_qty'))) > 0
        ]
        # Factory only when both pools are exhausted (dealer stage reachable after branch exhausted)
        branch_done = not branch_pool
        if branch_done and not dealer_pool:
            rem = _f(refreshed.get('remaining_qty'))
            await db.order_items.update_one({'id': item_id}, {'$set': {
                'no_further_stock': True,
                'factory_order_qty': rem,
                'request_status': REQUEST_STATUS_FACTORY,
                'status': REQUEST_STATUS_FACTORY,
                'updated_at': now,
            }})


def filter_unsent_allocations(item: dict, order: dict, level: str, existing_requests: List[dict]) -> List[dict]:
    """Return allocations for the given level that do not already have an active/sent request."""
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
                'request_status': REQUEST_STATUS_SENT,
                'status': REQUEST_STATUS_SENT,
                'sent_at': now,
            })
        else:
            updated.append(alloc)
    return updated
