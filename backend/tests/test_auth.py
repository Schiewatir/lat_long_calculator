import pytest
from sqlalchemy import select, update

from app.models import Session, User
from tests.conftest import login, make_user


async def test_login_me_logout(client, db):
    await make_user(db, "alice")
    user = await login(client, "alice")
    assert user["username"] == "alice" and user["role"] == "user"
    assert "gw_session" in client.cookies
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert (await client.post("/api/auth/logout")).status_code == 200
    client.cookies.clear()
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_wrong_credentials_same_message(client, db):
    await make_user(db, "alice")
    a = await client.post("/api/auth/login", json={"username": "alice", "password": "wrong password!!"})
    b = await client.post("/api/auth/login", json={"username": "nobody", "password": "wrong password!!"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


async def test_deactivated_account(client, db):
    await make_user(db, "bob", active=False)
    r = await client.post("/api/auth/login", json={"username": "bob", "password": "correct horse battery"})
    assert r.status_code == 401
    assert (await db.scalar(select(Session))) is None


async def test_brute_force_limit(client, db):
    await make_user(db, "alice")
    for _ in range(10):
        await client.post("/api/auth/login", json={"username": "alice", "password": "nope nope nope"})
    r = await client.post("/api/auth/login", json={"username": "alice", "password": "correct horse battery"})
    assert r.status_code == 429


async def test_idle_expiry(client, db):
    await make_user(db, "alice")
    await login(client, "alice")
    await db.execute(update(Session).values(expires_at=Session.created_at))
    await db.commit()
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_unauthenticated_and_csrf(client, db):
    assert (await client.get("/api/jobs")).status_code == 401
    await make_user(db, "alice")
    await login(client, "alice")
    del client.headers["X-CSRF-Token"]
    assert (await client.post("/api/auth/logout")).status_code == 403


async def test_non_admin_cannot_manage_users(client, db):
    await make_user(db, "alice")
    await login(client, "alice")
    assert (await client.get("/api/admin/users")).status_code == 403


async def test_admin_user_management(client, db):
    await make_user(db, "root", role="admin")
    await login(client, "root")
    r = await client.post("/api/admin/users", json={"username": "carol", "password": "a long password!", "role": "user"})
    assert r.status_code == 201
    dup = await client.post("/api/admin/users", json={"username": "CAROL", "password": "a long password!"})
    assert dup.status_code == 409
    weak = await client.post("/api/admin/users", json={"username": "dave", "password": "short"})
    assert weak.status_code == 422
    root_id = (await db.scalar(select(User.id).where(User.username == "root")))
    last = await client.patch(f"/api/admin/users/{root_id}", json={"active": False})
    assert last.status_code == 409
    demote = await client.patch(f"/api/admin/users/{root_id}", json={"role": "user"})
    assert demote.status_code == 409


async def test_forced_password_change_after_reset(client, db):
    await make_user(db, "root", role="admin")
    carol = await make_user(db, "carol")
    await login(client, "root")
    r = await client.post(f"/api/admin/users/{carol.id}/reset-password", json={"password": "temporary password"})
    assert r.status_code == 200
    client.cookies.clear()
    me = await login(client, "carol", "temporary password")
    assert me["must_change_password"]
    assert (await client.get("/api/jobs")).status_code == 403
    r = await client.post("/api/auth/change-password",
                          json={"current_password": "temporary password", "new_password": "my own new password"})
    assert r.status_code == 200
    assert (await client.get("/api/jobs")).status_code == 200


async def test_self_service_password_change(client, db):
    await make_user(db, "alice")
    await login(client, "alice")
    bad = await client.post("/api/auth/change-password", json={"current_password": "wrong", "new_password": "x" * 12})
    assert bad.status_code == 400
    weak = await client.post("/api/auth/change-password",
                             json={"current_password": "correct horse battery", "new_password": "short"})
    assert weak.status_code == 422


@pytest.mark.parametrize("existing_admin", [False, True])
async def test_bootstrap_admin(db, existing_admin):
    from app.cli import create_admin

    if existing_admin:
        await make_user(db, "root", role="admin")
    code = await create_admin("boss", "a long password!")
    assert (code == 0) is (not existing_admin)
    db.expire_all()
    admins = (await db.scalars(select(User).where(User.role == "admin"))).all()
    assert len(admins) == 1


async def test_capabilities_requires_login(client, db):
    assert (await client.get("/api/capabilities")).status_code == 401
    assert (await client.get("/api/health")).status_code == 200
