"""Permissions are enforced on the server, per team and per role."""


def test_requests_without_a_valid_token_get_401(client):
    assert client.get("/api/items").status_code == 401
    assert client.get("/api/items", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_login_and_logout(client, world):
    from tests.conftest import PASSWORD

    assert client.post("/api/auth/login", json={"username": "bob", "password": "wrong"}).status_code == 401
    response = client.post("/api/auth/login", json={"username": "bob", "password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["teams"] == [{"id": world.ops, "name": "Ops", "role": "member"}]

    auth = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/me", headers=auth).status_code == 200
    assert client.post("/api/auth/logout", headers=auth).status_code == 204
    assert client.get("/api/me", headers=auth).status_code == 401


def test_user_from_another_team_gets_404_on_read_and_write(client, headers, make_item):
    item = make_item()
    url = f"/api/items/{item['id']}"
    h = headers("oscar")  # member of team "Other" only
    assert client.get(url, headers=h).status_code == 404
    assert client.get(f"{url}/activity", headers=h).status_code == 404
    assert client.patch(url, json={"version": 1, "priority": "low"}, headers=h).status_code == 404
    assert client.post(f"{url}/claim", headers=h).status_code == 404
    assert client.post(f"{url}/comments", json={"body": "hi"}, headers=h).status_code == 404
    assert client.post(f"{url}/transition", json={"to_status": "in_progress", "version": 1},
                       headers=h).status_code == 404


def test_lists_only_show_your_teams(client, headers, make_item):
    make_item()
    assert client.get("/api/items", headers=headers("oscar")).json()["items"] == []
    assert len(client.get("/api/items", headers=headers("bob")).json()["items"]) == 1


def test_cannot_create_in_a_team_you_are_not_in(client, headers, world):
    body = {"team_id": world.ops, "title": "x", "type": "task"}
    assert client.post("/api/items", json=body, headers=headers("oscar")).status_code == 404


def test_viewer_can_read_but_not_write(client, headers, make_item, world):
    item = make_item()
    h = headers("vic")
    assert client.get(f"/api/items/{item['id']}", headers=h).status_code == 200
    assert client.post("/api/items", json={"team_id": world.ops, "title": "x", "type": "task"},
                       headers=h).status_code == 403
    assert client.post(f"/api/items/{item['id']}/claim", headers=h).status_code == 403
    assert client.post(f"/api/items/{item['id']}/comments", json={"body": "hi"}, headers=h).status_code == 403
    assert client.patch(f"/api/items/{item['id']}", json={"version": 1, "priority": "low"},
                        headers=h).status_code == 403


def test_only_leads_can_assign_others(client, headers, make_item, world):
    item = make_item()
    body = {"version": 1, "assignee_id": world.users["carol"]}
    assert client.patch(f"/api/items/{item['id']}", json=body, headers=headers("bob")).status_code == 403
    response = client.patch(f"/api/items/{item['id']}", json=body, headers=headers("alice"))
    assert response.status_code == 200
    assert response.json()["assignee"]["id"] == world.users["carol"]


def test_cannot_assign_someone_outside_the_team(client, headers, make_item, world):
    item = make_item()
    response = client.patch(f"/api/items/{item['id']}", json={"version": 1, "assignee_id": world.users["oscar"]},
                            headers=headers("alice"))
    assert response.status_code == 422


def test_member_cannot_create_item_assigned_to_someone_else(client, headers, world):
    body = {"team_id": world.ops, "title": "x", "type": "task", "assignee_id": world.users["carol"]}
    assert client.post("/api/items", json=body, headers=headers("bob")).status_code == 403
