from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from client import AuthError, GuidewireClient, GuidewireError
from mock import MockGuidewireClient

load_dotenv()

app = Server("gwmcp")
_client = None


def get_client():
    global _client
    if _client is None:
        if os.environ.get("GW_MOCK", "").lower() in ("1", "true", "yes"):
            _client = MockGuidewireClient()
        else:
            _client = GuidewireClient(
                base_url=os.environ["GW_BASE_URL"],
                token_url=os.environ["GW_TOKEN_URL"],
                client_id=os.environ["GW_CLIENT_ID"],
                client_secret=os.environ["GW_CLIENT_SECRET"],
                scope=os.environ.get("GW_OAUTH_SCOPE"),
            )
    return _client


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    types.Tool(
        name="search_policies",
        description=(
            "Search Guidewire PolicyCenter policies. "
            "At least one filter is recommended; omitting all returns the first page of all policies."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "policyNumber": {
                    "type": "string",
                    "description": "Exact policy number (e.g. 01-123456-01)",
                },
                "accountNumber": {
                    "type": "string",
                    "description": "Account number — returns all policies under that account",
                },
                "status": {
                    "type": "string",
                    "enum": ["inForce", "expired", "canceled", "scheduled"],
                    "description": "Policy status filter",
                },
                "effectiveDateStart": {
                    "type": "string",
                    "description": "Effective date range start (YYYY-MM-DD)",
                },
                "effectiveDateEnd": {
                    "type": "string",
                    "description": "Effective date range end (YYYY-MM-DD)",
                },
                "productCode": {
                    "type": "string",
                    "description": "Product code (e.g. PersonalAuto, HomeOwners, CommercialPackage)",
                },
                "pageSize": {
                    "type": "integer",
                    "description": "Results per page (default 25, max 100)",
                    "default": 25,
                },
            },
        },
    ),
    types.Tool(
        name="search_accounts",
        description=(
            "Search Guidewire PolicyCenter accounts by number, name, or email. "
            "Supports both personal (firstName/lastName) and commercial (companyName) accounts."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "accountNumber": {
                    "type": "string",
                    "description": "Exact account number",
                },
                "firstName": {
                    "type": "string",
                    "description": "First name (personal accounts)",
                },
                "lastName": {
                    "type": "string",
                    "description": "Last name (personal accounts)",
                },
                "companyName": {
                    "type": "string",
                    "description": "Company name (commercial accounts)",
                },
                "emailAddress": {
                    "type": "string",
                    "description": "Contact email address",
                },
                "pageSize": {
                    "type": "integer",
                    "description": "Results per page (default 25, max 100)",
                    "default": 25,
                },
            },
        },
    ),
    types.Tool(
        name="search_jobs",
        description=(
            "Search Guidewire PolicyCenter policy transactions: submissions, renewals, "
            "endorsements, cancellations, and reinstatements."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "jobNumber": {
                    "type": "string",
                    "description": "Exact job/transaction number",
                },
                "policyNumber": {
                    "type": "string",
                    "description": "Find all transactions for a specific policy",
                },
                "accountNumber": {
                    "type": "string",
                    "description": "Find all transactions under an account",
                },
                "jobType": {
                    "type": "string",
                    "enum": ["Submission", "Renewal", "Endorsement", "Cancellation", "Reinstatement"],
                    "description": "Type of policy transaction",
                },
                "status": {
                    "type": "string",
                    "enum": ["Draft", "Quoted", "Bound", "Withdrawn", "Declined"],
                    "description": "Transaction status",
                },
                "pageSize": {
                    "type": "integer",
                    "description": "Results per page (default 25, max 100)",
                    "default": 25,
                },
            },
        },
    ),
]


# ---------------------------------------------------------------------------
# Result formatters
# ---------------------------------------------------------------------------

def _attrs(item: dict) -> dict:
    """Return the attributes dict whether the API uses JSON:API style or flat."""
    return item.get("attributes", item)


def _fmt_policy(p: dict) -> str:
    a = _attrs(p)
    lines = [f"Policy: {a.get('policyNumber') or p.get('id', 'N/A')}"]
    if v := a.get("status"):
        lines.append(f"  Status:    {v}")
    if prod := a.get("product"):
        name = prod.get("name") or prod.get("code") if isinstance(prod, dict) else prod
        lines.append(f"  Product:   {name}")
    eff = a.get("effectiveDate") or a.get("effectiveDateStr", "")
    exp = a.get("expirationDate") or a.get("expirationDateStr", "")
    if eff:
        lines.append(f"  Dates:     {eff} → {exp}")
    if acct := a.get("account"):
        num = acct.get("accountNumber") if isinstance(acct, dict) else acct
        lines.append(f"  Account:   {num}")
    return "\n".join(lines)


def _fmt_account(a_item: dict) -> str:
    a = _attrs(a_item)
    lines = [f"Account: {a.get('accountNumber') or a_item.get('id', 'N/A')}"]
    if company := a.get("companyName"):
        lines.append(f"  Company:  {company}")
    else:
        first = a.get("firstName", "")
        last = a.get("lastName", "")
        if first or last:
            lines.append(f"  Name:     {first} {last}".rstrip())
    if email := a.get("emailAddress"):
        lines.append(f"  Email:    {email}")
    return "\n".join(lines)


def _fmt_job(j: dict) -> str:
    a = _attrs(j)
    lines = [f"Job: {a.get('jobNumber') or j.get('id', 'N/A')}"]
    if v := a.get("jobType") or a.get("type") or a.get("subtype"):
        lines.append(f"  Type:     {v}")
    if v := a.get("status"):
        lines.append(f"  Status:   {v}")
    if policy := a.get("policy"):
        num = policy.get("policyNumber") if isinstance(policy, dict) else policy
        lines.append(f"  Policy:   {num}")
    if v := a.get("createTime") or a.get("createdDate") or a.get("createDate"):
        lines.append(f"  Created:  {v}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP handlers
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    client = get_client()
    try:
        if name == "search_policies":
            result = await client.search(
                "/policies",
                {
                    "policyNumber": arguments.get("policyNumber"),
                    "accountNumber": arguments.get("accountNumber"),
                    "status": arguments.get("status"),
                    "effectiveDateStart": arguments.get("effectiveDateStart"),
                    "effectiveDateEnd": arguments.get("effectiveDateEnd"),
                    "productCode": arguments.get("productCode"),
                    "pageSize": arguments.get("pageSize", 25),
                },
            )
            items = result.get("data", [])
            if not items:
                text = "No policies found matching the search criteria."
            else:
                noun = "policy" if len(items) == 1 else "policies"
                text = f"Found {len(items)} {noun}:\n\n" + "\n\n".join(_fmt_policy(p) for p in items)

        elif name == "search_accounts":
            result = await client.search(
                "/accounts",
                {
                    "accountNumber": arguments.get("accountNumber"),
                    "firstName": arguments.get("firstName"),
                    "lastName": arguments.get("lastName"),
                    "companyName": arguments.get("companyName"),
                    "emailAddress": arguments.get("emailAddress"),
                    "pageSize": arguments.get("pageSize", 25),
                },
            )
            items = result.get("data", [])
            if not items:
                text = "No accounts found matching the search criteria."
            else:
                noun = "account" if len(items) == 1 else "accounts"
                text = f"Found {len(items)} {noun}:\n\n" + "\n\n".join(_fmt_account(a) for a in items)

        elif name == "search_jobs":
            result = await client.search(
                "/jobs",
                {
                    "jobNumber": arguments.get("jobNumber"),
                    "policyNumber": arguments.get("policyNumber"),
                    "accountNumber": arguments.get("accountNumber"),
                    "jobType": arguments.get("jobType"),
                    "status": arguments.get("status"),
                    "pageSize": arguments.get("pageSize", 25),
                },
            )
            items = result.get("data", [])
            if not items:
                text = "No jobs found matching the search criteria."
            else:
                noun = "job" if len(items) == 1 else "jobs"
                text = f"Found {len(items)} {noun}:\n\n" + "\n\n".join(_fmt_job(j) for j in items)

        else:
            text = f"Unknown tool: {name}"

    except AuthError as e:
        text = f"Authentication error: {e}"
    except GuidewireError as e:
        text = f"Guidewire API error: {e}"
    except Exception as e:
        text = f"Unexpected error ({type(e).__name__}): {e}"

    return [types.TextContent(type="text", text=text)]


# ---------------------------------------------------------------------------
# Entry point — stdio (local) or SSE (remote) based on GW_TRANSPORT
# ---------------------------------------------------------------------------

async def _run_stdio() -> None:
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def _run_sse() -> None:
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    import uvicorn

    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
            await app.run(r, w, app.create_initialization_options())

    async def health(request: Request):
        return JSONResponse({"status": "ok", "server": "gwmcp"})

    starlette_app = Starlette(
        routes=[
            Route("/health", endpoint=health),
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ]
    )

    port = int(os.environ.get("PORT", 8000))
    print(f"gwmcp SSE server listening on port {port}")
    uvicorn.run(starlette_app, host="0.0.0.0", port=port)


def main() -> None:
    transport = os.environ.get("GW_TRANSPORT", "stdio").lower()
    if transport == "sse":
        _run_sse()
    else:
        asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
