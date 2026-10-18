"""Background worker that processes the outbox (the `jobs` table).

Run with:  python -m app.worker

How it stays reliable:
- Jobs are written in the SAME transaction as the change that caused them, so a job
  exists if and only if the change was committed.
- A worker claims one job with SELECT ... FOR UPDATE SKIP LOCKED, so several workers can
  run side by side without ever taking the same job.
- A claimed job has a lease (locked_until). If the worker crashes mid-job, the lease
  expires and another worker picks the job up again.
- A failing job is retried with exponential backoff (2s, 4s, 8s, ...) up to max_attempts,
  then marked 'failed' with the last error for a human to look at.
- Handlers are idempotent: notifications have UNIQUE(job_id, user_id), so running the
  same job twice still delivers each notification once.
"""
import logging
import signal
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import AuthToken, IdempotencyKey, Job, Membership, Notification

log = logging.getLogger("worker")

LEASE_SECONDS = 60
BACKOFF_BASE_SECONDS = 2
BACKOFF_MAX_SECONDS = 300
POLL_SECONDS = 1.0
HOUSEKEEPING_EVERY_SECONDS = 600
IDEMPOTENCY_KEY_TTL = timedelta(hours=24)


# ---------------------------------------------------------------- handlers

def _deliver(db: Session, job: Job, user_ids, message: str) -> None:
    """'Send' a notification. ON CONFLICT DO NOTHING makes a repeat delivery a no-op."""
    for user_id in user_ids:
        db.execute(
            insert(Notification)
            .values(user_id=user_id, item_id=job.payload.get("item_id"), job_id=job.id, message=message)
            .on_conflict_do_nothing(index_elements=["job_id", "user_id"])
        )


def handle_notify_user(db: Session, job: Job) -> None:
    _deliver(db, job, [job.payload["user_id"]], job.payload["message"])


def handle_notify_approvers(db: Session, job: Job) -> None:
    approver_ids = db.scalars(
        select(Membership.user_id).where(
            Membership.team_id == job.payload["team_id"], Membership.role.in_(("lead", "admin")))
    ).all()
    exclude = job.payload.get("exclude_user_id")
    _deliver(db, job, [uid for uid in approver_ids if uid != exclude], job.payload["message"])


HANDLERS = {
    "notify_user": handle_notify_user,
    "notify_approvers": handle_notify_approvers,
}


# ---------------------------------------------------------------- processing

CLAIM_SQL = text("""
    UPDATE jobs
       SET status = 'running',
           attempts = attempts + 1,
           locked_until = now() + :lease * interval '1 second',
           updated_at = now()
     WHERE id = (
           SELECT id FROM jobs
            WHERE (status = 'pending' AND run_after <= now())
               OR (status = 'running' AND locked_until < now())
            ORDER BY run_after, id
            LIMIT 1
            FOR UPDATE SKIP LOCKED)
 RETURNING id
""")


def process_one(db: Session) -> bool:
    """Claim and run a single job. Returns False if there was nothing to do."""
    job_id = db.execute(CLAIM_SQL, {"lease": LEASE_SECONDS}).scalar_one_or_none()
    db.commit()  # the claim is its own short transaction
    if job_id is None:
        return False

    job = db.get(Job, job_id)
    if job.attempts > job.max_attempts:  # it crashed the worker on every previous try
        job.status, job.locked_until = "failed", None
        job.last_error = (job.last_error or "") + " | gave up after lease expired too many times"
        db.commit()
        return True

    try:
        handler = HANDLERS[job.kind]
        handler(db, job)
        # The handler's writes and 'done' are committed together.
        job.status, job.locked_until, job.last_error = "done", None, None
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        log.info("job %s (%s) done", job.id, job.kind)
    except Exception as exc:  # noqa: BLE001 - any failure means "retry later"
        db.rollback()
        job = db.get(Job, job_id)
        job.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        job.locked_until = None
        job.updated_at = datetime.now(timezone.utc)
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            log.error("job %s (%s) failed permanently: %s", job.id, job.kind, job.last_error)
        else:
            delay = min(BACKOFF_BASE_SECONDS * 2 ** (job.attempts - 1), BACKOFF_MAX_SECONDS)
            job.status = "pending"
            job.run_after = datetime.now(timezone.utc) + timedelta(seconds=delay)
            log.warning("job %s (%s) attempt %s failed, retrying in %ss", job.id, job.kind, job.attempts, delay)
        db.commit()
    return True


def housekeeping(db: Session) -> None:
    """Delete idempotency keys older than 24h and expired login tokens."""
    cutoff = datetime.now(timezone.utc) - IDEMPOTENCY_KEY_TTL
    db.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < cutoff))
    db.execute(delete(AuthToken).where(AuthToken.expires_at < datetime.now(timezone.utc)))
    db.commit()


def run_forever() -> None:
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        log.info("stopping after the current job")

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    last_housekeeping = 0.0
    log.info("worker started")

    while not stopping:
        try:
            with SessionLocal() as db:
                if time.monotonic() - last_housekeeping > HOUSEKEEPING_EVERY_SECONDS:
                    housekeeping(db)
                    last_housekeeping = time.monotonic()
                worked = process_one(db)
        except Exception:  # noqa: BLE001 - e.g. database restarting; wait and try again
            log.exception("worker loop error")
            worked = False
            time.sleep(5)
        if not worked:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_forever()
