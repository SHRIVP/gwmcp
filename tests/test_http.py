"""End-to-end: run the server over Streamable HTTP in mock mode and drive it with the MCP client."""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    port = _free_port()
    env = {**os.environ, "GW_MOCK": "true", "GW_TRANSPORT": "http", "PORT": str(port),
           "GW_PUBLIC_URL": "https://example.com/mcp"}
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{url}/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail(proc.stdout.read().decode())
    yield url
    proc.terminate()
    proc.wait(timeout=5)


async def _session_run(url, fn):
    async with streamable_http_client(f"{url}/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)


def test_tools_and_ui_metadata(base_url):
    async def go(s):
        tools = {t.name: t for t in (await s.list_tools()).tools}
        res = await s.read_resource("ui://gwmcp/policy-view.html")
        return tools, res

    tools, res = asyncio.run(_session_run(base_url, go))
    assert {"search_policies", "open_policy", "add_vehicle", "quote_policy_change", "bind_policy_change"} <= set(tools)
    meta = tools["open_policy"].meta
    assert meta["ui"]["resourceUri"] == "ui://gwmcp/policy-view.html"
    assert tools["bind_policy_change"].meta["ui"]["visibility"] == ["app"]
    assert tools["search_policies"].meta is None

    content = res.contents[0]
    assert content.mimeType == "text/html;profile=mcp-app"
    assert "ext-apps@1.7.5" in content.text
    ui = content.meta["ui"]
    assert ui["csp"]["resourceDomains"] == ["https://unpkg.com"]
    # Value documented by Claude for https://example.com/mcp
    assert ui["domain"] == "c3d80a4ed901ee05b21755a88273b4a4.claudemcpcontent.com"


def test_edit_flow_over_http(base_url):
    async def go(s):
        out = {}
        out["start"] = await s.call_tool("start_policy_change",
                                         {"policyNumber": "PA-200006-01", "effectiveDate": "2027-02-01"})
        out["add"] = await s.call_tool("add_vehicle",
                                       {"policyNumber": "PA-200006-01", "year": 2025, "make": "Kia", "model": "EV6"})
        out["quote"] = await s.call_tool("quote_policy_change", {"policyNumber": "PA-200006-01"})
        out["bad"] = await s.call_tool("update_coverage", {"policyNumber": "PA-200006-01",
                                                           "coverageCode": "PACollision", "value": "lots"})
        out["search"] = await s.call_tool("search_policies", {"policyNumber": "PA-200006-01"})
        return out

    out = asyncio.run(_session_run(base_url, go))
    assert out["start"].structuredContent["draft"]["status"] == "Draft"
    assert "Added the vehicle." in out["add"].content[0].text
    q = out["quote"].structuredContent["draft"]["quote"]
    assert q["annualPremium"] > out["quote"].structuredContent["premium"]
    assert out["bad"].isError and "Allowed" in out["bad"].content[0].text
    assert not out["search"].isError and "PA-200006-01" in out["search"].content[0].text


def test_legacy_sse_and_preview_still_served(base_url):
    with httpx.stream("GET", f"{base_url}/sse", timeout=5) as r:
        assert r.status_code == 200
        first = next(r.iter_lines())
        assert first.startswith("event: endpoint")
    assert httpx.get(f"{base_url}/preview").status_code == 200
