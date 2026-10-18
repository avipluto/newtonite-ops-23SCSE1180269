"""Idempotency keys: a retried or double-clicked request runs only once.

The client sends a unique `Idempotency-Key` header per logical action. Flow, all inside
the request's single transaction:

  1. INSERT (user_id, key) ... ON CONFLICT DO NOTHING
     - inserted  -> we are the first; run the action, store its response, commit.
     - conflict  -> someone already ran it; return the stored response.
  2. If a duplicate arrives while the first is still running, Postgres makes its INSERT
     wait on the unique key until the first transaction commits (then it replays the
     stored response) or rolls back (then it runs the action itself).

Errors are not stored: a failed request rolls back the key too, so a retry runs again.
"""
import hashlib
import json

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.errors import Conflict, ValidationFailed
from app.models import IdempotencyKey


def fingerprint(method: str, path: str, body) -> str:
    """Hash of the request, so the same key cannot be reused for a different request."""
    raw = json.dumps([method, path, body], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def begin(db: Session, user_id: int, key: str, request_fingerprint: str) -> IdempotencyKey | None:
    """Reserve the key. Returns None if we own it now, or the stored row to replay."""
    inserted = db.execute(
        insert(IdempotencyKey)
        .values(user_id=user_id, key=key, request_fingerprint=request_fingerprint)
        .on_conflict_do_nothing(index_elements=["user_id", "key"])
        .returning(IdempotencyKey.key)
    ).scalar_one_or_none()
    if inserted is not None:
        return None

    stored = db.scalar(select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key))
    if stored.request_fingerprint != request_fingerprint:
        raise ValidationFailed("This Idempotency-Key was already used for a different request",
                               code="idempotency_key_reused")
    if stored.response_status is None:  # defensive: should not happen, see module docstring
        raise Conflict("A request with this Idempotency-Key is still in progress", code="request_in_progress")
    return stored


def finish(db: Session, user_id: int, key: str, status: int, body: dict) -> None:
    db.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
        .values(response_status=status, response_body=body)
    )
