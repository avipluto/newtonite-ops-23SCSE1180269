"""Double-clicks and network retries must not create duplicates."""
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Comment, WorkItem
from tests.test_concurrency import run_in_parallel


def count(model) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model))


def item_body(world, **fields):
    return {"team_id": world.ops, "title": "Refund stuck", "type": "payment", "priority": "high", **fields}


def test_same_key_twice_creates_one_item_with_same_response(client, headers, world):
    h = {**headers("bob"), "Idempotency-Key": "create-refund-1"}
    first = client.post("/api/items", json=item_body(world), headers=h)
    second = client.post("/api/items", json=item_body(world), headers=h)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert count(WorkItem) == 1


def test_same_key_in_parallel_creates_one_item(headers, world):
    h = {**headers("bob"), "Idempotency-Key": "double-click-42"}
    responses = run_in_parallel([("POST", "/api/items", {"json": item_body(world), "headers": h})] * 5)

    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["id"] for r in responses}) == 1
    assert count(WorkItem) == 1


def test_key_reused_for_different_request_is_rejected(client, headers, world):
    h = {**headers("bob"), "Idempotency-Key": "key-1"}
    assert client.post("/api/items", json=item_body(world), headers=h).status_code == 201
    response = client.post("/api/items", json=item_body(world, title="Something else"), headers=h)
    assert response.status_code == 422
    assert response.json()["code"] == "idempotency_key_reused"
    assert count(WorkItem) == 1


def test_keys_are_per_user(client, headers, world):
    body = item_body(world)
    client.post("/api/items", json=body, headers={**headers("bob"), "Idempotency-Key": "same"})
    client.post("/api/items", json=body, headers={**headers("carol"), "Idempotency-Key": "same"})
    assert count(WorkItem) == 2


def test_failed_request_is_not_remembered(client, headers, world):
    """A request that fails rolls back its key too, so the retry really runs."""
    h = {**headers("bob"), "Idempotency-Key": "retry-me"}
    item = client.post("/api/items", json=item_body(world), headers=headers("bob")).json()
    url = f"/api/items/{item['id']}/comments"

    client.post(f"/api/items/{item['id']}/claim", headers=headers("carol"))  # unrelated change, fine
    assert client.post(url, json={"body": "   "}, headers=h).status_code == 422  # validation error
    assert client.post(url, json={"body": "Looking into it"}, headers=h).status_code == 201
    assert client.post(url, json={"body": "Looking into it"}, headers=h).status_code == 201  # replay
    assert count(Comment) == 1


def test_without_a_key_each_request_runs(client, headers, world):
    client.post("/api/items", json=item_body(world), headers=headers("bob"))
    client.post("/api/items", json=item_body(world), headers=headers("bob"))
    assert count(WorkItem) == 2
