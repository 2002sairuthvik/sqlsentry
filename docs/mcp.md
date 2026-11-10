# MCP server: safe database access for AI agents

`sqlsentry-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server. It lets AI
assistants and agents (Claude Desktop, Claude Code, Cursor, VS Code, or any MCP client) query your
databases, and **every query goes through sqlsentry's guard and policy**: one read-only `SELECT`,
hidden tables and columns invisible and unqueryable, a row cap and a timeout. Agents can explore and
query; they can never write, drop, or read what the policy hides.

```bash
pip install "sqlsentry[mcp]"
```

## Two ways agents can use it

| | The agent writes SQL | The agent delegates |
|---|---|---|
| Tools | `describe_schema`, then `run_sql` / `validate_sql` | `generate_sql`, `ask` |
| Who writes the SQL | The agent's own model | sqlsentry's configured model, with your glossary, examples and repair loop |
| Needs an LLM in sqlsentry's config | **No**: sqlsentry is just a guarded database gateway | Yes (these tools only appear when one is configured) |

### Tools

| Tool | What it does |
|---|---|
| `list_datasources` | Databases the agent can use, their SQL dialect, and whether queries can run |
| `describe_schema` | The policy-filtered schema (tables, columns, keys, comments, sample values), glossary and verified examples |
| `validate_sql` | Checks SQL without running it; returns the query as it would run, or why it is rejected |
| `run_sql` | Runs one read-only query (default 50 rows, at most the policy's `max_rows`); returns the rows, the SQL as run, and whether results were truncated |
| `generate_sql` | Question → commented SQL + explanation (or a clarifying question). Doesn't run it |
| `ask` | Question → SQL + explanation + rows, in one call |

Rejected queries come back as readable tool errors (e.g. `invalid_sql: Column 'email' could not be
resolved`), so the agent can fix the query and try again. All tools are marked read-only.

## Setup

1. Create a config (`sqlsentry init`, or your own) and note its **absolute path**. MCP clients start
   servers from unpredictable folders; sqlsentry resolves every relative path in the config from the
   config file's own folder, so only the config path itself needs to be absolute.
2. If you use `generate_sql` / `ask`, put the model's key in a `.env` file **next to the config**
   (e.g. `GROQ_API_KEY=...`). The server reads it at startup, so no client config needs to contain
   the key. Keys can also be passed through the client's `env` settings instead.
3. Use the **absolute path to `sqlsentry-mcp`** in client configs if the client can't find it on
   your PATH. Find it with `where sqlsentry-mcp` (Windows) or `which sqlsentry-mcp` (macOS/Linux).

Then add it to your client.

### Claude Code

```bash
claude mcp add --transport stdio sqlsentry -- sqlsentry-mcp --config /absolute/path/to/sqlsentry.yaml
```

Add `--scope user` to make it available in all your projects, or `--scope project` to share it with
your team through a `.mcp.json` file at the project root:

```json
{
  "mcpServers": {
    "sqlsentry": {
      "type": "stdio",
      "command": "sqlsentry-mcp",
      "args": ["--config", "/absolute/path/to/sqlsentry.yaml"]
    }
  }
}
```

Check it with `claude mcp list` or `/mcp` inside Claude Code.

### Claude Desktop

Settings → Developer → **Edit Config** opens `claude_desktop_config.json`
(Windows: `%APPDATA%\Claude\claude_desktop_config.json`,
macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "sqlsentry": {
      "command": "C:\\path\\to\\venv\\Scripts\\sqlsentry-mcp.exe",
      "args": ["--config", "C:\\path\\to\\sqlsentry.yaml"]
    }
  }
}
```

On Windows, write backslashes doubled as shown. Quit and restart Claude Desktop completely after
saving.

### Cursor

`.cursor/mcp.json` in a project, or `~/.cursor/mcp.json` for all projects:

```json
{
  "mcpServers": {
    "sqlsentry": {
      "type": "stdio",
      "command": "sqlsentry-mcp",
      "args": ["--config", "/absolute/path/to/sqlsentry.yaml"]
    }
  }
}
```

### VS Code

`.vscode/mcp.json` in the workspace. Note the top-level key is `servers`, not `mcpServers`:

```json
{
  "servers": {
    "sqlsentry": {
      "type": "stdio",
      "command": "sqlsentry-mcp",
      "args": ["--config", "/absolute/path/to/sqlsentry.yaml"]
    }
  }
}
```

## Try it

With the sample store from `sqlsentry init`, ask your assistant things like:

- "Using sqlsentry, which product category brings in the most revenue?"
- "Show me customer email addresses." The agent can't: `email` is hidden by the sample policy.
- "Delete the cancelled orders." It can't do that either; the guard rejects anything but a `SELECT`.

## Security notes

- The server runs locally over stdio with **your** config; there is no network listener.
- The policy is the boundary: whatever `tables.include` / `columns.hidden` allow, the agent can read.
  Give agents a datasource with a narrow policy and a **read-only database user**.
- Use `allowed_providers` to keep a datasource's schema away from external models when sqlsentry's
  own model is used (`generate_sql` / `ask`). Note that with `describe_schema` / `run_sql`, the
  schema and the rows go to *the agent's* model, whichever that is.
- Queries are recorded in the configured history store (`store.url`), without result rows.
