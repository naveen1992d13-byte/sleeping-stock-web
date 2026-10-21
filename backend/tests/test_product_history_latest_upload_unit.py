"""Product Hub History counts only a branch's last publish of the day (pure unit)."""
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import hybrid_history as hh  # noqa: E402


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, _n):
        return list(self._rows)


class _Products:
    def __init__(self, rows):
        self.rows = rows

    def aggregate(self, pipeline):
        groups = {}
        for row in self.rows:
            key = (row['brand_name'], row['dealer_name'], row['branch'], row.get('upload_id'))
            groups[key] = max(groups.get(key, ''), row['published_at'])
        return _Cursor([
            {'_id': {'brand': k[0], 'dealer': k[1], 'branch': k[2], 'upload_id': k[3]}, 'last_published_at': v}
            for k, v in groups.items()
        ])


class _Db:
    def __init__(self, rows):
        self.products = _Products(rows)


def _row(branch, upload_id, published_at):
    return {'brand_name': 'Hyundai', 'dealer_name': 'FPL', 'branch': branch,
            'upload_id': upload_id, 'published_at': published_at}


def test_republished_branch_keeps_only_latest_upload():
    db = _Db([
        _row('Vanagaram', 'u1', '2026-10-17T07:00:00+05:30'),
        _row('Vanagaram', 'u9', '2026-10-17T08:16:00+05:30'),
        _row('Koyambedu', 'u2', '2026-10-17T07:10:00+05:30'),
    ])
    query = asyncio.run(hh._restrict_to_latest_uploads(db, {'publish_status': 'Published'}))
    assert query['upload_id'] == {'$in': ['u2', 'u9']}
    assert query['publish_status'] == 'Published'


def test_single_publish_per_branch_leaves_query_unchanged():
    db = _Db([_row('Vanagaram', 'u1', '2026-10-17T07:00:00+05:30')])
    query = {'publish_status': 'Published'}
    assert asyncio.run(hh._restrict_to_latest_uploads(db, query)) == query
