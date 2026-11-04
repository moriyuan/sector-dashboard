"""待开发事项：研究台自身的轻量清单。独立建表，与行情快照互不干扰。"""
from fastapi import APIRouter, Body, HTTPException

from .store import now_iso

MAX_TITLE = 40
MAX_DETAIL = 300

_SCHEMA = '''CREATE TABLE IF NOT EXISTS backlog_items(
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, done_at TEXT)'''


def _ensure_schema(db):
    db.execute(_SCHEMA)
    cols = {r['name'] for r in db.execute('PRAGMA table_info(backlog_items)')}
    if 'detail' not in cols:  # 兼容早期只有 title 的旧表
        db.execute("ALTER TABLE backlog_items ADD COLUMN detail TEXT NOT NULL DEFAULT ''")


def router(store):
    with store.connect() as db:
        _ensure_schema(db)

    api = APIRouter()

    @api.get('/api/backlog')
    def listing():
        with store.connect() as db:
            rows = [dict(r) for r in db.execute(
                'SELECT * FROM backlog_items ORDER BY done, CASE WHEN done=0 THEN id END DESC, done_at DESC')]
        return {'rows': rows, 'open': sum(1 for r in rows if not r['done']),
                'done': sum(1 for r in rows if r['done'])}

    @api.post('/api/backlog')
    def add(title: str = Body(..., embed=True), detail: str = Body('', embed=True)):
        title = title.strip()
        detail = detail.strip()
        if not 1 <= len(title) <= MAX_TITLE:
            raise HTTPException(422, f'事项主题应为 1–{MAX_TITLE} 字')
        if len(detail) > MAX_DETAIL:
            raise HTTPException(422, f'描述最多 {MAX_DETAIL} 字')
        with store.connect() as db:
            cursor = db.execute('INSERT INTO backlog_items(title,detail,created_at) VALUES(?,?,?)',
                                (title, detail, now_iso()))
            row = db.execute('SELECT * FROM backlog_items WHERE id=?', (cursor.lastrowid,)).fetchone()
        return dict(row)

    @api.post('/api/backlog/{item_id}/done')
    def mark_done(item_id: int):
        with store.connect() as db:
            row = db.execute('SELECT * FROM backlog_items WHERE id=?', (item_id,)).fetchone()
            if not row:
                raise HTTPException(404, '没有该事项')
            if not row['done']:
                db.execute('UPDATE backlog_items SET done=1, done_at=? WHERE id=?', (now_iso(), item_id))
            row = db.execute('SELECT * FROM backlog_items WHERE id=?', (item_id,)).fetchone()
        return dict(row)

    def _get(db, item_id: int):
        row = db.execute('SELECT * FROM backlog_items WHERE id=?', (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, '没有该事项')
        return row

    @api.patch('/api/backlog/{item_id}')
    def edit(item_id: int, title: str | None = Body(None, embed=True),
             detail: str | None = Body(None, embed=True)):
        if title is None and detail is None:
            raise HTTPException(422, '没有要修改的内容')
        if title is not None:
            title = title.strip()
            if not 1 <= len(title) <= MAX_TITLE:
                raise HTTPException(422, f'事项主题应为 1–{MAX_TITLE} 字')
        if detail is not None:
            detail = detail.strip()
            if len(detail) > MAX_DETAIL:
                raise HTTPException(422, f'描述最多 {MAX_DETAIL} 字')
        with store.connect() as db:
            _get(db, item_id)
            if title is not None:
                db.execute('UPDATE backlog_items SET title=? WHERE id=?', (title, item_id))
            if detail is not None:
                db.execute('UPDATE backlog_items SET detail=? WHERE id=?', (detail, item_id))
            row = _get(db, item_id)
        return dict(row)

    @api.delete('/api/backlog/{item_id}')
    def remove(item_id: int):
        with store.connect() as db:
            _get(db, item_id)
            db.execute('DELETE FROM backlog_items WHERE id=?', (item_id,))
        return {'deleted': item_id}

    return api
