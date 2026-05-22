import os
import socket
from typing import Optional
import psycopg2
import psycopg2.extras
from pydantic import BaseModel
from mcp.server.fastmcp import FastMCP

DB_URL = os.environ["DATABASE_URL_222"]
HOSTNAME = socket.gethostname()

mcp = FastMCP("psql-memory")


def conn():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def _upsert(cur, name: str, type: str, description: str, body: str, tags: list[str]) -> dict:
    cur.execute(
        """
        INSERT INTO memories (name, type, description, body, tags)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE
            SET type        = EXCLUDED.type,
                description = EXCLUDED.description,
                body        = EXCLUDED.body,
                tags        = EXCLUDED.tags,
                updated_at  = NOW()
        RETURNING id, (xmax = 0) AS inserted
        """,
        (name, type, description, body, tags),
    )
    return cur.fetchone()


def _fmt(r: dict) -> str:
    tags_line = f"\n_tags: {', '.join(r['tags'])}_" if r.get("tags") else ""
    return (
        f"## [{r['type']}] {r['name']}{tags_line}\n"
        f"_{r['description']}_\n\n"
        f"{r['body']}"
    )


def _tag_condition(tags: Optional[list[str]]) -> tuple[str, list]:
    """Returns (sql_condition, params) for tag filtering using && (any match)."""
    if tags:
        return "tags && %s::text[]", [tags]
    return "", []


@mcp.tool()
def psql_memory_save(name: str, type: str, description: str, body: str,
                     tags: Optional[list[str]] = None) -> str:
    """
    Save or update a global memory. Stored as GLOBAL/{name}.
    type: one of user | feedback | project | reference
    description: one-liner, used as index hint in searches
    body: full memory content (markdown)
    tags: optional list of tags for filtering, e.g. ["server", "ssh", "222"]
    """
    full_name = f"GLOBAL/{name}"
    with conn() as c, c.cursor() as cur:
        row = _upsert(cur, full_name, type, description, body, tags or [])
    action = "created" if row["inserted"] else "updated"
    return f"Memory '{full_name}' {action} (id={row['id']})"


class Insight(BaseModel):
    name: str
    type: str
    description: str
    body: str
    tags: list[str] = []


@mcp.tool()
def psql_memory_save_session_insights(dialog: str, insights: list[Insight]) -> str:
    """
    Save insights from the current session. Stored as {hostname}/{dialog}/{name}.
    Call at the end of a session with a list of insights extracted from the conversation.
    type: one of user | feedback | project | reference
    tags: optional list of tags per insight
    """
    prefix = f"{HOSTNAME}/{dialog}"
    created, updated = 0, 0
    with conn() as c, c.cursor() as cur:
        for insight in insights:
            full_name = f"{prefix}/{insight.name}"
            row = _upsert(cur, full_name, insight.type, insight.description, insight.body, insight.tags)
            if row["inserted"]:
                created += 1
            else:
                updated += 1
    return (
        f"Saved {len(insights)} insights under '{prefix}/'\n"
        f"  created: {created}, updated: {updated}\n"
        f"  names: {', '.join(f'{prefix}/{i.name}' for i in insights)}"
    )


@mcp.tool()
def psql_memory_search(query: str, tags: Optional[list[str]] = None) -> str:
    """
    Search memories by free text across name, description and body.
    tags: optional list — returns only memories that have ANY of these tags.
    """
    with conn() as c, c.cursor() as cur:
        like = f"%{query}%"
        conditions = ["(name ILIKE %s OR description ILIKE %s OR body ILIKE %s)"]
        params: list = [like, like, like]
        tag_cond, tag_params = _tag_condition(tags)
        if tag_cond:
            conditions.append(tag_cond)
            params.extend(tag_params)
        where = "WHERE " + " AND ".join(conditions)
        cur.execute(
            f"SELECT name, type, description, body, tags, updated_at FROM memories {where} ORDER BY updated_at DESC",
            params,
        )
        rows = cur.fetchall()
    if not rows:
        return f"No memories found for query: {query!r}"
    return "\n\n---\n\n".join(_fmt(r) for r in rows)


@mcp.tool()
def psql_memory_load_by_tag(tags: list[str]) -> str:
    """
    Load all memories that have ANY of the given tags (full content).
    tags: list of tags to match, e.g. ["server", "mcp"]
    """
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT name, type, description, body, tags FROM memories WHERE tags && %s::text[] ORDER BY name",
            [tags],
        )
        rows = cur.fetchall()
    if not rows:
        return f"No memories found with tags: {tags}"
    return f"**{len(rows)} memories**\n\n" + "\n\n---\n\n".join(_fmt(r) for r in rows)


@mcp.tool()
def psql_memory_get(name: str) -> str:
    """Get a single memory by exact full name (e.g. GLOBAL/server-222)."""
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT type, description, body, tags, updated_at FROM memories WHERE name = %s",
            (name,),
        )
        row = cur.fetchone()
    if not row:
        return f"Memory '{name}' not found."
    tags_line = f"\n_tags: {', '.join(row['tags'])}_" if row["tags"] else ""
    return (
        f"## [{row['type']}] {name}{tags_line}\n"
        f"_{row['description']}_\n\n"
        f"{row['body']}\n\n"
        f"_updated: {row['updated_at']}_"
    )


@mcp.tool()
def psql_memory_list(type: Optional[str] = None, namespace: Optional[str] = None,
                     tags: Optional[list[str]] = None) -> str:
    """
    List all memories as an index (name + description + tags, no body).
    type:      optional filter: user | feedback | project | reference
    namespace: optional prefix filter, e.g. 'GLOBAL' or 'thinkpad/psql'
    tags:      optional list — returns only memories that have ANY of these tags
    """
    with conn() as c, c.cursor() as cur:
        conditions, params = [], []
        if type:
            conditions.append("type = %s")
            params.append(type)
        if namespace:
            conditions.append("name LIKE %s")
            params.append(f"{namespace}/%")
        tag_cond, tag_params = _tag_condition(tags)
        if tag_cond:
            conditions.append(tag_cond)
            params.extend(tag_params)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        cur.execute(
            f"SELECT name, type, description, tags FROM memories {where} ORDER BY name",
            params,
        )
        rows = cur.fetchall()
    if not rows:
        return "No memories stored yet."
    lines = []
    for r in rows:
        tag_str = f" `[{', '.join(r['tags'])}]`" if r["tags"] else ""
        lines.append(f"- [{r['type']}] **{r['name']}**{tag_str} — {r['description']}")
    return f"**{len(rows)} memories**\n\n" + "\n".join(lines)


@mcp.tool()
def psql_memory_list_session_insights(dialog: Optional[str] = None,
                                       machine: Optional[str] = None,
                                       tags: Optional[list[str]] = None) -> str:
    """
    List session insights (name + description + tags, no body). Excludes GLOBAL/*.
    dialog:  optional filter, e.g. 'psql'
    machine: optional filter, e.g. 'thinkpad'. If empty — all machines.
    tags:    optional list — returns only insights that have ANY of these tags
    """
    with conn() as c, c.cursor() as cur:
        conditions = ["name NOT LIKE 'GLOBAL/%%'"]
        params: list = []
        if machine:
            conditions.append("name LIKE %s")
            params.append(f"{machine}/%")
        if dialog:
            conditions.append("name LIKE %s")
            params.append(f"%/{dialog}/%")
        tag_cond, tag_params = _tag_condition(tags)
        if tag_cond:
            conditions.append(tag_cond)
            params.extend(tag_params)
        where = "WHERE " + " AND ".join(conditions)
        cur.execute(
            f"SELECT name, type, description, tags FROM memories {where} ORDER BY name",
            params,
        )
        rows = cur.fetchall()
    if not rows:
        return "No session insights found."
    lines = []
    for r in rows:
        tag_str = f" `[{', '.join(r['tags'])}]`" if r["tags"] else ""
        lines.append(f"- [{r['type']}] **{r['name']}**{tag_str} — {r['description']}")
    return f"**{len(rows)} session insights**\n\n" + "\n".join(lines)


@mcp.tool()
def psql_memory_load_session_insights(dialog: str, machine: Optional[str] = None,
                                       tags: Optional[list[str]] = None) -> str:
    """
    Load full content of all insights for a given dialog.
    dialog:  required, e.g. 'psql'
    machine: optional filter, e.g. 'thinkpad'. If empty — all machines.
    tags:    optional list — returns only insights that have ANY of these tags
    """
    with conn() as c, c.cursor() as cur:
        conditions = ["name NOT LIKE 'GLOBAL/%%'", "name LIKE %s"]
        params: list = [f"%/{dialog}/%"]
        if machine:
            conditions.append("name LIKE %s")
            params.append(f"{machine}/%")
        tag_cond, tag_params = _tag_condition(tags)
        if tag_cond:
            conditions.append(tag_cond)
            params.extend(tag_params)
        where = "WHERE " + " AND ".join(conditions)
        cur.execute(
            f"SELECT name, type, description, body, tags FROM memories {where} ORDER BY name",
            params,
        )
        rows = cur.fetchall()
    if not rows:
        suffix = f", machine={machine!r}" if machine else ""
        suffix += f", tags={tags}" if tags else ""
        return f"No session insights found for dialog={dialog!r}{suffix}."
    return f"**{len(rows)} insights**\n\n" + "\n\n---\n\n".join(_fmt(r) for r in rows)


@mcp.tool()
def psql_memory_delete(name: str) -> str:
    """Delete a memory by exact full name (e.g. GLOBAL/server-222)."""
    with conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM memories WHERE name = %s RETURNING id", (name,))
        row = cur.fetchone()
    if not row:
        return f"Memory '{name}' not found — nothing deleted."
    return f"Memory '{name}' deleted."


if __name__ == "__main__":
    mcp.run()
