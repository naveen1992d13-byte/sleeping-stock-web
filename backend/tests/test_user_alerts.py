"""Focused tests for additive Request/Notice/Query user alerts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import user_alerts as ua


class FakeCollection:
    def __init__(self):
        self.docs = []
        self.indexes = []

    async def insert_one(self, doc):
        key = (doc.get("recipient_id"), doc.get("source_type"), doc.get("source_id"), doc.get("event"))
        for d in self.docs:
            k2 = (d.get("recipient_id"), d.get("source_type"), d.get("source_id"), d.get("event"))
            if k2 == key:
                err = Exception("E11000 duplicate key")
                err.code = 11000
                raise err
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id="x")

    def find(self, query=None, projection=None):
        class Cursor:
            def __init__(self, rows):
                self.rows = rows

            def sort(self, *a, **k):
                return self

            async def to_list(self, n=None):
                return list(self.rows)[: n or len(self.rows)]

        rows = [dict(d) for d in self.docs if self._match(d, query or {})]
        return Cursor(rows)

    async def find_one(self, query=None, projection=None):
        for d in self.docs:
            if self._match(d, query or {}):
                return dict(d)
        return None

    async def count_documents(self, query):
        return len([d for d in self.docs if self._match(d, query)])

    async def update_one(self, query, update):
        for i, d in enumerate(self.docs):
            if self._match(d, query):
                if "$set" in update:
                    self.docs[i] = {**d, **update["$set"]}
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, query, update):
        n = 0
        for i, d in enumerate(self.docs):
            if self._match(d, query):
                if "$set" in update:
                    self.docs[i] = {**d, **update["$set"]}
                n += 1
        return SimpleNamespace(matched_count=n, modified_count=n)

    async def create_index(self, *a, **k):
        self.indexes.append((a, k))

    def _match(self, doc, query):
        for k, v in (query or {}).items():
            if k == "$or":
                if not any(self._match(doc, clause) for clause in v):
                    return False
                continue
            dv = doc.get(k)
            if isinstance(v, dict):
                if "$in" in v and dv not in v["$in"]:
                    return False
                if "$regex" in v:
                    import re

                    if not re.search(str(v["$regex"]), str(dv or ""), flags=re.I if v.get("$options") else 0):
                        return False
            elif dv != v:
                return False
        return True


class FakeDB:
    def __init__(self):
        self.user_alerts = FakeCollection()
        self.users = FakeCollection()


def test_create_user_alert_dedupes_and_filters_sources():
    async def _run():
        database = FakeDB()
        ua.init_user_alerts(database, None, None)
        a1 = await ua.create_user_alert(
            recipient_id="u1",
            source_type="request",
            source_id="r1",
            event="Request Accepted@t1",
            title="Request Accepted",
            message="RN1",
        )
        a2 = await ua.create_user_alert(
            recipient_id="u1",
            source_type="request",
            source_id="r1",
            event="Request Accepted@t1",
            title="Request Accepted",
            message="RN1",
        )
        # Same event name at a later transition time must NOT be suppressed
        a3 = await ua.create_user_alert(
            recipient_id="u1",
            source_type="request",
            source_id="r1",
            event="Request Accepted@t2",
            title="Request Accepted",
            message="RN1",
        )
        bad = await ua.create_user_alert(
            recipient_id="u1",
            source_type="order",
            source_id="o1",
            event="x",
            title="x",
        )
        assert a1 is not None
        assert a2 is None
        assert a3 is not None
        assert bad is None
        assert len(database.user_alerts.docs) == 2

    asyncio.get_event_loop().run_until_complete(_run())


def test_alert_query_reply_targets_creator_only():
    async def _run():
        database = FakeDB()
        database.users.docs = [
            {"id": "creator-uuid", "user_id": "SSU1", "email": "c@x.com", "role": "user", "status": "Active"},
            {"id": "master-1", "user_id": "SSM1", "email": "m@x.com", "role": "master", "status": "Active"},
        ]
        ua.init_user_alerts(database, None, None)
        n = await ua.alert_query_reply(
            {
                "id": "q1",
                "query_no": "QD1",
                "subject": "Help",
                "raised_by": {"user_id": "SSU1"},
                "replies": [{"reply_id": "r1"}],
            }
        )
        assert n == 1
        assert database.user_alerts.docs[0]["recipient_id"] == "creator-uuid"
        assert database.user_alerts.docs[0]["source_type"] == "query"
        assert database.user_alerts.docs[0]["source_id"] == "q1:reply:r1"

    asyncio.get_event_loop().run_until_complete(_run())


def test_ensure_indexes_creates_dedupe_and_list_indexes():
    async def _run():
        database = FakeDB()
        ua.init_user_alerts(database, None, None)
        await ua.ensure_indexes()
        assert len(database.user_alerts.indexes) >= 2
        names = [kwargs.get("name") for (_a, kwargs) in database.user_alerts.indexes]
        assert "user_alerts_dedupe" in names

    asyncio.get_event_loop().run_until_complete(_run())


def test_alert_notice_published_scopes_selected_brand():
    async def _run():
        database = FakeDB()
        database.users.docs = [
            {"id": "m1", "role": "master", "status": "Active", "brand": "X"},
            {"id": "a1", "role": "admin", "status": "Active", "brand": "Honda"},
            {"id": "a2", "role": "admin", "status": "Active", "brand": "Yamaha"},
            {"id": "u1", "role": "user", "status": "Active", "brand": "Honda"},
            {"id": "inactive", "role": "user", "status": "Inactive", "brand": "Honda"},
        ]
        ua.init_user_alerts(database, None, None)
        n = await ua.alert_notice_published(
            {
                "id": "n1",
                "title": "Brand notice",
                "priority": "High",
                "audience_type": "selected_brand",
                "brand_name": "Honda",
                "published_at": "2026-08-12T00:00:00+00:00",
            }
        )
        recipients = {d["recipient_id"] for d in database.user_alerts.docs}
        assert n == 3
        assert recipients == {"m1", "a1", "u1"}
        assert "a2" not in recipients
        assert "inactive" not in recipients

    asyncio.get_event_loop().run_until_complete(_run())


def test_alert_query_follow_up_targets_masters():
    async def _run():
        database = FakeDB()
        database.users.docs = [
            {"id": "m1", "role": "master", "status": "Active"},
            {"id": "m2", "role": "master", "status": "Active"},
            {"id": "a1", "role": "admin", "status": "Active"},
            {"id": "creator", "role": "user", "status": "Active"},
        ]
        ua.init_user_alerts(database, None, None)
        n = await ua.alert_query_follow_up(
            {
                "id": "q1",
                "query_no": "QD9",
                "subject": "Follow",
                "follow_ups": [{"follow_up_id": "f1"}],
            },
            actor_id="creator",
        )
        recipients = {d["recipient_id"] for d in database.user_alerts.docs}
        assert n == 2
        assert recipients == {"m1", "m2"}
        assert database.user_alerts.docs[0]["source_id"] == "q1:followup:f1"

    asyncio.get_event_loop().run_until_complete(_run())


def test_request_scope_excludes_unrelated_and_actor():
    async def _run():
        database = FakeDB()
        database.users.docs = [
            {"id": "requester", "email": "req@x.com", "role": "user", "status": "Active", "user_id": "U1"},
            {
                "id": "supplier_admin",
                "role": "admin",
                "status": "Active",
                "group": "Supply Dealer",
                "location": "Supply Branch",
            },
            {
                "id": "other_admin",
                "role": "admin",
                "status": "Active",
                "group": "Other Dealer",
                "location": "Other Branch",
            },
            {"id": "brand_master", "role": "master", "status": "Active", "brand": "Hyundai"},
            {"id": "other_master", "role": "master", "status": "Active", "brand": "Honda"},
            {"id": "actor_admin", "role": "admin", "status": "Active", "group": "Supply Dealer", "location": "Supply Branch"},
        ]
        ua.init_user_alerts(database, None, None)
        n = await ua.alert_request_event(
            {
                "id": "req-1",
                "request_number": "RN1",
                "part_number": "P1",
                "requested_by": "requester",
                "requester_email": "req@x.com",
                "supplying_brand": "Hyundai",
                "supplying_dealer": "Supply Dealer",
                "supplying_branch": "Supply Branch",
                "decided_at": "2026-08-12T01:00:00+00:00",
                "decided_by": "actor_admin",
            },
            "Request Accepted",
            actor_id="actor_admin",
        )
        recipients = {d["recipient_id"] for d in database.user_alerts.docs}
        assert "requester" in recipients
        assert "supplier_admin" in recipients
        assert "brand_master" in recipients
        assert "other_admin" not in recipients
        assert "other_master" not in recipients
        assert "actor_admin" not in recipients
        assert n == 3
        assert all(d["event"].startswith("Request Accepted@") for d in database.user_alerts.docs)

    asyncio.get_event_loop().run_until_complete(_run())


def test_request_scope_empty_without_dealer_branch():
    async def _run():
        database = FakeDB()
        database.users.docs = [
            {"id": "m1", "role": "master", "status": "Active", "brand": "Hyundai"},
            {"id": "a1", "role": "admin", "status": "Active", "group": "D", "location": "B"},
        ]
        ua.init_user_alerts(database, None, None)
        # brand-only still reaches brand masters
        ids = await ua.active_user_ids_for_request_scope("Hyundai", None, None)
        assert ids == ["m1"]
        # no brand/dealer/branch → nobody
        ids2 = await ua.active_user_ids_for_request_scope(None, None, None)
        assert ids2 == []

    asyncio.get_event_loop().run_until_complete(_run())
