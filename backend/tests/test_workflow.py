"""Server-enforced workflow: allowed transitions and the Lead-only approval step."""


def move(client, headers, item, to_status, user):
    return client.post(f"/api/items/{item['id']}/transition",
                       json={"to_status": to_status, "version": item["version"]}, headers=headers(user))


def test_full_happy_path(client, headers, make_item, world):
    item = make_item(assignee_id=world.users["bob"])
    for to_status, user in [("in_progress", "bob"), ("awaiting_approval", "bob"),
                            ("resolved", "alice"), ("closed", "bob")]:
        response = move(client, headers, item, to_status, user)
        assert response.status_code == 200, response.text
        item = response.json()
        assert item["status"] == to_status

    events = client.get(f"/api/items/{item['id']}/activity", headers=headers("bob")).json()["events"]
    status_changes = [(e["from"], e["to"]) for e in reversed(events) if e["field"] == "status"]
    assert status_changes == [("open", "in_progress"), ("in_progress", "awaiting_approval"),
                              ("awaiting_approval", "resolved"), ("resolved", "closed")]


def test_invalid_jump_open_to_closed_is_rejected(client, headers, make_item, world):
    item = make_item(assignee_id=world.users["bob"])
    response = move(client, headers, item, "closed", "alice")
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"
    assert client.get(f"/api/items/{item['id']}", headers=headers("bob")).json()["status"] == "open"


def submitted_item(client, headers, make_item, assignee):
    item = make_item(as_user=assignee, assignee_id=None)
    item = client.post(f"/api/items/{item['id']}/claim", headers=headers(assignee)).json()
    item = move(client, headers, item, "in_progress", assignee).json()
    return move(client, headers, item, "awaiting_approval", assignee).json()


def test_member_cannot_approve(client, headers, make_item):
    item = submitted_item(client, headers, make_item, "bob")
    response = move(client, headers, item, "resolved", "carol")
    assert response.status_code == 403


def test_lead_cannot_approve_own_work(client, headers, make_item):
    item = submitted_item(client, headers, make_item, "alice")
    assert move(client, headers, item, "resolved", "alice").status_code == 403
    assert move(client, headers, item, "resolved", "dave").status_code == 200  # another lead can


def test_lead_can_reject_back_to_in_progress(client, headers, make_item):
    item = submitted_item(client, headers, make_item, "bob")
    response = move(client, headers, item, "in_progress", "alice")
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"


def test_start_requires_an_assignee(client, headers, make_item):
    item = make_item()
    response = move(client, headers, item, "in_progress", "bob")
    assert response.status_code == 409
    assert response.json()["code"] == "no_assignee"


def test_member_cannot_move_someone_elses_item(client, headers, make_item, world):
    item = make_item(as_user="alice", assignee_id=world.users["bob"])
    assert move(client, headers, item, "in_progress", "carol").status_code == 403
    assert move(client, headers, item, "in_progress", "alice").status_code == 200  # a lead may


def test_closed_items_cannot_be_edited(client, headers, make_item, world):
    item = make_item(assignee_id=world.users["bob"])
    for to_status, user in [("in_progress", "bob"), ("awaiting_approval", "bob"),
                            ("resolved", "alice"), ("closed", "bob")]:
        item = move(client, headers, item, to_status, user).json()
    response = client.patch(f"/api/items/{item['id']}", json={"version": item["version"], "priority": "low"},
                            headers=headers("alice"))
    assert response.status_code == 409
    assert response.json()["code"] == "item_closed"


def test_allowed_transitions_are_reported_per_user(client, headers, make_item):
    item = submitted_item(client, headers, make_item, "bob")
    as_bob = client.get(f"/api/items/{item['id']}", headers=headers("bob")).json()
    as_alice = client.get(f"/api/items/{item['id']}", headers=headers("alice")).json()
    assert as_bob["can"]["transitions"] == []
    assert {t["to"] for t in as_alice["can"]["transitions"]} == {"resolved", "in_progress"}
