# Configuration

sqlsentry reads one YAML file: `--config`, else `$SQLSENTRY_CONFIG`, else `./sqlsentry.yaml`.
`${VAR}` and `${VAR:-default}` are expanded from the environment, and the CLI also loads a
`.env` file from the working directory. `sqlsentry init` writes a commented starter file.

## `llm`

```yaml
llm:
  default: groq                 # provider used unless a datasource or request overrides it
  providers:
    groq:
      type: openai_compatible   # openai_compatible | anthropic | fake | "my.module:MyProvider"
      base_url: https://api.groq.com/openai/v1
      model: openai/gpt-oss-120b
      api_key_env: GROQ_API_KEY # name of the env var, never the key itself
      temperature: 0.0
      max_tokens: 4096
      timeout_s: 60
      max_retries: 2
      json_mode: true           # request JSON output; auto-disabled if the server rejects it
      options: {}               # extra request fields (openai_compatible) or provider options
```

See [providers.md](providers.md) for ready-made provider blocks.

## `datasources`

```yaml
datasources:
  store:
    url: sqlite:///store.db     # any SQLAlchemy URL; use a read-only user
    dialect: sqlite             # optional; inferred from the URL
    db_schema: public           # optional schema/namespace to introspect
    description: ...            # shown to the model
    sample_values: 5            # distinct sample values per visible text column (0 = none)
    llm: null                   # provider override for this datasource
    policy:
      tables:
        include: ["*"]          # default-deny; shell-style wildcards, case-insensitive
        exclude: ["internal_*"]
      columns:
        hidden: ["customers.email", "*.ssn"]
      max_rows: 1000
      timeout_s: 30
      allow_execute: false
      allowed_providers: ["*"]
    glossary:                   # business definitions the model must follow
      - "revenue = SUM(order_items.quantity * order_items.unit_price)"
    examples:                   # verified question -> SQL pairs, the strongest accuracy lever
      - question: How many orders were cancelled?
        sql: SELECT COUNT(*) FROM orders WHERE status = 'cancelled'
```

## `consumers` (REST API)

```yaml
consumers:
  - name: analytics-team
    key_sha256: ["<digest from `sqlsentry hash-key`>"]   # several keys allow rotation
    grants:
      - datasource: store       # or "*"
        scopes: [generate, execute, validate, schema]
```

## `engine`

```yaml
engine:
  max_attempts: 3               # generate -> guard -> dry-run -> repair loop
  max_tables_in_prompt: 15      # schema linking kicks in above this
  max_examples_in_prompt: 3
  dry_run: true                 # EXPLAIN before returning (where the dialect supports it)
  schema_cache_ttl_s: 600
```

## `server` and `store`

```yaml
server:
  auth_enabled: true            # false only for local development
  rate_limit_per_minute: 60     # per consumer; 0 disables
  cors_origins: []

store:
  url: sqlite:///.sqlsentry/history.db   # generations, executions (no rows) and feedback; null = memory
```
