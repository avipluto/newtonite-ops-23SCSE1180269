"""Transactional outbox + worker: retries, max attempts, idempotent handlers."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from app import worker
from app.db import SessionLocal
from app.models import Job, Notification


def jobs():
    with SessionLocal() as db:
        return db.scalars(select(Job).order_by(Job.id)).all()


def notifications():
    with SessionLocal() as db:
        return db.scalars(select(Notification).order_by(Notification.id)).all()


def run_worker_once() -> bool:
    with SessionLocal() as db:
        return worker.process_one(db)


def make_due_now(job_id):
    with SessionLocal() as db:
        db.execute(update(Job).where(Job.id == job_id).values(run_after=func.now()))
        db.commit()


def test_assignment_writes_a_job_that_the_worker_delivers(make_item, world):
    make_item(as_user="alice", assignee_id=world.users["bob"])
    [job] = jobs()
    assert (job.kind, job.status) == ("notify_user", "pending")

    assert run_worker_once() is True
    assert run_worker_once() is False  # nothing left
    [job] = jobs()
    assert job.status == "done"
    [note] = notifications()
    assert note.user_id == world.users["bob"]
    assert "assigned you" in note.message


def test_submitting_for_approval_notifies_the_teams_leads(client, headers, make_item, world):
    item = make_item(as_user="bob", assignee_id=world.users["bob"])  # self-assigned: no job
    for to_status in ("in_progress", "awaiting_approval"):
        item = client.post(f"/api/items/{item['id']}/transition",
                           json={"to_status": to_status, "version": item["version"]}, headers=headers("bob")).json()
    run_worker_once()
    assert {n.user_id for n in notifications()} == {world.users["alice"], world.users["dave"], world.users["adm"]}


def test_running_the_same_job_twice_delivers_once(make_item, world):
    make_item(as_user="alice", assignee_id=world.users["bob"])
    run_worker_once()
    [job] = jobs()
    # Simulate "the worker crashed after delivering but before it could record 'done'".
    with SessionLocal() as db:
        db.execute(update(Job).where(Job.id == job.id).values(status="pending"))
        db.commit()
    run_worker_once()
    assert len(notifications()) == 1


def test_failing_job_retries_with_backoff_then_fails(make_item, world, monkeypatch):
    def broken_handler(db, job):
        raise RuntimeError("email provider down")

    monkeypatch.setitem(worker.HANDLERS, "notify_user", broken_handler)
    make_item(as_user="alice", assignee_id=world.users["bob"])
    [job] = jobs()

    run_worker_once()
    [job] = jobs()
    assert (job.status, job.attempts) == ("pending", 1)
    assert job.run_after > datetime.now(timezone.utc)  # backoff: not retried immediately
    assert "email provider down" in job.last_error
    assert run_worker_once() is False  # still waiting for its backoff

    for _ in range(job.max_attempts - 1):
        make_due_now(job.id)
        run_worker_once()
    [job] = jobs()
    assert (job.status, job.attempts) == ("failed", job.max_attempts)
    assert notifications() == []


def test_job_whose_worker_died_is_picked_up_again(make_item, world):
    make_item(as_user="alice", assignee_id=world.users["bob"])
    [job] = jobs()
    with SessionLocal() as db:  # looks like a worker claimed it and then crashed
        db.execute(update(Job).where(Job.id == job.id).values(
            status="running", attempts=1, locked_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db.commit()
    assert run_worker_once() is True
    [job] = jobs()
    assert (job.status, job.attempts) == ("done", 2)
    assert len(notifications()) == 1


def test_rejected_change_leaves_no_job_behind(client, headers, make_item, world):
    """Outbox rows live in the same transaction: if the change fails, so does the job."""
    item = make_item()
    response = client.patch(f"/api/items/{item['id']}", json={"version": 99, "assignee_id": world.users["carol"]},
                            headers=headers("alice"))
    assert response.status_code == 409
    assert jobs() == []
