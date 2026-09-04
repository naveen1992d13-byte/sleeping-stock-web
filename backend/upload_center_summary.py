"""Upload Center today-summary rules (pure helpers, no I/O).

Identity: (brand, dealer, branch).
Completed uploaders = unique identities with a Published upload today.
Waiting is never Completed.
Item/qty/value cards use the latest valid upload per identity so duplicate
Waiting files from the same branch do not inflate totals.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

Identity = Tuple[str, str, str]


def _norm(value: Any) -> str:
    return str(value or "").strip()


def upload_identity(doc: dict) -> Identity:
    brand = _norm(doc.get("brand_name") or doc.get("brand"))
    dealer = _norm(doc.get("dealer_name") or doc.get("dealer"))
    branch = _norm(doc.get("branch") or doc.get("name"))
    return (brand, dealer, branch)


def branch_identity(doc: dict) -> Identity:
    brand = _norm(doc.get("brand_name") or doc.get("brand"))
    dealer = _norm(doc.get("dealer") or doc.get("dealer_name"))
    branch = _norm(doc.get("name") or doc.get("branch"))
    return (brand, dealer, branch)


def identity_complete(key: Identity) -> bool:
    return bool(key[0] and key[1] and key[2])


def is_cancelled(doc: dict) -> bool:
    return _norm(doc.get("status")) == "Cancelled" or _norm(doc.get("publish_status")) == "Cancelled"


def is_failed(doc: dict) -> bool:
    return _norm(doc.get("status")) == "Failed" or _norm(doc.get("publish_status")) == "Failed"


def is_published(doc: dict) -> bool:
    return (not is_cancelled(doc)) and _norm(doc.get("publish_status")) == "Published"


def is_waiting(doc: dict) -> bool:
    return (not is_cancelled(doc)) and (not is_failed(doc)) and (not is_published(doc))


def is_valid(doc: dict) -> bool:
    """Latest-valid = not cancelled and not failed (Waiting or Published)."""
    return (not is_cancelled(doc)) and (not is_failed(doc))


def _sort_ts(doc: dict) -> Tuple[str, str, str]:
    return (
        str(doc.get("created_at") or ""),
        str(doc.get("upload_time") or ""),
        str(doc.get("id") or doc.get("upload_no") or ""),
    )


def latest_per_identity(docs: Iterable[dict], predicate: Optional[Callable[[dict], bool]] = None) -> Dict[Identity, dict]:
    grouped: Dict[Identity, dict] = {}
    for doc in docs:
        if predicate and not predicate(doc):
            continue
        key = upload_identity(doc)
        if not identity_complete(key):
            continue
        prev = grouped.get(key)
        if prev is None or _sort_ts(doc) > _sort_ts(prev):
            grouped[key] = doc
    return grouped


def _items_of(doc: dict) -> int:
    return int(doc.get("item_count", doc.get("rows_imported", 0)) or 0)


def _qty_of(doc: dict) -> float:
    return float(doc.get("total_available_qty", 0) or 0)


def _value_of(doc: dict) -> float:
    return float(doc.get("total_value", 0) or 0)


def _sum_metrics(docs: Iterable[dict]) -> Dict[str, float]:
    items = 0
    qty = 0.0
    value = 0.0
    for doc in docs:
        items += _items_of(doc)
        qty += _qty_of(doc)
        value += _value_of(doc)
    return {"items": items, "qty": qty, "value": value}


def expected_identities_from_branches(branch_docs: Iterable[dict]) -> Set[Identity]:
    keys: Set[Identity] = set()
    for doc in branch_docs:
        status = _norm(doc.get("status")).lower()
        if status in {"inactive", "disabled", "deleted"}:
            continue
        key = branch_identity(doc)
        if identity_complete(key):
            keys.add(key)
    return keys


def summarize_today(uploads_today: List[dict], expected_pairs: Set[Identity]) -> dict:
    """Build Upload Center today cards from today's upload documents."""
    cancelled_recs = [u for u in uploads_today if is_cancelled(u)]
    failed_recs = [u for u in uploads_today if is_failed(u) and not is_cancelled(u)]
    waiting_recs = [u for u in uploads_today if is_waiting(u)]
    published_recs = [u for u in uploads_today if is_published(u)]
    valid_recs = [u for u in uploads_today if is_valid(u)]

    latest_valid = latest_per_identity(valid_recs)
    latest_published = latest_per_identity(published_recs)
    latest_waiting = latest_per_identity(waiting_recs)

    pending_by_key: Dict[Identity, dict] = {}
    for key, waiting in latest_waiting.items():
        published = latest_published.get(key)
        if published is None or _sort_ts(waiting) > _sort_ts(published):
            pending_by_key[key] = waiting

    uploaded_m = _sum_metrics(latest_valid.values())
    published_m = _sum_metrics(latest_published.values())
    pending_m = _sum_metrics(pending_by_key.values())

    uploaded_keys = set(latest_valid.keys())
    published_keys = set(latest_published.keys())
    completed_keys = {key for key in published_keys if key in expected_pairs}

    expected_n = len(expected_pairs)
    completed_n = len(completed_keys)
    return {
        "brandsUploaded": len({key[0] for key in uploaded_keys}),
        "dealersUploaded": len({key[1] for key in uploaded_keys}),
        "branchesUploaded": len(uploaded_keys),
        "expectedUploads": expected_n,
        "completedUploads": completed_n,
        "balanceUploads": max(expected_n - completed_n, 0),
        "published": len(published_recs),
        "pending": len(waiting_recs),
        "cancelled": len(cancelled_recs),
        "failed": len(failed_recs),
        "uploadedItems": uploaded_m["items"],
        "uploadedQty": uploaded_m["qty"],
        "uploadedValue": uploaded_m["value"],
        "publishedItems": published_m["items"],
        "publishedQty": published_m["qty"],
        "publishedValue": published_m["value"],
        "pendingItems": pending_m["items"],
        "pendingQty": pending_m["qty"],
        "pendingValue": pending_m["value"],
        "published_keys": published_keys,
        "completed_keys": completed_keys,
        "latest_valid_ids": [u.get("id") for u in latest_valid.values() if u.get("id")],
        "latest_published_ids": [u.get("id") for u in latest_published.values() if u.get("id")],
        "pending_waiting_ids": [u.get("id") for u in pending_by_key.values() if u.get("id")],
    }


def balance_rows(expected_pairs: Set[Identity], completed_keys: Set[Identity]) -> dict:
    completed = []
    pending = []
    for brand, dealer, branch in sorted(expected_pairs):
        row = {
            "brand": brand,
            "dealer": dealer,
            "branch": branch,
            "upload_status": "Completed" if (brand, dealer, branch) in completed_keys else "Pending",
        }
        if row["upload_status"] == "Completed":
            completed.append(row)
        else:
            pending.append(row)
    return {"completed": completed, "pending": pending}
