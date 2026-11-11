"""Persist mock Policy System state in Redis, for serverless hosts such as Vercel.

On serverless, each request can land on a fresh process, so the mock's
in-memory edits would be lost. ``PersistentMockClient`` loads the mock's state
from Redis for every call and, after an edit, saves it back with a
compare-and-set on a revision counter, so two edits arriving at once can't
overwrite each other (the loser reloads and re-applies).

Redis is reached through Upstash's REST API (plain HTTPS, no connection pool),
configured by the env vars Vercel's Upstash integration sets:
``KV_REST_API_URL`` / ``KV_REST_API_TOKEN`` or
``UPSTASH_REDIS_REST_URL`` / ``UPSTASH_REDIS_REST_TOKEN``.
"""

from __future__ import annotations

import json
import os

import httpx

from client import EverywireError
from mock import MockEverywireClient

STATE_KEY = os.environ.get("GW_STATE_KEY", "gwmcp:mock:state")

# KEYS[1] = state blob, KEYS[2] = revision; ARGV[1] = new blob, ARGV[2] = expected revision
_CAS_SCRIPT = """
local rev = tonumber(redis.call('GET', KEYS[2]) or '0')
if rev ~= tonumber(ARGV[2]) then return 0 end
redis.call('SET', KEYS[1], ARGV[1])
redis.call('SET', KEYS[2], rev + 1)
return 1
"""


class UpstashStore:
    def __init__(self, url: str, token: str, key: str = STATE_KEY, transport: httpx.AsyncBaseTransport | None = None):
        self.url = url.rstrip("/")
        self.token = token
        self.key = key
        self.rev_key = key + ":rev"
        self._transport = transport

    @classmethod
    def from_env(cls) -> "UpstashStore | None":
        url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
        token = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        return cls(url, token) if url and token else None

    async def _command(self, *args) -> object:
        async with httpx.AsyncClient(transport=self._transport, timeout=10) as http:
            resp = await http.post(self.url, json=[str(a) for a in args],
                                   headers={"Authorization": f"Bearer {self.token}"})
        body = resp.json() if resp.content else {}
        if resp.status_code != 200 or "error" in body:
            raise EverywireError(503, f"State store error: {body.get('error') or resp.text[:200]}")
        return body.get("result")

    async def load(self) -> tuple[dict | None, int]:
        blob, rev = await self._command("MGET", self.key, self.rev_key)
        return (json.loads(blob) if blob else None), int(rev or 0)

    async def save(self, state: dict, expected_rev: int) -> bool:
        ok = await self._command("EVAL", _CAS_SCRIPT, 2, self.key, self.rev_key,
                                 json.dumps(state, separators=(",", ":")), expected_rev)
        return ok == 1

    async def clear(self) -> None:
        await self._command("DEL", self.key, self.rev_key)


class PersistentMockClient:
    """Same interface as MockEverywireClient, with state kept in a store between requests."""

    supports_editing = True
    MAX_ATTEMPTS = 5

    def __init__(self, store):
        self.store = store

    async def _run(self, method: str, *args, write: bool):
        for _ in range(self.MAX_ATTEMPTS):
            state, rev = await self.store.load()
            mock = MockEverywireClient.from_state(state) if state else MockEverywireClient()
            result = await getattr(mock, method)(*args)  # EverywireError propagates; nothing is saved
            if not write or await self.store.save(mock.to_state(), rev):
                return result
        raise EverywireError(409, "Policy System is busy with another change to this data. Try again.")

    # reads
    async def search(self, path, params):
        return await self._run("search", path, params, write=False)

    async def get_policy_view(self, *a):
        return await self._run("get_policy_view", *a, write=False)

    # writes
    async def start_policy_change(self, *a):
        return await self._run("start_policy_change", *a, write=True)

    async def add_vehicle(self, *a):
        return await self._run("add_vehicle", *a, write=True)

    async def remove_vehicle(self, *a):
        return await self._run("remove_vehicle", *a, write=True)

    async def add_driver(self, *a):
        return await self._run("add_driver", *a, write=True)

    async def update_coverage(self, *a):
        return await self._run("update_coverage", *a, write=True)

    async def quote_policy_change(self, *a):
        return await self._run("quote_policy_change", *a, write=True)

    async def bind_policy_change(self, *a):
        return await self._run("bind_policy_change", *a, write=True)

    async def withdraw_policy_change(self, *a):
        return await self._run("withdraw_policy_change", *a, write=True)
