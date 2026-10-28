"""``client.docstore()`` — Nexus DocStore, the document database (sync).

::

    from nexus import NexusClient
    from nexus.docstore import increment, where, or_, count, sum_

    db = NexusClient().docstore()              # service key from NEXUS_DOCSTORE_SERVICE_KEY
    db.deploy_collection("orders", {"rules": {...}, "searchFields": ["note"]})
    db.collection("orders").doc("o1").update({"total": increment(5)})
    open_orders = db.collection("orders").where("status", "==", "open").order_by("total", desc=True).limit(20).get()

    user_db = db.as_user(id_token)             # same API, security rules apply as that user

    for change in db.watch_changes(since=last_seq):   # resumable CDC
        index(change); last_seq = change.seq

Unlike the telemetry resources, every DocStore call RAISES on failure (also
with ``silent=True``): a database read that quietly returns ``None`` is
indistinguishable from missing data.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, TypeVar, Union

from .. import _payloads as p
from .._docstore import (
    Change,
    DocumentSnapshot,
    DocumentVersion,
    QueryResult,
    QueryState,
    WriteResult,
    and_filter,
    backoff,
    iso,
    parse_changes,
    parse_history,
    parse_query,
    parse_results,
    parse_write,
    snapshot,
    to_update_spec,
    transaction_ops,
    where,
)
from ..exceptions import ConflictError, ConfigurationError, NotFoundError, ServiceDisabledError

if TYPE_CHECKING:  # pragma: no cover
    from datetime import datetime

    from ..client import NexusClient

__all__ = ["DocStore", "CollectionRef", "DocumentRef", "Query", "WriteBatch", "Transaction"]

T = TypeVar("T")
SERVICE_KEY_ENV = "NEXUS_DOCSTORE_SERVICE_KEY"


def auth_headers(service_key: Optional[str], token: Optional[str]) -> Dict[str, str]:
    if token:
        return {"Authorization": "Bearer " + token}
    if service_key:
        return {"x-nexus-service-key": service_key}
    return {}


class DocStore:
    """A DocStore client bound to one identity (service key, end user, or anonymous)."""

    def __init__(self, client: "NexusClient", *, service_key: Optional[str] = None, token: Optional[str] = None) -> None:
        self._client = client
        self._service_key = service_key
        self._token = token
        self.headers = auth_headers(service_key, token)

    # ── identity ─────────────────────────────────────────────────────────────

    def as_user(self, token: str) -> "DocStore":
        """The same database as one end user (their JWT): security rules apply."""
        return DocStore(self._client, service_key=self._service_key, token=token)

    def anonymous(self) -> "DocStore":
        """As an anonymous app user — rules see ``auth == null``."""
        return DocStore(self._client)

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _exec(self, request: p.Request) -> Any:
        if not self._client.config.service_enabled("docstore"):
            raise ServiceDisabledError("DocStore is switched off (NEXUS_DOCSTORE_ENABLED / services)")
        # Straight to the engine: synchronous and raising, whatever dispatcher
        # or `silent` setting the telemetry side of the client uses.
        return self._client.engine.execute(request)

    # ── entry points ─────────────────────────────────────────────────────────

    def collection(self, name: str) -> "CollectionRef":
        return CollectionRef(self, name)

    def doc(self, path: str) -> "DocumentRef":
        """``doc("posts/p1")``."""
        collection, _, doc_id = path.partition("/")
        if not collection or not doc_id:
            raise ValueError("{0!r} is not a <collection>/<id> path".format(path))
        return DocumentRef(self, collection, doc_id)

    def batch(self) -> "WriteBatch":
        return WriteBatch(self)

    def run_transaction(self, fn: Callable[["Transaction"], T], *, max_attempts: int = 5) -> T:
        """Run ``fn(tx)`` optimistically: reads record versions, writes carry
        them, and a concurrent change makes the commit fail and ``fn`` re-run."""
        attempt = 0
        while True:
            attempt += 1
            tx = Transaction(self)
            result = fn(tx)
            try:
                tx.commit()
                return result
            except ConflictError as exc:
                if not exc.is_precondition_failed or attempt >= max_attempts:
                    raise
                time.sleep(backoff(attempt))

    def changes(self, *, since: int = 0, collections: Optional[Sequence[str]] = None, limit: Optional[int] = None) -> "tuple[List[Change], int, bool]":
        """One page of the change stream (service key): ``(changes, next_seq, has_more)``."""
        return parse_changes(self._exec(p.docstore_changes(self.headers, since=since, collections=collections, limit=limit)))

    def watch_changes(self, *, since: int = 0, collections: Optional[Sequence[str]] = None, poll_interval: float = 1.0, stop: Optional[Callable[[], bool]] = None) -> Iterator[Change]:
        """Every committed change after ``since``, in commit order, forever (or
        until ``stop()`` is true). Persist ``change.seq`` and pass it back as
        ``since`` to resume exactly where you left off after a restart."""
        cursor = since
        while not (stop and stop()):
            batch, cursor, more = self.changes(since=cursor, collections=collections, limit=500)
            yield from batch
            if not more:
                time.sleep(poll_interval)

    def deploy_collection(self, name: str, config: Mapping[str, Any]) -> Dict[str, Any]:
        """Collections as code (service key): create or update to match ``config``
        (``rules``, ``schema``, ``schemaMode``, ``relations``, ``uniqueConstraints``,
        ``searchFields``, ``ttlField``, ``historyDays``, ``conflictStrategy`` …)."""
        return self._exec(p.docstore_deploy_collection(name, config, self.headers)) or {}

    def list_collections(self) -> List[Dict[str, Any]]:
        return list(self._exec(p.docstore_list_collections(self.headers)) or [])


class Query:
    """An immutable query; every builder returns a new one."""

    def __init__(self, db: DocStore, collection: str, state: Optional[QueryState] = None) -> None:
        self._db = db
        self.collection = collection
        self._state = state or QueryState()

    def _with(self, **changes: Any) -> "Query":
        s = QueryState(**{**self._state.__dict__, **changes})
        q = Query(self._db, self.collection, s)
        return q

    def where(self, field_or_filter: Union[str, Mapping[str, Any]], op: Optional[str] = None, value: Any = None) -> "Query":
        """``where("total", ">=", 100)`` or a composed filter (``or_(...)``, raw JSON)."""
        extra = where(field_or_filter, op or "==", value) if isinstance(field_or_filter, str) else dict(field_or_filter)
        return self._with(filter=and_filter(self._state.filter, extra))

    def order_by(self, field_path: str, *, desc: bool = False) -> "Query":
        key = {"field": "$id" if field_path == "id" else field_path, "dir": "desc" if desc else "asc"}
        return self._with(sort=[*self._state.sort, key])

    def limit(self, n: int) -> "Query":
        return self._with(limit=int(n))

    def start_after(self, cursor: str) -> "Query":
        """Keyset pagination: the ``next_cursor`` of the previous page."""
        return self._with(cursor=cursor)

    def offset(self, n: int) -> "Query":
        return self._with(offset=int(n))

    def select(self, *fields: str) -> "Query":
        return self._with(select=list(fields))

    def expand(self, *relations: str) -> "Query":
        return self._with(expand=[*(self._state.expand or []), *relations])

    def search(self, text: str) -> "Query":
        """Full-text search over the collection's search fields."""
        return self._with(search=text)

    def as_of(self, when: Union["datetime", str]) -> "Query":
        """Time travel: the collection as it was at ``when``."""
        return self._with(as_of=iso(when))

    def with_count(self) -> "Query":
        return self._with(count=True)

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self._state.body().items() if v is not None}

    def get(self) -> QueryResult:
        return parse_query(self.collection, self._db._exec(p.docstore_query(self.collection, self._state.body(), self._db.headers)))

    def stream(self, page_size: int = 200) -> Iterator[DocumentSnapshot]:
        """Every match, page by page (keyset — safe while documents change)."""
        q = self.limit(page_size)
        while True:
            page = q.get()
            yield from page.docs
            if not page.next_cursor:
                return
            q = q.start_after(page.next_cursor)

    def count(self) -> int:
        groups = self.aggregate({"n": {"op": "count"}})
        return int(groups[0]["values"]["n"] or 0) if groups else 0

    def aggregate(self, aggregations: Mapping[str, Mapping[str, Any]], *, group_by: Optional[Sequence[str]] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """``aggregate({"n": count(), "revenue": sum_("total")}, group_by=["status"])`` →
        ``[{"key": {"status": "paid"}, "values": {"n": 12, "revenue": 940.5}}, …]``."""
        body = {"filter": self._state.filter, "search": self._state.search, "asOf": self._state.as_of, "aggregations": dict(aggregations), "groupBy": list(group_by) if group_by else None, "limit": limit}
        raw = self._db._exec(p.docstore_aggregate(self.collection, body, self._db.headers)) or {}
        return list(raw.get("groups") or [])


class CollectionRef(Query):
    def __init__(self, db: DocStore, name: str) -> None:
        super().__init__(db, name)

    def doc(self, doc_id: str) -> "DocumentRef":
        return DocumentRef(self._db, self.collection, doc_id)

    def add(self, data: Mapping[str, Any], *, id: Optional[str] = None) -> DocumentSnapshot:  # noqa: A002
        """Create with a generated, time-sortable id (or ``id``)."""
        raw = self._db._exec(p.docstore_create(self.collection, data, self._db.headers, id))
        return parse_write(raw, self.collection, id or "")

    def restore(self, as_of: Union["datetime", str], *, dry_run: bool = False) -> Dict[str, Any]:
        """Point-in-time restore of the whole collection (service key). Every
        differing document is written back as a NEW version, so it is undoable."""
        return self._db._exec(p.docstore_restore_collection(self.collection, iso(as_of), self._db.headers, dry_run=dry_run)) or {}


class DocumentRef:
    def __init__(self, db: DocStore, collection: str, doc_id: str) -> None:
        self._db = db
        self.collection = collection
        self.id = doc_id

    @property
    def path(self) -> str:
        return "{0}/{1}".format(self.collection, self.id)

    def get(self, *, expand: Optional[Sequence[str]] = None, select: Optional[Sequence[str]] = None, as_of: Optional[Union["datetime", str]] = None) -> DocumentSnapshot:
        """Read; a missing document returns a snapshot with ``exists == False``."""
        try:
            raw = self._db._exec(p.docstore_get(self.collection, self.id, self._db.headers, expand=expand, select=select, as_of=iso(as_of) if as_of else None))
        except NotFoundError:
            raw = None
        return snapshot(self.collection, self.id, raw)

    def set(self, data: Mapping[str, Any], *, merge: bool = False, if_version: Optional[int] = None) -> DocumentSnapshot:
        """Create or replace (``merge=True`` deep-merges). ``if_version`` refuses to
        overwrite a change you haven't seen (0 = must not exist)."""
        return parse_write(self._db._exec(p.docstore_set(self.collection, self.id, data, self._db.headers, merge=merge, if_version=if_version)), self.collection, self.id)

    def update(self, data: Mapping[str, Any], *, if_version: Optional[int] = None, upsert: bool = False) -> DocumentSnapshot:
        """Partial update with field values: ``{"views": increment(1), "a.b": 2}``."""
        req = p.docstore_update(self.collection, self.id, to_update_spec(data), self._db.headers, if_version=if_version, upsert=upsert)
        return parse_write(self._db._exec(req), self.collection, self.id)

    def delete(self, *, if_version: Optional[int] = None) -> None:
        self._db._exec(p.docstore_delete(self.collection, self.id, self._db.headers, if_version=if_version))

    def history(self, *, limit: Optional[int] = None, before_version: Optional[int] = None) -> List[DocumentVersion]:
        return parse_history(self._db._exec(p.docstore_history(self.collection, self.id, self._db.headers, limit=limit, before_version=before_version)))

    def restore(self, version: int) -> DocumentSnapshot:
        """Back to an earlier version (written as a NEW version — undoable)."""
        return parse_write(self._db._exec(p.docstore_restore(self.collection, self.id, version, self._db.headers)), self.collection, self.id)


class WriteBatch:
    """Up to 500 writes committed atomically. Usable as a context manager —
    it commits on a clean exit and discards on an exception."""

    def __init__(self, db: DocStore) -> None:
        self._db = db
        self._ops: List[Dict[str, Any]] = []

    def create(self, ref: DocumentRef, data: Mapping[str, Any]) -> "WriteBatch":
        self._ops.append({"type": "create", "collection": ref.collection, "id": ref.id, "data": dict(data)})
        return self

    def set(self, ref: DocumentRef, data: Mapping[str, Any], *, merge: bool = False, if_version: Optional[int] = None) -> "WriteBatch":
        op: Dict[str, Any] = {"type": "set", "collection": ref.collection, "id": ref.id, "data": dict(data)}
        if merge:
            op["merge"] = True
        if if_version is not None:
            op["precondition"] = {"version": if_version}
        self._ops.append(op)
        return self

    def update(self, ref: DocumentRef, data: Mapping[str, Any], *, if_version: Optional[int] = None) -> "WriteBatch":
        op: Dict[str, Any] = {"type": "update", "collection": ref.collection, "id": ref.id, "update": to_update_spec(data)}
        if if_version is not None:
            op["precondition"] = {"version": if_version}
        self._ops.append(op)
        return self

    def delete(self, ref: DocumentRef, *, if_version: Optional[int] = None) -> "WriteBatch":
        op: Dict[str, Any] = {"type": "delete", "collection": ref.collection, "id": ref.id}
        if if_version is not None:
            op["precondition"] = {"version": if_version}
        self._ops.append(op)
        return self

    def _final_ops(self) -> List[Dict[str, Any]]:
        return self._ops

    def commit(self) -> List[WriteResult]:
        if not self._ops:
            return []
        return parse_results(self._db._exec(p.docstore_commit(self._final_ops(), self._db.headers)))

    def __enter__(self) -> "WriteBatch":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if exc_type is None:
            self.commit()


class Transaction(WriteBatch):
    """Read-modify-write. Use via :meth:`DocStore.run_transaction`."""

    def __init__(self, db: DocStore) -> None:
        super().__init__(db)
        self._seen: Dict[str, int] = {}

    def get(self, ref: DocumentRef) -> DocumentSnapshot:
        snap = ref.get()
        self._seen[ref.path] = snap.version
        return snap

    def _final_ops(self) -> List[Dict[str, Any]]:
        return transaction_ops(self._ops, self._seen)


def default_service_key(explicit: Optional[str]) -> Optional[str]:
    key = explicit or os.environ.get(SERVICE_KEY_ENV) or None
    if key is not None and not key.startswith("nxsk_"):
        raise ConfigurationError("a DocStore service key starts with 'nxsk_' (got {0!r}…)".format(key[:6]))
    return key
