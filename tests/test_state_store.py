import asyncio
import json

import httpx
import pytest

from client import EverywireError
from state_store import STATE_KEY, PersistentMockClient, UpstashStore


class FakeUpstash:
    """Speaks the slice of Upstash's REST protocol the store uses: MGET, EVAL (our CAS script), DEL."""

    def __init__(self):
        self.data: dict[str, str] = {}
        self.calls: list[str] = []
        self.before_eval = None  # hook to simulate a concurrent writer

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok"
        cmd, *args = json.loads(request.content)
        self.calls.append(cmd)
        if cmd == "MGET":
            return httpx.Response(200, json={"result": [self.data.get(k) for k in args]})
        if cmd == "EVAL":
            if self.before_eval:
                hook, self.before_eval = self.before_eval, None
                hook(self)
            _script, _n, key, rev_key, blob, expected = args
            rev = int(self.data.get(rev_key, 0))
            if rev != int(expected):
                return httpx.Response(200, json={"result": 0})
            self.data[key], self.data[rev_key] = blob, str(rev + 1)
            return httpx.Response(200, json={"result": 1})
        if cmd == "DEL":
            for k in args:
                self.data.pop(k, None)
            return httpx.Response(200, json={"result": len(args)})
        return httpx.Response(400, json={"error": f"unsupported {cmd}"})


def make(fake: FakeUpstash) -> PersistentMockClient:
    store = UpstashStore("https://fake.upstash.io", "tok", transport=httpx.MockTransport(fake.handler))
    return PersistentMockClient(store)


def run(coro):
    return asyncio.run(coro)


def test_edits_survive_across_clients():
    fake = FakeUpstash()
    run(make(fake).start_policy_change("PA-200001-01", "2026-12-01", "Add car"))
    run(make(fake).add_vehicle("PA-200001-01", 2024, "Honda", "Civic"))  # a "different process"
    v = run(make(fake).get_policy_view("PA-200001-01"))
    assert [x["model"] for x in v["draft"]["vehicles"] if x["change"] == "added"] == ["Civic"]


def test_reads_do_not_write():
    fake = FakeUpstash()
    run(make(fake).get_policy_view("PA-200001-01"))
    run(make(fake).search("/policies", {"status": "inForce"}))
    assert "EVAL" not in fake.calls and fake.data == {}


def test_failed_edit_saves_nothing():
    fake = FakeUpstash()
    with pytest.raises(EverywireError):
        run(make(fake).add_vehicle("PA-200001-01", 2024, "Honda", "Civic"))  # no open change
    assert fake.data == {}


def test_concurrent_write_is_retried_not_lost():
    fake = FakeUpstash()
    client = make(fake)
    run(client.start_policy_change("PA-200001-01", "2026-12-01"))

    def other_writer(f):
        # Someone else adds a vehicle between our load and our save.
        # (Runs on its own thread: we're inside the first client's event loop here.)
        import threading

        f_before = dict(f.data)
        t = threading.Thread(target=lambda: run(make(f).add_vehicle("PA-200001-01", 2020, "Mazda", "CX-5")))
        t.start()
        t.join()
        assert f.data != f_before

    fake.before_eval = other_writer
    v = run(client.update_coverage("PA-200001-01", "PARoadside", "Included"))
    added = [x["model"] for x in v["draft"]["vehicles"] if x["change"] == "added"]
    roadside = next(c for c in v["draft"]["coverages"] if c["code"] == "PARoadside")
    assert added == ["CX-5"] and roadside["value"] == "Included"  # both edits kept


def test_store_errors_surface_as_everywire_errors():
    store = UpstashStore("https://fake.upstash.io", "tok",
                         transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "bad token"})))
    with pytest.raises(EverywireError, match="bad token"):
        run(PersistentMockClient(store).get_policy_view("PA-200001-01"))


def test_create_account_persists():
    fake = FakeUpstash()
    run(make(fake).create_account("Maria", "Lopez", None, "maria@example.com",
                                  "12 Oak St", "Springfield", "IL", "62704"))
    found = run(make(fake).search("/accounts", {"lastName": "Lopez"}))["data"]
    assert [a["attributes"]["accountNumber"] for a in found] == ["ACC-10006"]


class CountingLookup:
    """Fails the test if called while a store load/apply/save cycle is in progress."""

    def __init__(self, client_ref):
        self.client_ref = client_ref
        self.calls = 0

    async def __call__(self, address):
        assert not self.client_ref["in_run"], "lookup ran inside _run"
        self.calls += 1
        return {"input": address,
                "geocode": {"status": "ok", "source": "US Census Geocoder", "matchedAddress": "M",
                            "state": "TX", "county": "Travis County", "lat": 30.0, "lon": -97.0},
                "flood": {"status": "ok", "source": "FEMA National Flood Hazard Layer", "zone": "AE",
                          "subtype": None}}


def make_with_lookup(fake):
    ref = {"in_run": False}
    lookup = CountingLookup(ref)
    store = UpstashStore("https://fake.upstash.io", "tok", transport=httpx.MockTransport(fake.handler))
    client = PersistentMockClient(store, property_lookup=lookup)
    inner = client._run

    async def tracked(*a, **kw):
        ref["in_run"] = True
        try:
            return await inner(*a, **kw)
        finally:
            ref["in_run"] = False
    client._run = tracked
    return client, lookup


def test_persistent_lookup_runs_outside_run():
    from datetime import date, timedelta
    fake = FakeUpstash()
    client, lookup = make_with_lookup(fake)
    eff = (date.today() + timedelta(days=10)).isoformat()
    num = run(client.start_submission("ACC-10001", "HomeOwners", eff))["policyNumber"]

    def concurrent_bump(f):  # the first save loses a race and must retry
        f.data[f"{STATE_KEY}:rev"] = str(int(f.data.get(f"{STATE_KEY}:rev", 0)) + 1)
    fake.before_eval = concurrent_bump
    v = run(client.set_home_details(num, "1 Main St, Austin, TX 78701", 1998, "frame", 2000))
    assert lookup.calls == 1
    assert v["draft"]["home"]["lookup"]["flood"]["zone"] == "AE"
    q = run(client.quote_policy_change(num))
    assert lookup.calls == 1 and len(q["draft"]["quote"]["factors"]) == 7


def test_home_state_round_trips():
    from datetime import date, timedelta
    fake = FakeUpstash()
    client, _ = make_with_lookup(fake)
    eff = (date.today() + timedelta(days=10)).isoformat()
    num = run(client.start_submission("ACC-10001", "HomeOwners", eff))["policyNumber"]
    run(client.set_home_details(num, "1 Main St, Austin, TX 78701", 1998))
    fresh, _ = make_with_lookup(fake)
    v = run(fresh.get_policy_view(num))
    assert v["draft"]["home"]["yearBuilt"] == 1998 and v["draft"]["kind"] == "submission"
