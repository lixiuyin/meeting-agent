# MCP Server

`backend/src/mcp.py` exposes Meeting Agent tools through FastMCP. It is a thin
client of the canonical HTTP API: it does not initialize migrations, open
SQLite/Chroma, or import domain services.

## Start

Start the backend on port 7008 first, then run from `backend/`:

```bash
uv run python -m src.mcp
```

Defaults:

```dotenv
MCP_API_URL=http://127.0.0.1:7008/api/v1
MCP_API_KEY=
MCP_TRANSPORT=stdio
MCP_HOST=127.0.0.1
MCP_HTTP_PORT=9000
```

`MCP_API_KEY` overrides `API_KEY` for calls from MCP. If it is empty, MCP uses
`API_KEY`. In development both may be empty if the API itself allows dev access.

Claude Desktop example:

```json
{
  "mcpServers": {
    "meeting-agent": {
      "command": "uv",
      "args": ["run", "python", "-m", "src.mcp"],
      "cwd": "/absolute/path/to/meeting-agent/backend",
      "env": {
        "MCP_API_URL": "http://127.0.0.1:7008/api/v1",
        "MCP_API_KEY": "replace-me"
      }
    }
  }
}
```

## Tools and API mapping

| MCP tool | HTTP API |
|---|---|
| `list_meetings` | `GET /meetings` |
| `search_meetings` | `POST /chat/search` |
| `ask_about_meetings` | `POST /chat` |
| `manage_memory` | `/memory`, `/memory/search`, `/memory/decay`, `/memory/entities/merge` |
| `list_skills` | `GET /skills` |
| `invoke_skill` | `POST /skills/invoke` |

The API credential determines the principal. The legacy `user_id` tool
argument is retained for client compatibility but is ignored.

## Boundary rationale

- Authorization, idempotency, validation and error mapping remain identical to
  the frontend path.
- Only the API process/worker owns storage and migrations.
- Running MCP and the API concurrently cannot create a second independent
  database/vector writer.
- API failures are returned as readable MCP tool errors instead of bypassing
  the API with a local fallback.

## HTTP / SSE transport

```bash
MCP_TRANSPORT=streamable-http MCP_HTTP_PORT=9000 \
  MCP_API_KEY=replace-me uv run python -m src.mcp
```

`MCP_HTTP_PORT` by itself remains a compatibility shortcut for
`MCP_TRANSPORT=streamable-http`. `MCP_TRANSPORT=sse` selects legacy SSE.

## Security

Stdio trusts the local host process. HTTP/SSE is intentionally restricted to
`127.0.0.1` because the MCP endpoint has no inbound authentication. It also
requires `MCP_API_KEY` (or `API_KEY`) for downstream API calls. For remote
clients, keep MCP bound to loopback and place an authenticated same-host reverse
proxy in front of it; never publish the MCP port directly.

## Diagnostics

```bash
curl -fsS http://127.0.0.1:7008/api/v1/health/ready
npx @modelcontextprotocol/inspector uv run python -m src.mcp
```

If a tool reports an API connection error, verify `MCP_API_URL`, the backend
health endpoint and `MCP_API_KEY`. Database locks in the MCP process are no
longer possible because MCP does not open the database.
