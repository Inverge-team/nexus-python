"""DocStore building blocks shared by the sync and asyncio clients.

Everything here is pure: field values, filters, the query body, snapshot
models, response parsing and trigger-webhook verification. The I/O lives in
:mod:`nexus.resources.docstore` (sync) and :mod:`nexus.aio.docstore` (async),
which differ only in ``await``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from .exceptions import NexusError

__all__ = [
    "FieldValue",
    "increment",
    "multiply",
    "minimum",
    "maximum",
    "array_union",
    "array_remove",
    "array_push",
    "server_timestamp",
    "delete_field",
    "merge_field",
    "to_update_spec",
    "where",
    "or_",
    "and_",
    "not_",
    "where_related",
    "count",
    "count_distinct",
    "sum_",
    "avg",
    "min_",
    "max_",
    "DocumentSnapshot",
    "QueryResult",
    "DocumentVersion",
    "Change",
    "WriteResult",
    "verify_webhook",
    "InvalidWebhookSignature",
]

JsonMap = Dict[str, Any]

# ── field values ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FieldValue:
    """A server-side atomic update for one field. Use the helpers below."""

    kind: str
    value: Any = None
    slice: Optional[int] = None


def increment(n: Union[int, float]) -> FieldValue:
    """Add ``n`` atomically (concurrent increments never lose)."""
    return FieldValue("inc", n)


def multiply(n: Union[int, float]) -> FieldValue:
    return FieldValue("mul", n)


def minimum(v: Any) -> FieldValue:
    """Keep the smaller of the stored value and ``v``."""
    return FieldValue("min", v)


def maximum(v: Any) -> FieldValue:
    return FieldValue("max", v)


def array_union(*values: Any) -> FieldValue:
    """Add values to an array if absent."""
    return FieldValue("union", list(values))


def array_remove(*values: Any) -> FieldValue:
    return FieldValue("remove", list(values))


def array_push(values: Sequence[Any], *, keep_last: Optional[int] = None) -> FieldValue:
    """Append (duplicates allowed); ``keep_last`` caps the array — a rolling log."""
    return FieldValue("push", list(values), -int(keep_last) if keep_last else None)


def server_timestamp() -> FieldValue:
    """The server's clock at commit (ISO-8601)."""
    return FieldValue("serverTime")


def delete_field() -> FieldValue:
    return FieldValue("delete")


def merge_field(value: Mapping[str, Any]) -> FieldValue:
    """Deep-merge an object into the field (``None`` values delete keys)."""
    return FieldValue("merge", dict(value))


def to_update_spec(update: Mapping[str, Any]) -> JsonMap:
    """``{"views": increment(1), "a.b": 2}`` → ``{"$inc": {...}, "$set": {...}}``.
    A mapping already written with ``$`` operators passes through."""
    if update and all(str(k).startswith("$") for k in update):
        return dict(update)
    spec: JsonMap = {}

    def put(op: str, f: str, v: Any) -> None:
        spec.setdefault(op, {})[f] = v

    def lst(op: str, f: str) -> None:
        spec.setdefault(op, []).append(f)

    for f, v in update.items():
        if not isinstance(v, FieldValue):
            put("$set", f, v)
        elif v.kind == "inc":
            put("$inc", f, v.value)
        elif v.kind == "mul":
            put("$mul", f, v.value)
        elif v.kind == "min":
            put("$min", f, v.value)
        elif v.kind == "max":
            put("$max", f, v.value)
        elif v.kind == "union":
            put("$addToSet", f, {"$each": v.value})
        elif v.kind == "remove":
            put("$pull", f, {"$in": v.value})
        elif v.kind == "push":
            put("$push", f, {"$each": v.value, **({"$slice": v.slice} if v.slice else {})})
        elif v.kind == "serverTime":
            lst("$serverTime", f)
        elif v.kind == "delete":
            lst("$unset", f)
        elif v.kind == "merge":
            put("$merge", f, v.value)
    return spec


# ── filters ──────────────────────────────────────────────────────────────────

_OPS = {
    "==": "$eq", "!=": "$ne", "<": "$lt", "<=": "$lte", ">": "$gt", ">=": "$gte",
    "in": "$in", "not-in": "$nin", "between": "$between",
    "array-contains": "$contains", "array-contains-any": "$containsAny", "array-contains-all": "$containsAll",
    "starts-with": "$startsWith", "ends-with": "$endsWith", "matches": "$regex", "exists": "$exists",
    "size": "$size", "type": "$type",
}


def where(field_path: str, op: str, value: Any) -> JsonMap:
    """One condition, for composing with :func:`or_` / :func:`and_`.

    ``op`` is one of ``== != < <= > >= in not-in between array-contains
    array-contains-any array-contains-all starts-with ends-with matches exists size type``.
    """
    try:
        wire = _OPS[op]
    except KeyError:
        raise ValueError("unknown operator {0!r}".format(op)) from None
    return {("$id" if field_path == "id" else field_path): {wire: _jsonable(value)}}


def or_(*filters: Mapping[str, Any]) -> JsonMap:
    return {"$or": [dict(f) for f in filters]}


def and_(*filters: Mapping[str, Any]) -> JsonMap:
    return {"$and": [dict(f) for f in filters]}


def not_(f: Mapping[str, Any]) -> JsonMap:
    return {"$not": dict(f)}


def where_related(relation: str, f: Mapping[str, Any]) -> JsonMap:
    """Match through a relation: ``where_related("author", where("verified", "==", True))``."""
    return {relation: {"$rel": dict(f)}}


def count() -> JsonMap:
    return {"op": "count"}


def count_distinct(field_path: str) -> JsonMap:
    return {"op": "countDistinct", "field": field_path}


def sum_(field_path: str) -> JsonMap:
    return {"op": "sum", "field": field_path}


def avg(field_path: str) -> JsonMap:
    return {"op": "avg", "field": field_path}


def min_(field_path: str) -> JsonMap:
    return {"op": "min", "field": field_path}


def max_(field_path: str) -> JsonMap:
    return {"op": "max", "field": field_path}


def iso(value: Union[datetime, str]) -> str:
    """A datetime (naive = UTC) or ISO string, as the API's ISO-8601."""
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _jsonable(v: Any) -> Any:
    return iso(v) if isinstance(v, datetime) else v


# ── models ───────────────────────────────────────────────────────────────────


def _dt(v: Any) -> Optional[datetime]:
    if not isinstance(v, str):
        return None
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


@dataclass(frozen=True)
class DocumentSnapshot:
    collection: str
    id: str
    raw: Optional[JsonMap]

    @property
    def exists(self) -> bool:
        return self.raw is not None

    @property
    def data(self) -> Optional[JsonMap]:
        return None if self.raw is None else self.raw.get("data")

    def get(self, path: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in path.split("."):
            if not isinstance(cur, Mapping) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def __getitem__(self, key: str) -> Any:
        if self.data is None:
            raise KeyError(key)
        return self.data[key]

    @property
    def version(self) -> int:
        return int((self.raw or {}).get("version") or 0)

    @property
    def created_at(self) -> Optional[datetime]:
        return _dt((self.raw or {}).get("createdAt"))

    @property
    def updated_at(self) -> Optional[datetime]:
        return _dt((self.raw or {}).get("updatedAt"))

    @property
    def expand(self) -> JsonMap:
        """Related documents requested with ``expand``."""
        return dict((self.raw or {}).get("expand") or {})


@dataclass(frozen=True)
class QueryResult:
    docs: List[DocumentSnapshot]
    next_cursor: Optional[str]
    has_more: bool
    total: Optional[int] = None

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.docs)

    def __len__(self) -> int:
        return len(self.docs)


@dataclass(frozen=True)
class DocumentVersion:
    version: int
    op: str
    at: Optional[datetime]
    actor: Optional[str]
    deleted: bool
    data: Optional[JsonMap]
    changed_fields: List[str]


@dataclass(frozen=True)
class Change:
    """One committed write from the change stream. ``seq`` is the resume point."""

    seq: int
    collection: str
    id: str
    op: str
    version: int
    data: Optional[JsonMap]
    actor: Optional[str]
    tx_id: Optional[str]
    at: Optional[datetime]


@dataclass(frozen=True)
class WriteResult:
    index: int
    status: str
    collection: str
    id: str
    version: Optional[int]
    doc: Optional[DocumentSnapshot]
    error: Optional[JsonMap] = None


def snapshot(collection: str, doc_id: str, raw: Any) -> DocumentSnapshot:
    return DocumentSnapshot(collection, doc_id, raw if isinstance(raw, Mapping) else None)


def parse_query(collection: str, raw: Any) -> QueryResult:
    raw = raw or {}
    docs = [snapshot(collection, str(d.get("id")), d) for d in raw.get("items") or []]
    return QueryResult(docs, raw.get("nextCursor"), bool(raw.get("hasMore")), raw.get("total"))


def parse_write(raw: Any, collection: str, doc_id: str) -> DocumentSnapshot:
    raw = raw or {}
    return snapshot(collection, str(raw.get("id") or doc_id), raw.get("doc"))


def parse_results(raw: Any) -> List[WriteResult]:
    out = []
    for r in (raw or {}).get("results") or []:
        col, did = str(r.get("collection")), str(r.get("id"))
        out.append(WriteResult(int(r.get("index", 0)), str(r.get("status")), col, did, r.get("version"), snapshot(col, did, r.get("doc")) if r.get("doc") else None, r.get("error")))
    return out


def parse_history(raw: Any) -> List[DocumentVersion]:
    return [
        DocumentVersion(int(v["version"]), str(v["op"]), _dt(v.get("at")), v.get("actor"), bool(v.get("deleted")), v.get("data"), list(v.get("changedFields") or []))
        for v in (raw or {}).get("versions") or []
    ]


def parse_changes(raw: Any) -> "tuple[List[Change], int, bool]":
    raw = raw or {}
    changes = [
        Change(int(c["seq"]), str(c.get("collection")), str(c.get("id")), str(c.get("op")), int(c.get("version") or 0), c.get("data"), c.get("actor"), c.get("txId"), _dt(c.get("at")))
        for c in raw.get("changes") or []
    ]
    return changes, int(raw.get("next") or 0), bool(raw.get("hasMore"))


@dataclass
class QueryState:
    """The immutable JSON body a query compiles to."""

    filter: Optional[JsonMap] = None
    sort: List[JsonMap] = field(default_factory=list)
    limit: Optional[int] = None
    cursor: Optional[str] = None
    offset: Optional[int] = None
    select: Optional[List[str]] = None
    expand: Optional[List[str]] = None
    search: Optional[str] = None
    as_of: Optional[str] = None
    count: bool = False

    def body(self) -> JsonMap:
        return {
            "filter": self.filter,
            "sort": self.sort or None,
            "limit": self.limit,
            "cursor": self.cursor,
            "offset": self.offset,
            "select": self.select,
            "expand": self.expand,
            "search": self.search,
            "asOf": self.as_of,
            "count": True if self.count else None,
        }


def and_filter(current: Optional[JsonMap], extra: JsonMap) -> JsonMap:
    return extra if current is None else {"$and": [current, extra]}


def transaction_ops(ops: List[JsonMap], seen: Mapping[str, int]) -> List[JsonMap]:
    """Guard each write with the version its transaction read (or 'must not exist')."""
    out = []
    for op in ops:
        op = dict(op)
        v = seen.get("{0}/{1}".format(op["collection"], op["id"]))
        if v is not None and "precondition" not in op:
            op["precondition"] = {"version": v}
        if op["type"] == "create" and "precondition" not in op:
            op["precondition"] = {"exists": False}
        out.append(op)
    return out


def backoff(attempt: int) -> float:
    import random

    return min(1.0, 0.025 * 2 ** attempt) * (0.5 + random.random())


# ── trigger webhooks ─────────────────────────────────────────────────────────


class InvalidWebhookSignature(NexusError):
    """A DocStore trigger delivery failed verification (bad signature, stale, or replayed)."""


def verify_webhook(
    body: Union[bytes, str],
    headers: Mapping[str, str],
    secret: str,
    *,
    tolerance: int = 300,
    seen_nonce: Optional[Any] = None,
    now: Optional[float] = None,
) -> JsonMap:
    """Verify a DocStore trigger webhook and return its decoded payload.

    The signature is HMAC-SHA256 (hex) of ``"{timestamp}.{nonce}.{body}"`` with
    the trigger's ``whsec_…`` secret, sent as ``x-nexus-signature`` with
    ``x-nexus-timestamp`` and ``x-nexus-nonce``. Pass the RAW request body.
    ``seen_nonce(nonce) -> bool`` (optional) lets you reject replays: return
    True when you have already seen the nonce. Deliveries are at-least-once —
    dedupe on the payload's ``deliveryId`` (or ``seq``) for exactly-once effects.
    """
    h = {k.lower(): v for k, v in headers.items()}
    sig, ts, nonce = h.get("x-nexus-signature"), h.get("x-nexus-timestamp"), h.get("x-nexus-nonce")
    if not sig or not ts or not nonce:
        raise InvalidWebhookSignature("missing x-nexus-signature / x-nexus-timestamp / x-nexus-nonce")
    try:
        ts_i = int(ts)
    except ValueError:
        raise InvalidWebhookSignature("x-nexus-timestamp is not a number") from None
    if abs((time.time() if now is None else now) - ts_i) > tolerance:
        raise InvalidWebhookSignature("timestamp outside the {0}s tolerance".format(tolerance))
    raw = body.decode("utf-8") if isinstance(body, bytes) else body
    expected = hmac.new(secret.encode(), "{0}.{1}.{2}".format(ts_i, nonce, raw).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise InvalidWebhookSignature("signature mismatch")
    if seen_nonce is not None and seen_nonce(nonce):
        raise InvalidWebhookSignature("nonce already used (replay)")
    return json.loads(raw)
