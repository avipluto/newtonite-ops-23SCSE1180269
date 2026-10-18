"""Two people acting on the same item at the same time."""
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.main import app
from app.models import ActivityEvent, WorkItem


def run_in_parallel(requests):
    """Fire all requests at (nearly) the same moment, each from its own thread and client."""
    barrier = threading.Barrier(len(requests))

    def fire(request):
        method, url, kwargs = request
        client = TestClient(app)
        barrier.wait()  # everyone starts together
        return client.request(method, url, **kwargs)

    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        return list(pool.map(fire, requests))


def test_concurrent_claim_exactly_one_wins(make_item, headers, world):
    item = make_item()
    claimers = ["bob", "carol", "erin", "alice", "dave", "adm"]
    responses = run_in_parallel([("POST", f"/api/items/{item['id']}/claim", {"headers": headers(u)})
                                 for u in claimers])

    codes = sorted(r.status_code for r in responses)
    assert codes == [200] + [409] * (len(claimers) - 1)
    winner = next(r.json() for r in responses if r.status_code == 200)
    losers = [r.json() for r in responses if r.status_code == 409]
    assert all(body["code"] == "already_claimed" for body in losers)
    # Losers get the item as it really is now, so the UI can show who won.
    assert all(body["current"]["assignee"]["id"] == winner["assignee"]["id"] for body in losers)

    with SessionLocal() as db:
        assert db.get(WorkItem, item["id"]).assignee_id == winner["assignee"]["id"]
        assignee_events = db.scalar(select(func.count()).select_from(ActivityEvent).where(
            ActivityEvent.item_id == item["id"], ActivityEvent.field == "assignee"))
        assert assignee_events == 1  # history agrees: one claim happened


def test_stale_update_is_rejected_and_data_unchanged(client, make_item, headers):
    item = make_item(priority="low")
    first = client.patch(f"/api/items/{item['id']}", json={"version": 1, "priority": "urgent"},
                         headers=headers("bob"))
    assert first.status_code == 200
    assert first.json()["version"] == 2

    # Carol still has version 1 open in her browser.
    stale = client.patch(f"/api/items/{item['id']}", json={"version": 1, "title": "Carol's edit"},
                         headers=headers("carol"))
    assert stale.status_code == 409
    body = stale.json()
    assert body["code"] == "version_conflict"
    assert body["current"]["version"] == 2
    assert body["current"]["priority"] == "urgent"

    latest = client.get(f"/api/items/{item['id']}", headers=headers("bob")).json()
    assert latest["title"] == "Payment gateway timeout"  # Carol's edit did not land
    assert latest["version"] == 2


def test_parallel_edits_with_same_version_only_one_lands(make_item, headers):
    item = make_item()
    responses = run_in_parallel([
        ("PATCH", f"/api/items/{item['id']}", {"json": {"version": 1, "priority": "urgent"}, "headers": headers("bob")}),
        ("PATCH", f"/api/items/{item['id']}", {"json": {"version": 1, "priority": "low"}, "headers": headers("carol")}),
    ])
    assert sorted(r.status_code for r in responses) == [200, 409]

    with SessionLocal() as db:
        row = db.get(WorkItem, item["id"])
        winner = next(r.json() for r in responses if r.status_code == 200)
        assert row.version == 2
        assert row.priority == winner["priority"]


def test_stale_transition_is_rejected(client, make_item, headers):
    item = make_item(assignee_id=None)
    client.post(f"/api/items/{item['id']}/claim", headers=headers("bob"))  # version -> 2
    response = client.post(f"/api/items/{item['id']}/transition",
                           json={"to_status": "in_progress", "version": 1}, headers=headers("bob"))
    assert response.status_code == 409
    assert response.json()["code"] == "version_conflict"
