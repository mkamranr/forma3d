from __future__ import annotations

import uuid

from httpx import AsyncClient

from tests.conftest import auth, register


async def _user(client: AsyncClient, email: str) -> dict[str, str]:
    data = await register(client, email=email, name=email.split("@")[0])
    return auth(data["access_token"])


async def test_project_crud_lifecycle(client: AsyncClient) -> None:
    h = await _user(client, "a@example.com")

    created = await client.post("/api/v1/projects", json={"name": "  Office Desk ", "description": "Oak"}, headers=h)
    assert created.status_code == 201
    project = created.json()
    assert project["name"] == "Office Desk"
    assert project["version_count"] == 0
    assert project["thumbnail_url"] is None
    pid = project["id"]

    assert (await client.get(f"/api/v1/projects/{pid}", headers=h)).json()["name"] == "Office Desk"

    renamed = await client.put(f"/api/v1/projects/{pid}", json={"name": "Writing Desk"}, headers=h)
    assert renamed.status_code == 200 and renamed.json()["name"] == "Writing Desk"

    listing = (await client.get("/api/v1/projects", headers=h)).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == pid

    assert (await client.delete(f"/api/v1/projects/{pid}", headers=h)).status_code == 204
    assert (await client.get(f"/api/v1/projects/{pid}", headers=h)).status_code == 404
    assert (await client.get("/api/v1/projects?status=all", headers=h)).json()["total"] == 0


async def test_project_ownership_is_enforced(client: AsyncClient) -> None:
    alice = await _user(client, "alice@example.com")
    bob = await _user(client, "bob@example.com")
    pid = (await client.post("/api/v1/projects", json={"name": "Secret"}, headers=alice)).json()["id"]

    assert (await client.get(f"/api/v1/projects/{pid}", headers=bob)).status_code == 404
    assert (await client.put(f"/api/v1/projects/{pid}", json={"name": "Hacked"}, headers=bob)).status_code == 404
    assert (await client.delete(f"/api/v1/projects/{pid}", headers=bob)).status_code == 404
    assert (await client.post(f"/api/v1/projects/{pid}/duplicate", headers=bob)).status_code == 404
    assert (await client.get("/api/v1/projects", headers=bob)).json()["total"] == 0
    assert (await client.get(f"/api/v1/projects/{pid}", headers=alice)).json()["name"] == "Secret"


async def test_projects_require_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/projects")).status_code == 401
    assert (await client.post("/api/v1/projects", json={"name": "x"})).status_code == 401


async def test_unknown_project_is_404(client: AsyncClient) -> None:
    h = await _user(client, "a@example.com")
    assert (await client.get(f"/api/v1/projects/{uuid.uuid4()}", headers=h)).status_code == 404


async def test_validation_rejects_blank_name_and_unknown_fields(client: AsyncClient) -> None:
    h = await _user(client, "a@example.com")
    assert (await client.post("/api/v1/projects", json={"name": "   "}, headers=h)).status_code == 422
    assert (await client.post("/api/v1/projects", json={"name": "ok", "user_id": "x"}, headers=h)).status_code == 422
    pid = (await client.post("/api/v1/projects", json={"name": "ok"}, headers=h)).json()["id"]
    bad_status = await client.put(f"/api/v1/projects/{pid}", json={"status": "deleted"}, headers=h)
    assert bad_status.status_code == 422


async def test_archive_search_and_sort(client: AsyncClient) -> None:
    h = await _user(client, "a@example.com")
    ids = {}
    for name in ["Chair", "Desk", "Cabinet"]:
        ids[name] = (await client.post("/api/v1/projects", json={"name": name}, headers=h)).json()["id"]

    await client.put(f"/api/v1/projects/{ids['Cabinet']}", json={"status": "archived"}, headers=h)

    active = (await client.get("/api/v1/projects?sort=name_asc", headers=h)).json()
    assert [p["name"] for p in active["items"]] == ["Chair", "Desk"]

    archived = (await client.get("/api/v1/projects?status=archived", headers=h)).json()
    assert [p["name"] for p in archived["items"]] == ["Cabinet"]

    all_desc = (await client.get("/api/v1/projects?status=all&sort=name_desc", headers=h)).json()
    assert [p["name"] for p in all_desc["items"]] == ["Desk", "Chair", "Cabinet"]

    search = (await client.get("/api/v1/projects?q=des", headers=h)).json()
    assert [p["name"] for p in search["items"]] == ["Desk"]

    # LIKE wildcards in the search term are treated literally.
    assert (await client.get("/api/v1/projects?q=%25", headers=h)).json()["total"] == 0


async def test_duplicate_project(client: AsyncClient) -> None:
    h = await _user(client, "a@example.com")
    pid = (await client.post("/api/v1/projects", json={"name": "Desk", "description": "d"}, headers=h)).json()["id"]
    dup = await client.post(f"/api/v1/projects/{pid}/duplicate", headers=h)
    assert dup.status_code == 201
    assert dup.json()["name"] == "Desk (copy)" and dup.json()["id"] != pid
    assert (await client.get("/api/v1/projects", headers=h)).json()["total"] == 2
