# eaZion-memory-mcp — Claude Memory MCP Server
<img width="1024" height="285" alt="image" src="https://github.com/user-attachments/assets/7f4453e1-2126-4d00-b23e-cfbcd01c089c" />


A global MCP server that stores Claude's memories in PostgreSQL.  
<br>
Replaces the file-based memory system (`~/.claude/projects/.../memory/`) with a SQL store supporting full-text search and tag filtering.

---

## Directory layout

```
~/.claude/mcp/              ← shared directory for all custom MCP servers
└── eazion-memory-mcp/
    ├── server.py            # FastMCP server, 9 tools
    ├── requirements.txt     # mcp, psycopg2-binary
    ├── .venv/               # Python 3.12 (uv)
    ├── .gitignore
    └── README.md
```

---

## Database

The server requires a reachable PostgreSQL instance. Setting it up is **outside the scope of this MCP** — see [Quick PostgreSQL setup](#quick-postgresql-setup) if you need one.

### Connection & fallback

The server reads two environment variables:

| Variable | Purpose |
|---|---|
| `EAZION_DATABASE_URL` | Primary connection — direct network access to the PostgreSQL host |
| `EAZION_DATABASE_URL_LOOPBACK_SSH_TUNNEL` | Fallback — for when the database is behind a firewall and you tunnel via SSH (`localhost`) |

**Fallback logic:** on every connection attempt the server tries `EAZION_DATABASE_URL` first. If it gets an `OperationalError` (host unreachable, refused, etc.) **and** `EAZION_DATABASE_URL_LOOPBACK_SSH_TUNNEL` is set, it transparently retries through the tunnel. At least one variable must be set or the server fails to start.

#### Setting up an SSH tunnel (when needed)

```bash
# Forward remote port 5432 → localhost:5432
ssh -N -L 5432:localhost:5432 user@remote-host
```

Then set:
```
EAZION_DATABASE_URL_LOOPBACK_SSH_TUNNEL=postgresql://postgres:password@localhost:5432/claude_memory
```

### Table schema

```sql
CREATE TABLE memories (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ('user','feedback','project','reference')),
    description TEXT NOT NULL,
    body        TEXT NOT NULL,
    tags        TEXT[] DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX memories_trgm_idx ON memories
    USING GIN((name || ' ' || description || ' ' || body) gin_trgm_ops);
CREATE INDEX memories_tags_idx ON memories USING GIN(tags);
```

A trigger keeps `updated_at` current on every update.

---

## Naming convention

```
GLOBAL/{name}                 — global memories (written via eazion_memory_save)
{machine}/{dialog}/{name}     — session insights (written via eazion_memory_save_session_insights)
```

Examples:
```
GLOBAL/server-222
GLOBAL/memory-system
thinkpad/psql/pulumi-python-setup
thinkpad/psql/uv-python-install
```

---

## Tools (9 total)

### Global memories

| Tool | Description |
|---|---|
| `eazion_memory_save(name, type, description, body, tags?)` | Upsert as `GLOBAL/{name}` |
| `eazion_memory_get(name)` | Fetch by exact full name |
| `eazion_memory_delete(name)` | Delete by exact full name |
| `eazion_memory_search(query, tags?)` | ILIKE search across name + description + body; optional tag filter |
| `eazion_memory_list(type?, namespace?, tags?)` | Index listing; filter by type, namespace prefix, and/or tags |
| `eazion_memory_load_by_tag(tags)` | Full content of all records matching any of the given tags |

### Session insights

| Tool | Description |
|---|---|
| `eazion_memory_save_session_insights(dialog, insights)` | Batch-save insights as `{hostname}/{dialog}/{name}` |
| `eazion_memory_list_session_insights(dialog?, machine?, tags?)` | Index listing of session insights; all params optional |
| `eazion_memory_load_session_insights(dialog, machine?, tags?)` | Full content of insights for a given dialog |

### Tag behaviour

All tag filters use the PostgreSQL `&&` (array overlap) operator — a record matches if it has **any** of the specified tags.

### Memory types

- `user` — user profile: role, preferences, working style
- `feedback` — guidance: what to do / avoid, and why
- `project` — project context: goals, deadlines, decisions
- `reference` — pointers to external resources: servers, APIs, dashboards

### Insight schema

```python
class Insight(BaseModel):
    name: str          # short slug, e.g. "pulumi-sudo-workaround"
    type: str          # user | feedback | project | reference
    description: str   # one-liner used as an index hint
    body: str          # full content (markdown)
    tags: list[str]    # e.g. ["pulumi", "ssh", "sudo"]
```

---

## Dependencies

- **Python 3.12** — installed via `uv` (system Python 3.8 does not support the `mcp` package)
- **uv** — Python installer and package manager, no root required
- **Packages:** `mcp` (FastMCP), `psycopg2-binary`, `pydantic`

### Why uv instead of apt/pip

On Ubuntu 20.04 the system Python is 3.8; `mcp` requires 3.10+.  
apt needs a PPA for newer versions, and `sudo` is not available from a Claude session.  
`uv python install 3.12` places Python in `~/.local/share/uv/` with no root.

The system `pip list --format json` also crashes due to a non-standard `python-apt` version (`2.0.1-elementary11-*`), which breaks Pulumi and other tooling. A uv venv bypasses this entirely.

---

## Registration in Claude Code

```bash
claude mcp add eazion-memory-mcp \
  -s user \
  -e "EAZION_DATABASE_URL=postgresql://user:password@host:5432/claude_memory" \
  -e "EAZION_DATABASE_URL_LOOPBACK_SSH_TUNNEL=postgresql://user:password@localhost:5432/claude_memory" \
  -- \
  ~/.claude/mcp/eazion-memory-mcp/.venv/bin/python \
  ~/.claude/mcp/eazion-memory-mcp/server.py
```

- `-s user` — available globally across all sessions
- Config is stored in `~/.claude.json`
- `mcpServers` in `settings.json` **does not work** — it is not a valid field
- `~/.claude/.mcp.json` **is not picked up** automatically

```bash
claude mcp list   # check connection status
```

---

## Getting started

### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$PATH:$HOME/.local/bin"
```

### 2. Create the venv

```bash
cd ~/.claude/mcp/eazion-memory-mcp
uv python install 3.12
uv venv .venv --python 3.12
uv pip install --python .venv mcp psycopg2-binary
```

### 3. Create the database schema

Connect to your PostgreSQL instance and run:

```sql
CREATE DATABASE claude_memory;

\c claude_memory

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE memories (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('user','feedback','project','reference')),
    description TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX memories_trgm_idx
    ON memories USING GIN((name || ' ' || description || ' ' || body) gin_trgm_ops);
CREATE INDEX memories_tags_idx ON memories USING GIN(tags);

CREATE OR REPLACE FUNCTION memories_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;

CREATE TRIGGER memories_updated_at
    BEFORE UPDATE ON memories
    FOR EACH ROW EXECUTE FUNCTION memories_set_updated_at();
```

### 4. Register the server

```bash
claude mcp add eazion-memory-mcp \
  -s user \
  -e "EAZION_DATABASE_URL=postgresql://user:password@host:5432/claude_memory" \
  -e "EAZION_DATABASE_URL_LOOPBACK_SSH_TUNNEL=postgresql://user:password@localhost:5432/claude_memory" \
  -- ~/.claude/mcp/eazion-memory-mcp/.venv/bin/python ~/.claude/mcp/eazion-memory-mcp/server.py
```

---

## Quick PostgreSQL setup

> This MCP only requires a reachable PostgreSQL instance and `EAZION_DATABASE_URL`.  
> If you don't have one, here is the fastest way to spin one up with Docker.

```bash
# Create a persistent data directory
mkdir -p /opt/pgdata && chown 999:999 /opt/pgdata

# Run PostgreSQL 17
docker run -d \
  --name postgres \
  --restart unless-stopped \
  -e POSTGRES_PASSWORD=yourpassword \
  -e PGDATA=/var/lib/postgresql/data/pgdata \
  -v /opt/pgdata:/var/lib/postgresql/data \
  -p 5432:5432 \
  postgres:17-alpine

# Wait until ready
until docker exec postgres pg_isready -U postgres; do sleep 1; done
```

Then set:
```
EAZION_DATABASE_URL=postgresql://postgres:yourpassword@<host>:5432/claude_memory
```

If the host is only reachable via SSH tunnel:
```bash
ssh -N -L 5432:localhost:5432 user@remote-host
```
```
EAZION_DATABASE_URL_LOOPBACK_SSH_TUNNEL=postgresql://postgres:yourpassword@localhost:5432/claude_memory
```

---

## Debugging

```bash
# Test primary connection
EAZION_DATABASE_URL="postgresql://..." \
  ~/.claude/mcp/eazion-memory-mcp/.venv/bin/python -c \
  "import psycopg2, os; psycopg2.connect(os.environ['EAZION_DATABASE_URL']); print('OK')"

# Test tunnel fallback
EAZION_DATABASE_URL_LOOPBACK_SSH_TUNNEL="postgresql://..." \
  ~/.claude/mcp/eazion-memory-mcp/.venv/bin/python -c \
  "import psycopg2, os; psycopg2.connect(os.environ['EAZION_DATABASE_URL_LOOPBACK_SSH_TUNNEL']); print('OK')"

# List registered tools
EAZION_DATABASE_URL="..." \
  ~/.claude/mcp/eazion-memory-mcp/.venv/bin/python -c "
import sys; sys.path.insert(0, '.')
import server
print([t.name for t in server.mcp._tool_manager.list_tools()])
"

# Interactive testing via MCP Inspector
npx @modelcontextprotocol/inspector \
  ~/.claude/mcp/eazion-memory-mcp/.venv/bin/python \
  ~/.claude/mcp/eazion-memory-mcp/server.py
```

### Known gotchas

- Literal `%` inside f-string SQL fragments must be escaped as `%%` — psycopg2 treats bare `%` as a placeholder and raises `IndexError: list index out of range`
- `EAZION_DATABASE_URL` is passed via `env` in `~/.claude.json`; bash variable names cannot start with a digit
