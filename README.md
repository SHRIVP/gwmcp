# Guidewire PolicyCenter MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that connects Claude to Guidewire PolicyCenter, enabling natural language search across policies, accounts, and jobs.

## Tools

| Tool | Description |
|---|---|
| `search_policies` | Search by policy number, account, status, date range, or product |
| `search_accounts` | Search personal and commercial accounts by name, number, or email |
| `search_jobs` | Search policy transactions — submissions, renewals, endorsements, cancellations |

## Setup

### 1. Install dependencies

```bash
pip install -e .
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your Guidewire credentials:

```env
GW_BASE_URL=https://{tenant}.guidewire.net/pc/rest
GW_TOKEN_URL=https://{your-idp}/oauth2/token
GW_CLIENT_ID=your-client-id
GW_CLIENT_SECRET=your-client-secret
```

### 3. Run locally (stdio)

```bash
python server.py
```

### 3. Test with MCP Inspector

```bash
mcp dev server.py
```

## Mock Mode

No Guidewire credentials? Use built-in mock data:

```bash
GW_MOCK=true mcp dev server.py
```

Sample data includes policies (`PA-200001-01`, `HO-200002-01`, `CP-200004-01`), accounts (`ACC-10001` Jane Smith, `ACC-10003` Acme Corporation), and jobs of various types and statuses.

## Connect to Claude

### Claude.ai (Web Chat) — recommended for most users

This requires the server to be deployed remotely (e.g. Railway) so Claude.ai can reach it.

1. Go to **claude.ai → Settings → Integrations**
2. Click **Add Integration**
3. Enter the server URL:
   ```
   https://your-deployment.up.railway.app/sse
   ```
4. Click **Save**

The integration will appear in your Claude.ai chat. Enable it per-conversation using the tools icon in the chat bar, then ask things like:
- *"Search for all inForce personal auto policies"*
- *"Find accounts with last name Smith"*
- *"Show me all renewal jobs for policy PA-200001-01"*

### Claude Desktop (Mac/Windows app)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "gwmcp": {
      "url": "https://your-deployment.up.railway.app/sse"
    }
  }
}
```

Restart Claude Desktop after saving.

### Claude Code (CLI)

```bash
claude mcp add --transport sse gwmcp https://your-deployment.up.railway.app/sse
```

## Deploy to Railway

```bash
# Install Railway CLI
brew install railway

# Login and deploy
railway login
railway init
railway variables set GW_TRANSPORT=sse GW_BASE_URL=... GW_TOKEN_URL=... GW_CLIENT_ID=... GW_CLIENT_SECRET=...
railway up
railway domain
```

The `railway.toml` is pre-configured. Railway sets the `PORT` automatically.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GW_BASE_URL` | Yes* | PolicyCenter REST base URL (e.g. `https://tenant.guidewire.net/pc/rest`) |
| `GW_TOKEN_URL` | Yes* | OAuth 2.0 token endpoint |
| `GW_CLIENT_ID` | Yes* | OAuth client ID |
| `GW_CLIENT_SECRET` | Yes* | OAuth client secret |
| `GW_OAUTH_SCOPE` | No | Space-separated OAuth scopes (e.g. `pc:read`) |
| `GW_TRANSPORT` | No | `stdio` (default) or `sse` for remote deployment |
| `GW_MOCK` | No | Set to `true` to use mock data without real credentials |
| `PORT` | No | Port for SSE transport (default `8000`, auto-set by Railway) |

*Not required when `GW_MOCK=true`

## Project Structure

```
gwmcp/
├── server.py       # MCP server — tool definitions and handlers
├── client.py       # Guidewire HTTP client with OAuth2 token caching
├── mock.py         # Mock client with sample insurance data
├── pyproject.toml  # Package config and dependencies
├── railway.toml    # Railway deployment config
└── .env.example    # Environment variable template
```
