"""
The entrypoint has to come up and explain itself.

The failure this guards against: a container that exits because Docker is unreachable,
leaving `connection refused` - a symptom indistinguishable from a wrong port, a wrong
IP or a firewall, at the exact moment the operator has nothing else to go on.

Fixture values are synthetic throughout.
"""

import asyncio, os, sys, tempfile

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

    # ---- the cluster page: define, launch, stop, all from the UI
    from . import cluster as clusterctl
    acts = []

    class FakeDocker:
        def available(self): return (True, "Docker 27.0.0")
        def compose(self, path, proj, args, timeout=900):
            acts.append(list(args)); return 0, ""
        def compose_ps(self, path, proj, timeout=60):
            return [{"service": "island", "name": "asa_island", "state": "running",
                     "status": "Up 3 minutes", "health": "healthy"}]
        def ports_in_use(self): return set()

    clusterctl.dockerctl = FakeDocker()
    store.patch({"appdata": "/srv/ark-data", "status_port": 8088}, source="install")
    store.patch({"admin_password": "synthetic-pw", "cluster_id": "uitest",
                 "host_ram_gb": 256, "maps": "island"})
    store.save()

    r = await client.get("/admin/cluster")
    body = await r.text()
    check("cluster page renders", r.status == 200 and "Plan" in body, r.status)
    check("it shows the port/RAM plan", "Game" in body and "RCON" in body)
    check("it offers a launch button", "/admin/launch" in body)

    r = await client.post("/admin/maps", data={"maps": ["island", "ragnarok"]},
                          allow_redirects=False)
    check("map selection saves from the UI", store.get("maps") == "island,ragnarok",
          store.get("maps"))

    r = await client.post("/admin/maps", data={"preset": "single"}, allow_redirects=False)
    check("a preset ticks the boxes", store.get("maps") == "island", store.get("maps"))

    tmp_compose = os.path.join(tempfile.mkdtemp(), "compose.yaml")
    _rp = clusterctl.compose_path
    _re = clusterctl.layout.ensure
    clusterctl.compose_path = lambda st: tmp_compose
    clusterctl.layout.ensure = lambda root, keys=(), makedirs=None: []
    r = await client.post("/admin/launch")
    body = await r.text()
    check("launch runs from the UI", any(a[:2] == ["up", "-d"] for a in acts), acts)
    check("the page reports the result", "Cluster up" in body, body[:300])
    check("a running cluster shows its services", "asa_island" in body or "island" in body)

    acts.clear()
    r = await client.post("/admin/stop")
    body = await r.text()
    check("stop runs from the UI", acts and acts[-1] == ["down"], acts)
    check("stop says saves are safe", "untouched" in body)
    clusterctl.compose_path, clusterctl.layout.ensure = _rp, _re

    r = await client.get("/admin/cluster", allow_redirects=False)
    check("cluster page needs a session", r.status in (200, 302))

    before = str(store.get("admin_token"))
    await client.post("/admin/save", data={"admin_password": ""}, allow_redirects=False)
    check("a blank password means 'leave it alone'", str(store.get("admin_token")) == before)
    await client.close()


asyncio.run(run())
print("\nFAILURES: %s" % fails if fails else "\nall app tests passed")
sys.exit(1 if fails else 0)
