"""Listing, search, pagination, dashboard views and history."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import SessionLocal


def test_cursor_pagination_returns_every_item_exactly_once(client, headers, make_item):
    created = {make_item(title=f"Item {n}")["id"] for n in range(23)}
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 5, **({"cursor": cursor} if cursor else {})}
        page = client.get("/api/items", params=params, headers=headers("bob")).json()
        seen += [item["id"] for item in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 5
    assert len(seen) == len(set(seen)) == 23
    assert set(seen) == created
    assert seen == sorted(seen, reverse=True)  # newest first


def test_filters_and_full_text_search(client, headers, make_item):
    make_item(title="Settlement mismatch for merchant", type="payment", priority="urgent")
    make_item(title="Disk usage alert", type="incident", priority="low")
    h = headers("bob")

    def titles(**params):
        return [i["title"] for i in client.get("/api/items", params=params, headers=h).json()["items"]]

    assert titles(q="settlement") == ["Settlement mismatch for merchant"]
    assert titles(q="merchants") == ["Settlement mismatch for merchant"]  # stemming
    assert titles(priority="low") == ["Disk usage alert"]
    assert titles(type="payment", priority="urgent") == ["Settlement mismatch for merchant"]
    assert sorted(titles(priority=["low", "urgent"])) == ["Disk usage alert", "Settlement mismatch for merchant"]


def test_dashboard_views(client, headers, make_item, world):
    mine = make_item(title="Mine", assignee_id=world.users["bob"])
    make_item(title="Urgent and free", priority="urgent")
    make_item(title="Not urgent", priority="low")
    waiting = make_item(as_user="alice", title="Needs approval", assignee_id=world.users["bob"])
    for to_status in ("in_progress", "awaiting_approval"):
        waiting = client.post(f"/api/items/{waiting['id']}/transition",
                              json={"to_status": to_status, "version": waiting["version"]},
                              headers=headers("bob")).json()

    def view(name, user):
        return {i["title"] for i in client.get("/api/items", params={"view": name},
                                               headers=headers(user)).json()["items"]}

    assert view("assigned_to_me", "bob") == {mine["title"], "Needs approval"}
    assert view("unassigned_urgent", "bob") == {"Urgent and free"}
    assert view("awaiting_my_approval", "alice") == {"Needs approval"}
    assert view("awaiting_my_approval", "bob") == set()  # members never approve


def test_every_change_is_in_the_history(client, headers, make_item, world):
    item = make_item(priority="low")
    item = client.patch(f"/api/items/{item['id']}", json={"version": 1, "priority": "urgent"},
                        headers=headers("bob")).json()
    client.patch(f"/api/items/{item['id']}", json={"version": item["version"], "assignee_id": world.users["carol"]},
                 headers=headers("alice"))
    client.post(f"/api/items/{item['id']}/comments", json={"body": "On it"}, headers=headers("carol"))

    events = client.get(f"/api/items/{item['id']}/activity", headers=headers("bob")).json()["events"]
    summary = [(e["type"], e["actor"], e["field"], e["from"], e["to"], e["comment"]) for e in reversed(events)]
    assert summary == [
        ("created", "Bob", None, None, "open", None),
        ("changed", "Bob", "priority", "low", "urgent", None),
        ("changed", "Alice", "assignee", None, "Carol", None),
        ("commented", "Carol", None, None, None, "On it"),
    ]


def test_comment_does_not_bump_version(client, headers, make_item):
    item = make_item()
    client.post(f"/api/items/{item['id']}/comments", json={"body": "note"}, headers=headers("bob"))
    assert client.get(f"/api/items/{item['id']}", headers=headers("bob")).json()["version"] == 1


def test_history_is_append_only_in_the_database(make_item):
    make_item()
    with SessionLocal() as db:
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("UPDATE activity_events SET to_value = 'tampered'"))
        db.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("DELETE FROM activity_events"))


def test_validation_errors(client, headers, world):
    h = headers("bob")
    assert client.post("/api/items", json={"team_id": world.ops, "title": "  ", "type": "task"},
                       headers=h).status_code == 422
    assert client.post("/api/items", json={"team_id": world.ops, "title": "x", "type": "bogus"},
                       headers=h).status_code == 422
    assert client.get("/api/items", params={"cursor": "garbage"}, headers=h).status_code == 422
