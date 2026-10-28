"""Nexus DocStore — the document database.

Get a client from :meth:`nexus.NexusClient.docstore` (or
:meth:`nexus.aio.AsyncNexusClient.docstore`); import the helpers from here::

    from nexus.docstore import increment, array_union, server_timestamp, where, or_, count, sum_, verify_webhook
"""

from ._docstore import (  # noqa: F401
    Change,
    DocumentSnapshot,
    DocumentVersion,
    FieldValue,
    InvalidWebhookSignature,
    QueryResult,
    WriteResult,
    and_,
    array_push,
    array_remove,
    array_union,
    avg,
    count,
    count_distinct,
    delete_field,
    increment,
    max_,
    maximum,
    merge_field,
    min_,
    minimum,
    multiply,
    not_,
    or_,
    server_timestamp,
    sum_,
    to_update_spec,
    verify_webhook,
    where,
    where_related,
)
from .exceptions import ConflictError, ServiceDisabledError  # noqa: F401
from .resources.docstore import CollectionRef, DocStore, DocumentRef, Query, Transaction, WriteBatch  # noqa: F401
