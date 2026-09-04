"""
The entrypoint has to come up and explain itself.

The failure this guards against: a container that exits because Docker is unreachable,
leaving `connection refused` - a symptom indistinguishable from a wrong port, a wrong
IP or a firewall, at the exact moment the operator has nothing else to go on.

Fixture values are synthetic throughout.
"""

import asyncio, sys, tempfile

from aiohttp.test_utils import TestClient, TestServer

from .app import build_app, COOKIE
from .firstrun import bootstrap

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


DOCKER_DOWN = (False, "can't reach Docker. Mount the host socket into this container.")
DOCKER_UP = (True, "Docker 27.0.0, compose plugin present")


async def run():
    d = tempfile.mkdtemp()
    store, _created, code = bootstrap(d, environ={})

    # ---- Docker unreachable: the UI still serves, and says why
    client = TestClient(TestServer(build_app(store, docker=DOCKER_DOWN)))
    await client.start_server()

    r = await client.get("/setup")
    body = await r.text()
    check("setup page serves with no Docker", r.status == 200, r.status)
    check("it names the problem", "Docker not connected" in body)
    check("it says the UI still works",
          "cannot create or manage map containers" in body)
    check("it still shows the setup form", "Setup code" in body)

    r = await client.get("/healthz")
    js = await r.json()
    check("health endpoint answers", r.status == 200 and js["ok"] is True, js)
    check("health reports docker down", js["docker"] is False, js)

    r = await client.get("/", allow_redirects=False)
    check("root redirects to setup when unclaimed", r.status == 302, r.status)

    # ---- the setup code is what claims the instance
    r = await client.post("/setup", data={"code": "wrong-code"}, allow_redirects=False)
    check("a wrong code is refused", "printed in the container log" in await r.text())

    r = await client.post("/setup", data={"code": code}, allow_redirects=False)
    check("the right code is accepted", r.status == 302, r.status)
    check("and starts a session", COOKIE in r.cookies)

    r = await client.get("/admin")
    body = await r.text()
    check("settings page renders once claimed", r.status == 200 and "Save changes" in body)
    check("banner follows onto other pages", "Docker not connected" in body)
    check("timezone renders as a picker",
          '<select name="timezone"' in body and "Europe/London" in body)
    await client.close()

    # ---- Docker present: no banner
    store2, _c, code2 = bootstrap(tempfile.mkdtemp(), environ={})
    client = TestClient(TestServer(build_app(store2, docker=DOCKER_UP)))
    await client.start_server()
    body = await (await client.get("/setup")).text()
    check("no banner when Docker is fine", "Docker not connected" not in body)
    await client.close()

    # ---- saving a setting through the UI
    client = TestClient(TestServer(build_app(store, docker=DOCKER_DOWN)))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: str(store.get("admin_token"))})
    await client.post("/admin/save", data={"timezone": "Europe/London"},
                      allow_redirects=False)
    check("a saved timezone sticks", store.get("timezone") == "Europe/London",
          store.get("timezone"))

    await client.post("/admin/save", data={"timezone": "not-a-zone"},
                      allow_redirects=False)
    check("an invalid value is refused, not stored",
          store.get("timezone") == "Europe/London", store.get("timezone"))

    before = str(store.get("admin_token"))
    await client.post("/admin/save", data={"admin_password": ""}, allow_redirects=False)
    check("a blank password means 'leave it alone'", str(store.get("admin_token")) == before)
    await client.close()


asyncio.run(run())
print("\nFAILURES: %s" % fails if fails else "\nall app tests passed")
sys.exit(1 if fails else 0)
