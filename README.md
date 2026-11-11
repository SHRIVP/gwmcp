# Everywire Policy System MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that connects Claude to Everywire Policy System. Claude can search policies, accounts, and jobs, edit policies through policy changes, and write new business: create an account, then quote and bind a new Personal Auto or Homeowners policy. An interactive **Policy System view** (an [MCP App](https://claude.com/docs/connectors/building/mcp-apps/getting-started)) opens beside the chat and shows each change as Claude makes it.

> **Status:** policy editing and the view currently run against the built-in **mock** data (`GW_MOCK=true`). Mapping policy changes onto a live tenant's Cloud API, and OAuth sign-in for the connector, are the next steps. Don't expose this server with real credentials until OAuth is in place.

## Tools

| Tool | Description |
|---|---|
| `search_policies` | Search by policy number, account, status, date range, or product |
| `search_accounts` | Search personal and commercial accounts by name, number, or email |
| `search_jobs` | Search policy transactions — submissions, renewals, endorsements, cancellations |
| `open_policy` | Open a policy in the Policy System view: vehicles, drivers, coverages, premium, open change |
| `start_policy_change` | Start a policy change (endorsement) with an effective date |
| `add_vehicle` / `remove_vehicle` | Edit vehicles on the open change (Personal Auto) |
| `add_driver` | Add a driver to the open change (Personal Auto). Drivers under 25 carry a surcharge |
| `create_account` | Create a new customer account (person or company) with a mailing address |
| `start_submission` | Start a new-business submission for an account: Personal Auto or Homeowners |
| `set_home_details` | Homeowners: property address (checked against Census and FEMA), year built, construction, size, roof, stories |
| `update_coverage` | Change a limit or deductible to one of its allowed values |
| `quote_policy_change` | Quote the change or submission: annual premium (and prorated change in cost for a change) |
| `withdraw_policy_change` | Discard the open change or submission |
| `bind_policy_change` | **View only.** Claude can't call it. The user binds with the view's Bind button |

### How editing works

1. Claude calls `start_policy_change`, then edit tools. Each edit lands in a **draft** policy change; the live policy is untouched.
2. Every edit tool returns the updated policy, and the view renders it, with added vehicles and drivers and changed coverages highlighted.
3. Claude calls `quote_policy_change`. Any later edit sends the change back to Draft, so it has to be re-quoted.
4. **You** click **Bind change**, then confirm. The view calls `bind_policy_change` and tells Claude what you did.

When the view is open in the side panel, it refreshes every few seconds (it shows a **Live** indicator).

### How new business works

1. Claude calls `create_account` for a new customer (or uses an existing account), then `start_submission` with the product and effective date. The submission gets its policy number right away and keeps it when bound.
2. Claude builds it with the same edit tools. Auto needs at least one vehicle and one driver. Homeowners needs `set_home_details` with an address, year built, construction type and square footage.
3. For homeowners, the server looks the address up in free public data, with no API keys:
   - **US Census Geocoder:** confirms the address and returns its state and county.
   - **FEMA National Flood Hazard Layer:** returns the flood zone.
4. `quote_policy_change` prices it. Homeowners premiums multiply the base premium by factors for home age, construction, size, roof age, stories, flood zone and state. The quote lists each factor, so you can see why the price came out where it did.
5. **You** click **Bind new policy** in the view. The policy goes in force, or is scheduled if its effective date is in the future.

If a lookup is slow (5 second limit) or down, the quote still works: that factor counts as neutral, and the lookup is retried on the next home-details update or quote. An address the Census can't match is shown as "address not verified".

> Pricing is demo pricing, not filed rates.

## Setup

### 1. Install dependencies

```bash
pip install -e .
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your Everywire credentials:

```env
GW_BASE_URL=https://{tenant}.everywire.net/pc/rest
GW_TOKEN_URL=https://{your-idp}/oauth2/token
GW_CLIENT_ID=your-client-id
GW_CLIENT_SECRET=your-client-secret
```

### 3. Run locally (stdio)

```bash
python server.py
```

### 4. Run the HTTP server and preview the UI

```bash
GW_MOCK=true GW_TRANSPORT=http python server.py
```

- MCP endpoint: `http://localhost:8000/mcp` (Streamable HTTP), plus legacy SSE at `/sse`
- UI preview: open `http://localhost:8000/preview`. It's a small stand-in for Claude that calls the tools and renders the Policy System view, mock mode only

### 5. Run tests

```bash
pip install -e '.[dev]'
pytest
```

### Test with MCP Inspector

```bash
mcp dev server.py
```

## Mock Mode

No Everywire credentials? Use built-in mock data:

```bash
GW_MOCK=true mcp dev server.py
```

Sample data includes policies (`PA-200001-01`, `HO-200002-01`, `CP-200004-01`), accounts (`ACC-10001` Jane Smith, `ACC-10003` Acme Corporation), and jobs of various types and statuses.

## Connect to Claude

### Claude.ai / Claude Desktop as a custom connector (recommended)

Claude connects from Anthropic's servers, so the server needs a public HTTPS URL: deploy it (e.g. Railway, below), or use a tunnel while testing.

1. Go to **Settings → Connectors → Add custom connector**
2. Enter the server URL, ending in **`/mcp`**:
   ```
   https://your-deployment.up.railway.app/mcp
   ```
3. Enable the connector in a chat, then try:
   - *"Open policy PA-200001-01 in Policy System"*
   - *"Add a 2024 Honda Civic, VIN 2HGFE1F30RH000099, to that policy from Dec 1, and raise the collision deductible to $1,000"*
   - *"Quote it"*, then click **Bind change** in the view
   - *"New customer Maria Lopez, maria@example.com, 12 Oak St, Springfield IL 62704. Quote home insurance from Dec 1: built 1998, brick, 2,100 sq ft, roof replaced 2019"*, then click **Bind new policy**

The view shows in the conversation; click **Expand**, or let it open on its own, to keep it in the side panel. The side panel and the newer interactive features are rolling out to some organizations first and need the unified Chat and Cowork experience. Without them, the view still renders inline in the chat.

### Claude Desktop (Mac/Windows app)

Add it as a custom connector the same way as above. Connectors are shared between claude.ai and the desktop app, and the desktop app renders the view too.

### Claude Code (CLI)

Claude Code uses the tools as text; it doesn't render the view.

```bash
claude mcp add --transport http gwmcp https://your-deployment.up.railway.app/mcp
```

## Deploy to Vercel

`vercel_app.py` exposes the server's web app to Vercel's Python runtime (set as `[tool.vercel] entrypoint` in `pyproject.toml`), and Vercel routes every path to it, so `/mcp`, `/health` and `/preview` keep their URLs. Legacy `/sse` needs a long-lived process and doesn't work on Vercel. Claude connectors use `/mcp`.

Serverless requests can land on fresh processes, so in mock mode, edits are kept in **Upstash Redis** (from the Vercel Marketplace). Without Redis, mock edits live only in process memory.

```bash
npm i -g vercel
vercel login
vercel link                       # create/link the project
# Vercel dashboard → project → Storage → Upstash for Redis → Connect
#   (sets KV_REST_API_URL / KV_REST_API_TOKEN for the project)
vercel env add GW_MOCK production         # true
vercel env add GW_PUBLIC_URL production   # https://<your-domain>/mcp
vercel --prod
vercel domains add <your-domain>          # optional custom domain, e.g. gwmcp.example.com
curl https://<your-domain>/health         # → "state": "redis"
```

To reset the demo data, delete the `gwmcp:mock:state` and `gwmcp:mock:state:rev` keys in the Upstash console.

## Deploy to Railway

```bash
# Install Railway CLI
brew install railway

# Login and deploy
railway login
railway init
# Mock data (safe for a demo; no auth yet):
railway variables set GW_TRANSPORT=http GW_MOCK=true
# Real tenant (search only for now — add OAuth before exposing):
# railway variables set GW_TRANSPORT=http GW_BASE_URL=... GW_TOKEN_URL=... GW_CLIENT_ID=... GW_CLIENT_SECRET=...
railway up
railway domain
# then, so the view gets a stable sandbox origin in Claude:
railway variables set GW_PUBLIC_URL=https://<your-domain>/mcp
```

The `railway.toml` is pre-configured. Railway sets the `PORT` automatically.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GW_BASE_URL` | Yes* | Policy System REST base URL (e.g. `https://tenant.everywire.net/pc/rest`) |
| `GW_TOKEN_URL` | Yes* | OAuth 2.0 token endpoint |
| `GW_CLIENT_ID` | Yes* | OAuth client ID |
| `GW_CLIENT_SECRET` | Yes* | OAuth client secret |
| `GW_OAUTH_SCOPE` | No | Space-separated OAuth scopes (e.g. `pc:read`) |
| `GW_TRANSPORT` | No | `stdio` (default) or `http` for remote deployment (`/mcp` + legacy `/sse`; `sse` is an alias) |
| `GW_MOCK` | No | Set to `true` to use mock data without real credentials. Required for policy editing for now |
| `GW_PUBLIC_URL` | No | Exact public URL added to Claude (e.g. `https://x.up.railway.app/mcp`); sets the view's `ui.domain` |
| `PORT` | No | Port for the HTTP transport (default `8000`, auto-set by Railway) |

*Not required when `GW_MOCK=true`

## Project Structure

```
gwmcp/
├── server.py       # MCP server — tools, UI resource, stdio/HTTP transports
├── client.py       # Everywire HTTP client with OAuth2 token caching
├── mock.py         # Stateful mock: sample data, policy changes, new business, rating
├── property_data.py # Census geocoder + FEMA flood zone lookups for homeowners quotes
├── views.py        # Policy view model shared by the UI and Claude's text
├── state_store.py  # Redis (Upstash) persistence for mock state on serverless
├── vercel_app.py   # Vercel entry point ([tool.vercel] in pyproject.toml)
├── ui/policy_view.html   # The Policy System view (MCP App)
├── dev/preview_host.html # Local stand-in for Claude to preview the view
├── tests/          # pytest: mock workflow + end-to-end over HTTP
├── pyproject.toml  # Package config and dependencies
├── railway.toml    # Railway deployment config
└── .env.example    # Environment variable template
```
