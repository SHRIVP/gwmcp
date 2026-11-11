from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv
import mcp.types as types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server

from client import AuthError, EverywireClient, EverywireError
from mock import MockEverywireClient
from views import policy_view_text

load_dotenv()

app = Server("gwmcp")
_client = None

HERE = Path(__file__).parent
VIEW_URI = "ui://gwmcp/policy-view.html"
VIEW_MIME = "text/html;profile=mcp-app"


def is_mock() -> bool:
    return os.environ.get("GW_MOCK", "").lower() in ("1", "true", "yes")


def get_client():
    global _client
    if _client is None:
        if is_mock():
            from state_store import PersistentMockClient, UpstashStore

            # With Redis configured (e.g. on Vercel), edits survive across serverless
            # invocations; otherwise they live in this process's memory.
            store = UpstashStore.from_env()
            _client = PersistentMockClient(store) if store else MockEverywireClient()
        else:
            _client = EverywireClient(
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

def _tool(name: str, title: str, description: str, properties: dict, required: list[str] | None = None,
          *, ui: bool = False, app_only: bool = False, read_only: bool = False,
          destructive: bool = False) -> types.Tool:
    spec: dict = {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": {"type": "object", "properties": properties, **({"required": required} if required else {})},
        "annotations": {
            "title": title,
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "openWorldHint": False,
        },
    }
    if ui:
        ui_meta: dict = {"resourceUri": VIEW_URI}
        if app_only:
            ui_meta["visibility"] = ["app"]
        # "ui/resourceUri" is the legacy flat key some hosts still read.
        spec["_meta"] = {"ui": ui_meta, "ui/resourceUri": VIEW_URI}
    return types.Tool.model_validate(spec)


POLICY_NUMBER = {"type": "string", "description": "Policy number, e.g. PA-200001-01"}

SEARCH_TOOLS = [
    _tool(
        "search_policies", "Search policies",
        "Search Everywire Policy System policies. "
        "At least one filter is recommended; omitting all returns the first page of all policies.",
        {
            "policyNumber": {"type": "string", "description": "Exact policy number (e.g. 01-123456-01)"},
            "accountNumber": {"type": "string",
                              "description": "Account number — returns all policies under that account"},
            "status": {"type": "string", "enum": ["inForce", "expired", "canceled", "scheduled"],
                       "description": "Policy status filter"},
            "effectiveDateStart": {"type": "string", "description": "Effective date range start (YYYY-MM-DD)"},
            "effectiveDateEnd": {"type": "string", "description": "Effective date range end (YYYY-MM-DD)"},
            "productCode": {"type": "string",
                            "description": "Product code (e.g. PersonalAuto, HomeOwners, CommercialPackage)"},
            "pageSize": {"type": "integer", "description": "Results per page (default 25, max 100)", "default": 25},
        },
        read_only=True,
    ),
    _tool(
        "search_accounts", "Search accounts",
        "Search Everywire Policy System accounts by number, name, or email. "
        "Supports both personal (firstName/lastName) and commercial (companyName) accounts.",
        {
            "accountNumber": {"type": "string", "description": "Exact account number"},
            "firstName": {"type": "string", "description": "First name (personal accounts)"},
            "lastName": {"type": "string", "description": "Last name (personal accounts)"},
            "companyName": {"type": "string", "description": "Company name (commercial accounts)"},
            "emailAddress": {"type": "string", "description": "Contact email address"},
            "pageSize": {"type": "integer", "description": "Results per page (default 25, max 100)", "default": 25},
        },
        read_only=True,
    ),
    _tool(
        "search_jobs", "Search jobs",
        "Search Everywire Policy System policy transactions: submissions, renewals, "
        "endorsements, cancellations, and reinstatements.",
        {
            "jobNumber": {"type": "string", "description": "Exact job/transaction number"},
            "policyNumber": {"type": "string", "description": "Find all transactions for a specific policy"},
            "accountNumber": {"type": "string", "description": "Find all transactions under an account"},
            "jobType": {"type": "string",
                        "enum": ["Submission", "Renewal", "Endorsement", "Cancellation", "Reinstatement"],
                        "description": "Type of policy transaction"},
            "status": {"type": "string", "enum": ["Draft", "Quoted", "Bound", "Withdrawn", "Declined"],
                       "description": "Transaction status"},
            "pageSize": {"type": "integer", "description": "Results per page (default 25, max 100)", "default": 25},
        },
        read_only=True,
    ),
]

ACCOUNT_TOOLS = [
    _tool(
        "create_account", "Create account",
        "Create a new customer account, for a person (firstName + lastName) or a company (companyName). "
        "Do this before start_submission when the customer isn't in the system yet; search_accounts first "
        "to avoid duplicates.",
        {
            "firstName": {"type": "string", "description": "First name (person)"},
            "lastName": {"type": "string", "description": "Last name (person)"},
            "companyName": {"type": "string", "description": "Company name (company accounts, instead of names)"},
            "email": {"type": "string", "description": "Contact email address"},
            "phone": {"type": "string", "description": "Contact phone number, if known"},
            "addressLine1": {"type": "string", "description": "Mailing street address, e.g. 12 Oak St"},
            "city": {"type": "string", "description": "Mailing city"},
            "state": {"type": "string", "description": "2-letter state code, e.g. IL"},
            "postalCode": {"type": "string", "description": "5-digit ZIP or ZIP+4"},
        },
        ["email", "addressLine1", "city", "state", "postalCode"],
    ),
]

EDIT_TOOLS = [
    _tool(
        "open_policy", "Open policy",
        "Open a policy in the interactive Policy System view, showing its vehicles, drivers, coverages, premium and any "
        "open policy change. Call this before editing, and to show the user a policy.",
        {"policyNumber": POLICY_NUMBER}, ["policyNumber"], ui=True, read_only=True,
    ),
    _tool(
        "start_policy_change", "Start policy change",
        "Start a policy change (endorsement) on an in-force or scheduled policy. Required before any edit; "
        "edits stay in this draft and don't touch the live policy until the user binds it.",
        {
            "policyNumber": POLICY_NUMBER,
            "effectiveDate": {"type": "string", "description": "Date the change takes effect (YYYY-MM-DD), "
                                                               "within the policy term"},
            "description": {"type": "string", "description": "Short reason for the change, e.g. 'Add new car'"},
        },
        ["policyNumber", "effectiveDate"], ui=True,
    ),
    _tool(
        "add_vehicle", "Add vehicle",
        "Add a vehicle to the open policy change on a Personal Auto policy.",
        {
            "policyNumber": POLICY_NUMBER,
            "year": {"type": "integer", "description": "Model year, e.g. 2024"},
            "make": {"type": "string", "description": "Manufacturer, e.g. Honda"},
            "model": {"type": "string", "description": "Model, e.g. Civic"},
            "vin": {"type": "string", "description": "Vehicle identification number, if known"},
        },
        ["policyNumber", "year", "make", "model"], ui=True,
    ),
    _tool(
        "remove_vehicle", "Remove vehicle",
        "Remove a vehicle from the open policy change on a Personal Auto policy, by its vehicle id.",
        {
            "policyNumber": POLICY_NUMBER,
            "vehicleId": {"type": "string", "description": "Vehicle id as shown by open_policy, e.g. veh-1"},
        },
        ["policyNumber", "vehicleId"], ui=True,
    ),
    _tool(
        "add_driver", "Add driver",
        "Add a driver to the open policy change on a Personal Auto policy. Drivers under 25 on the "
        "change's effective date carry a young-driver surcharge.",
        {
            "policyNumber": POLICY_NUMBER,
            "firstName": {"type": "string", "description": "Driver's first name"},
            "lastName": {"type": "string", "description": "Driver's last name"},
            "dateOfBirth": {"type": "string", "description": "Date of birth (YYYY-MM-DD)"},
            "licenseNumber": {"type": "string", "description": "Driver's license number, if known"},
        },
        ["policyNumber", "firstName", "lastName", "dateOfBirth"], ui=True,
    ),
    _tool(
        "update_coverage", "Update coverage",
        "Change a coverage limit or deductible on the open policy change, using one of the allowed values "
        "listed by open_policy.",
        {
            "policyNumber": POLICY_NUMBER,
            "coverageCode": {"type": "string", "description": "Coverage code, e.g. PACollision"},
            "value": {"type": "string", "description": "One of the coverage's allowed values, "
                                                       "e.g. '$1,000 deductible'"},
        },
        ["policyNumber", "coverageCode", "value"], ui=True,
    ),
    _tool(
        "quote_policy_change", "Quote policy change",
        "Quote the open policy change to get the new annual premium and the change in cost. "
        "After quoting, the user reviews the change and binds it from the Policy System view.",
        {"policyNumber": POLICY_NUMBER}, ["policyNumber"], ui=True,
    ),
    _tool(
        "withdraw_policy_change", "Withdraw policy change",
        "Withdraw (discard) the open policy change. The live policy is left unchanged.",
        {"policyNumber": POLICY_NUMBER}, ["policyNumber"], ui=True, destructive=True,
    ),
    # Only the view's Bind button can call this: binding applies the change to the
    # live policy, so a person makes that call, not Claude.
    _tool(
        "bind_policy_change", "Bind policy change",
        "Bind the quoted policy change so it applies to the live policy.",
        {"policyNumber": POLICY_NUMBER}, ["policyNumber"], ui=True, app_only=True, destructive=True,
    ),
]

TOOLS = SEARCH_TOOLS + ACCOUNT_TOOLS + EDIT_TOOLS

# tool name -> (client method, argument names in call order, headline for Claude)
EDIT_DISPATCH = {
    "open_policy": ("get_policy_view", ["policyNumber"], None),
    "start_policy_change": ("start_policy_change", ["policyNumber", "effectiveDate", "description"],
                            "Started a policy change."),
    "add_vehicle": ("add_vehicle", ["policyNumber", "year", "make", "model", "vin"], "Added the vehicle."),
    "remove_vehicle": ("remove_vehicle", ["policyNumber", "vehicleId"], "Removed the vehicle."),
    "add_driver": ("add_driver", ["policyNumber", "firstName", "lastName", "dateOfBirth", "licenseNumber"],
                   "Added the driver."),
    "update_coverage": ("update_coverage", ["policyNumber", "coverageCode", "value"], "Updated the coverage."),
    "quote_policy_change": ("quote_policy_change", ["policyNumber"], "Quoted the policy change."),
    "withdraw_policy_change": ("withdraw_policy_change", ["policyNumber"], "Withdrew the policy change."),
    "bind_policy_change": ("bind_policy_change", ["policyNumber"], "Bound the policy change."),
}


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
    if phone := a.get("phone"):
        lines.append(f"  Phone:    {phone}")
    if addr := a.get("primaryAddress"):
        lines.append(f"  Address:  {addr['addressLine1']}, {addr['city']}, {addr['state']} {addr['postalCode']}")
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
# UI resource
# ---------------------------------------------------------------------------

def _ui_domain() -> str | None:
    """Claude's stable sandbox origin for this server: sha256(server URL)[:32].claudemcpcontent.com.

    Only set when GW_PUBLIC_URL (the exact URL added to Claude, e.g.
    https://example.up.railway.app/mcp) is configured; a wrong value makes
    Claude reject the view, while leaving it out is fine for this view.
    """
    url = os.environ.get("GW_PUBLIC_URL", "").strip()
    if not url:
        return None
    return hashlib.sha256(url.encode()).hexdigest()[:32] + ".claudemcpcontent.com"


def view_resource_meta() -> dict:
    ui: dict = {
        "csp": {"resourceDomains": ["https://unpkg.com"]},  # the view loads the MCP Apps SDK from unpkg
        "prefersBorder": True,
        "preferredDisplayMode": "fullscreen",  # Claude extension: open in the side panel when available
    }
    if domain := _ui_domain():
        ui["domain"] = domain
    return {"ui": ui}


def view_html() -> str:
    return (HERE / "ui" / "policy_view.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# MCP handlers
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


@app.list_resources()
async def list_resources() -> list[types.Resource]:
    return [
        types.Resource(
            uri=VIEW_URI,
            name="Policy System policy view",
            description="Interactive view of a policy and its open policy change",
            mimeType=VIEW_MIME,
        )
    ]


@app.read_resource()
async def read_resource(uri) -> list[ReadResourceContents]:
    if str(uri) != VIEW_URI:
        raise ValueError(f"Unknown resource: {uri}")
    return [ReadResourceContents(content=view_html(), mime_type=VIEW_MIME, meta=view_resource_meta())]


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=True)


async def _call_edit_tool(name: str, arguments: dict) -> types.CallToolResult:
    client = get_client()
    if not getattr(client, "supports_editing", False):
        return _error(
            "Policy editing is only wired up in mock mode (GW_MOCK=true) so far. "
            "Connecting it to a live Policy System tenant's Cloud API is the next step."
        )
    method, arg_names, headline = EDIT_DISPATCH[name]
    args = [arguments.get(a) for a in arg_names]
    # Drop trailing optional arguments the caller left out so client defaults apply.
    while args and args[-1] is None and len(args) > 1:
        args.pop()
    view = await getattr(client, method)(*args)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=policy_view_text(view, headline))],
        structuredContent=view,
    )


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name in EDIT_DISPATCH:
            return await _call_edit_tool(name, arguments)

        client = get_client()
        if name == "create_account":
            if not getattr(client, "supports_editing", False):
                return _error("Creating accounts is only wired up in mock mode (GW_MOCK=true) so far.")
            acct = await client.create_account(
                *(arguments.get(k) for k in ("firstName", "lastName", "companyName", "email",
                                             "addressLine1", "city", "state", "postalCode", "phone")))
            text = f"Created account {acct['attributes']['accountNumber']}.\n\n{_fmt_account(acct)}"

        elif name == "search_policies":
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
            return _error(f"Unknown tool: {name}")

    except AuthError as e:
        return _error(f"Authentication error: {e}")
    except EverywireError as e:
        return _error(f"Everywire API error: {e}")
    except Exception as e:
        return _error(f"Unexpected error ({type(e).__name__}): {e}")

    return [types.TextContent(type="text", text=text)]


# ---------------------------------------------------------------------------
# Entry point — stdio (local) or HTTP (remote) based on GW_TRANSPORT
# ---------------------------------------------------------------------------

async def _run_stdio() -> None:
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


async def _log_rpc(scope, receive):
    """Debug aid (GW_LOG_RPC=true): log each JSON-RPC method, and the client's
    MCP Apps support on initialize. Returns a receive() that replays the body."""
    import json

    chunks, more = [], True
    while more:
        msg = await receive()
        chunks.append(msg.get("body", b""))
        more = msg.get("more_body", False)
    body = b"".join(chunks)
    try:
        payload = json.loads(body) if body else None
        for m in payload if isinstance(payload, list) else [payload] if payload else []:
            line = f"[rpc] {scope.get('method')} {m.get('method')}"
            params = m.get("params") or {}
            if m.get("method") == "initialize":
                caps = params.get("capabilities") or {}
                line += (f" client={params.get('clientInfo')} protocol={params.get('protocolVersion')}"
                         f" extensions={json.dumps(caps.get('extensions'))}")
            elif m.get("method") in ("tools/call", "resources/read"):
                line += f" {params.get('name') or params.get('uri')}"
            print(line, flush=True)
    except Exception as e:  # never let logging break a request
        print(f"[rpc] unparsable body ({e})", flush=True)

    sent = False

    async def replay():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


def build_http_app():
    """Starlette app serving Streamable HTTP at /mcp, legacy SSE at /sse, and /health.

    Also the ASGI app Vercel runs (see api/index.py). /sse needs a long-lived
    process, so it only works on a regular server, not on Vercel.
    """
    from mcp.server.sse import SseServerTransport
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import FileResponse, JSONResponse, Response
    from starlette.routing import Mount, Route

    sse = SseServerTransport("/messages/")

    class StreamableHTTPEndpoint:
        # An ASGI app (not a function), so Starlette hands it the raw request.
        # Stateless: a fresh session manager per request, so nothing depends on
        # ASGI lifespan events, which serverless hosts may not send.
        async def __call__(self, scope, receive, send):
            if os.environ.get("GW_LOG_RPC", "").lower() in ("1", "true", "yes"):
                receive = await _log_rpc(scope, receive)
            manager = StreamableHTTPSessionManager(app=app, stateless=True)
            async with manager.run():
                await manager.handle_request(scope, receive, send)

    async def handle_sse(request: Request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
            await app.run(r, w, app.create_initialization_options())
        return Response()

    async def health(request: Request):
        from state_store import PersistentMockClient

        state = "redis" if isinstance(get_client(), PersistentMockClient) else "memory" if is_mock() else None
        return JSONResponse({"status": "ok", "server": "gwmcp", "mock": is_mock(), "state": state})

    async def preview(request: Request):
        # Local stand-in for Claude that renders the policy view; mock data only.
        if not is_mock():
            return JSONResponse({"error": "The preview host is only served in mock mode."}, status_code=404)
        return FileResponse(HERE / "dev" / "preview_host.html")

    return Starlette(
        routes=[
            Route("/health", endpoint=health),
            Route("/mcp", endpoint=StreamableHTTPEndpoint(), methods=["GET", "POST", "DELETE"]),
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
            Route("/preview", endpoint=preview),
        ],
    )


def _run_http() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    print(f"gwmcp listening on port {port}: Streamable HTTP at /mcp, legacy SSE at /sse"
          + (", preview at /preview (mock mode)" if is_mock() else ""))
    uvicorn.run(build_http_app(), host="0.0.0.0", port=port)


def main() -> None:
    transport = os.environ.get("GW_TRANSPORT", "stdio").lower()
    if transport in ("http", "sse"):
        _run_http()
    else:
        asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
