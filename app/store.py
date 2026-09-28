"""SQLite-backed ChatKit Store.

Follows the ChatKit guidance of storing models as JSON blobs, so library
updates can change the schema without database migrations. Threads are
scoped per user through RequestContext, so one user never sees another's
conversations.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from chatkit.store import NotFoundError, Store
from chatkit.types import Attachment, Page, ThreadItem, ThreadMetadata
from pydantic import TypeAdapter

_ITEM = TypeAdapter(ThreadItem)
_ATTACHMENT = TypeAdapter(Attachment)


@dataclass
class RequestContext:
    user_id: str


class SQLiteStore(Store[RequestContext]):
    def __init__(self, path: Path | str):
        self.path = str(path)
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS items (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    thread_id TEXT NOT NULL, user_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_items_thread ON items(thread_id, seq);
                CREATE INDEX IF NOT EXISTS idx_threads_user ON threads(user_id, created_at);
                """
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------ threads

    async def load_thread(self, thread_id: str, context: RequestContext) -> ThreadMetadata:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM threads WHERE id=? AND user_id=?", (thread_id, context.user_id)
            ).fetchone()
        if not row:
            raise NotFoundError(f"Thread {thread_id} not found")
        return ThreadMetadata.model_validate_json(row["data"])

    async def save_thread(self, thread: ThreadMetadata, context: RequestContext) -> None:
        # Save metadata only; items live in their own table.
        meta = ThreadMetadata.model_validate(thread.model_dump(include=set(ThreadMetadata.model_fields)))
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO threads (id, user_id, created_at, data) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (meta.id, context.user_id, meta.created_at.isoformat(), meta.model_dump_json()),
            )

    async def load_threads(
        self, limit: int, after: str | None, order: str, context: RequestContext
    ) -> Page[ThreadMetadata]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, data FROM threads WHERE user_id=? ORDER BY created_at "
                + ("DESC" if order == "desc" else "ASC"),
                (context.user_id,),
            ).fetchall()
        return _paginate([(r["id"], ThreadMetadata.model_validate_json(r["data"])) for r in rows], after, limit)

    async def delete_thread(self, thread_id: str, context: RequestContext) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM items WHERE thread_id=? AND user_id=?", (thread_id, context.user_id))
            conn.execute("DELETE FROM threads WHERE id=? AND user_id=?", (thread_id, context.user_id))

    # ------------------------------------------------------------ items

    async def load_thread_items(
        self, thread_id: str, after: str | None, limit: int, order: str, context: RequestContext
    ) -> Page[ThreadItem]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, data FROM items WHERE thread_id=? AND user_id=? ORDER BY seq "
                + ("DESC" if order == "desc" else "ASC"),
                (thread_id, context.user_id),
            ).fetchall()
        return _paginate([(r["id"], _ITEM.validate_json(r["data"])) for r in rows], after, limit)

    async def add_thread_item(self, thread_id: str, item: ThreadItem, context: RequestContext) -> None:
        await self.save_item(thread_id, item, context)

    async def save_item(self, thread_id: str, item: ThreadItem, context: RequestContext) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO items (id, thread_id, user_id, data) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (item.id, thread_id, context.user_id, _ITEM.dump_json(item).decode()),
            )

    async def load_item(self, thread_id: str, item_id: str, context: RequestContext) -> ThreadItem:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM items WHERE id=? AND thread_id=? AND user_id=?",
                (item_id, thread_id, context.user_id),
            ).fetchone()
        if not row:
            raise NotFoundError(f"Item {item_id} not found")
        return _ITEM.validate_json(row["data"])

    async def delete_thread_item(self, thread_id: str, item_id: str, context: RequestContext) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM items WHERE id=? AND thread_id=? AND user_id=?", (item_id, thread_id, context.user_id)
            )

    # ------------------------------------------------------------ attachments (not enabled in the UI yet)

    async def save_attachment(self, attachment: Attachment, context: RequestContext) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO attachments (id, user_id, data) VALUES (?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (attachment.id, context.user_id, _ATTACHMENT.dump_json(attachment).decode()),
            )

    async def load_attachment(self, attachment_id: str, context: RequestContext) -> Attachment:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM attachments WHERE id=? AND user_id=?", (attachment_id, context.user_id)
            ).fetchone()
        if not row:
            raise NotFoundError(f"Attachment {attachment_id} not found")
        return _ATTACHMENT.validate_json(row["data"])

    async def delete_attachment(self, attachment_id: str, context: RequestContext) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM attachments WHERE id=? AND user_id=?", (attachment_id, context.user_id))


def _paginate(rows: list[tuple[str, object]], after: str | None, limit: int) -> Page:
    start = 0
    if after:
        ids = [row_id for row_id, _ in rows]
        start = ids.index(after) + 1 if after in ids else len(rows)
    window = rows[start:start + limit]
    has_more = start + limit < len(rows)
    return Page(
        data=[obj for _, obj in window],
        has_more=has_more,
        after=window[-1][0] if window and has_more else None,
    )
