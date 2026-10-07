# Security model

sqlsentry turns untrusted text into SQL. This page explains exactly what protects you, and
what you still need to do yourself.

## Layers of defence

| # | Layer | Where | Can it be configured? |
|---|---|---|---|
| 1 | **Read-only database user** | Your database | You must set it up |
| 2 | **Locked baseline guard** | `sqlsentry/guard/baseline.py` | **No**, by design |
| 3 | **Policy guard** | `sqlsentry/guard/rules.py` + your config | Yes |
| 4 | **Read-only execution** | `sqlsentry/execution/executor.py` | No |
| 5 | **API auth, grants, rate limits** | `sqlsentry/server/` | Yes |

### Layer 2: locked baseline

Every SQL string is parsed with sqlglot and rejected unless it is:

- exactly **one** statement;
- a `SELECT` (CTEs and set operations allowed);
- free of write, DDL, DCL, transaction, session or procedural nodes **anywhere** in the tree
  (e.g. a `DELETE` hidden inside a CTE);
- free of row locking (`FOR UPDATE`) and `SELECT ... INTO`;
- free of deny-listed functions: sleeping (`pg_sleep`, `SLEEP`, `BENCHMARK`), file/OS/network access
  (`pg_read_file`, `LOAD_FILE`, `xp_cmdshell`, `read_csv`, `dblink`, ...), dynamic SQL
  (`query_to_xml`, `OPENQUERY`, ...) and server settings (`set_config`). Prefix rules also
  cover whole families (`pg_*`, `xp_*`, `dbms_*`, ...).

### Layer 3: policy

- **Tables are default-deny.** Only tables matched by `policy.tables.include` (and not by `exclude`) exist
  as far as the model and the guard are concerned. Unknown and forbidden tables get the same
  error message, so errors don't reveal what exists.
- **Hidden columns** are removed at introspection time. They are never read, never sampled, and
  never sent to a model. Queries that reference them fail, and `SELECT *` is rewritten into the
  visible column list.
- **Row cap:** a `LIMIT` is added or lowered to `max_rows`.
- **Provider allow-list:** `allowed_providers` controls which LLM providers may receive this
  datasource's schema (e.g. only a self-hosted model).
- Execution re-checks the SQL against the **current** policy, so tightening a policy takes effect
  for SQL generated earlier too.

### Layer 4: execution

Execution runs in a transaction that is always rolled back. Where the dialect supports it, it is also
read-only at the database level (`SET TRANSACTION READ ONLY` on Postgres, `PRAGMA query_only` on
SQLite, `SET SESSION TRANSACTION READ ONLY` on MySQL) with a statement timeout.

## What is sent to the LLM

Only the policy-filtered schema (table and column names, types, comments, foreign keys, and a few
sample values from visible text columns), your glossary, your examples, and the question.
**Query results are never sent.** If even sample values are too sensitive, set `sample_values: 0`.

## What you still need to do

- **Use a read-only database user** with grants on only the tables the policy includes. The guard is
  strong, but defence in depth means not relying on any single layer.
- Keep `server.auth_enabled: true` anywhere other than your own machine.
- Treat model output as untrusted. sqlsentry already does this; your application should too
  (e.g. don't render `explanation` as HTML without escaping).
- Remember that free hosted LLM tiers may use prompts for training. Use a local model for
  sensitive schemas.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
