from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import auth, register


async def test_register_returns_token_and_sets_httponly_cookie(client: AsyncClient) -> None:
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": "New@Example.com", "password": "s3cret-pass", "display_name": " Nia "},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["user"]["email"] == "new@example.com"
    assert body["user"]["display_name"] == "Nia"
    assert "password_hash" not in body["user"]
    cookie = res.headers["set-cookie"]
    assert "f3d_refresh=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/api/v1/auth" in cookie


async def test_register_duplicate_email_conflicts(client: AsyncClient) -> None:
    await register(client)
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": "A@example.com", "password": "another-pass", "display_name": "Dup"},
    )
    assert res.status_code == 409
    assert res.json()["code"] == "email_taken"


async def test_register_rejects_short_password_and_unknown_fields(client: AsyncClient) -> None:
    res = await client.post(
        "/api/v1/auth/register", json={"email": "x@example.com", "password": "short", "display_name": "X"}
    )
    assert res.status_code == 422
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": "x@example.com", "password": "long-enough", "display_name": "X", "is_admin": True},
    )
    assert res.status_code == 422


async def test_login_and_me(client: AsyncClient) -> None:
    await register(client)
    res = await client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "s3cret-pass"})
    assert res.status_code == 200
    token = res.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers=auth(token))
    assert me.status_code == 200
    assert me.json()["email"] == "a@example.com"


async def test_login_wrong_password_and_unknown_user_look_identical(client: AsyncClient) -> None:
    await register(client)
    bad_pw = await client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "nope-nope"})
    no_user = await client.post("/api/v1/auth/login", json={"email": "z@example.com", "password": "nope-nope"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json() == no_user.json()


async def test_me_requires_valid_token(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=auth("garbage"))).status_code == 401


async def test_refresh_rotates_and_detects_reuse(client: AsyncClient) -> None:
    await register(client)
    old_refresh = client.cookies.get("f3d_refresh")
    assert old_refresh

    first = await client.post("/api/v1/auth/refresh")
    assert first.status_code == 200
    new_refresh = client.cookies.get("f3d_refresh")
    assert new_refresh and new_refresh != old_refresh

    # Replay the rotated (old) token -> rejected and the family is revoked.
    client.cookies.clear()
    client.cookies.set("f3d_refresh", old_refresh, path="/api/v1/auth")
    reuse = await client.post("/api/v1/auth/refresh")
    assert reuse.status_code == 401

    # The newest token from the same family is now revoked too.
    client.cookies.clear()
    client.cookies.set("f3d_refresh", new_refresh, path="/api/v1/auth")
    after = await client.post("/api/v1/auth/refresh")
    assert after.status_code == 401


async def test_logout_revokes_refresh(client: AsyncClient) -> None:
    await register(client)
    token = client.cookies.get("f3d_refresh")
    res = await client.post("/api/v1/auth/logout")
    assert res.status_code == 204
    client.cookies.clear()
    client.cookies.set("f3d_refresh", token, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_refresh_without_cookie(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_auth_rate_limit(client: AsyncClient) -> None:
    client.app.state.settings.rate_limit_auth_per_minute = 3  # type: ignore[attr-defined]
    codes = []
    for _ in range(5):
        r = await client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "whatever"})
        codes.append(r.status_code)
    assert codes[:3] == [401, 401, 401]
    assert codes[3:] == [429, 429]
