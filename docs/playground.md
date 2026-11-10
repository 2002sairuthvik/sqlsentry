# Playground

A browser page for trying sqlsentry without writing code. It has three parts:

1. **Ask:** a question in plain English gives commented SQL, an explanation and, on a connected
   database, the rows.
2. **Paste your schema:** `CREATE TABLE` statements (for example from `pg_dump --schema-only`) give SQL
   for *your* database. Pasted schemas are never executed.
3. **Try to break the guard:** any SQL, checked by the guard with no model involved, shows exactly
   what would run or why it is blocked.

## Run it locally

```bash
sqlsentry serve --playground          # then open http://127.0.0.1:8000/playground
```

It uses the datasources and model from your config. Or enable it permanently in the config:

```yaml
server:
  playground:
    enabled: true
    datasources: [store]            # which datasources visitors may use; empty = all
    allow_custom_schema: true       # let visitors paste DDL (schema-only, never executed)
    custom_schema_max_chars: 50000
    requests_per_minute: 10         # model calls per visitor IP; guard checks allow 6x more
    max_rows: 50
    trust_proxy_headers: false      # true only behind a trusted reverse proxy (uses X-Forwarded-For)
    sample_questions:
      store: ["Which product category brings in the most revenue?"]
```

When the playground is enabled, `/` opens it instead of the API docs.

## How it is protected

The playground needs no API key, so it is deliberately narrow:

- Only the datasources listed under `playground.datasources` are reachable; others in the same config
  stay invisible to visitors.
- A pasted schema becomes a temporary, schema-only datasource. It is never executed, its size is
  limited, it doesn't appear in the REST API's datasource lists, and only the most recent 32 are
  kept in memory.
- Each visitor IP is rate-limited, and the limit is checked *before* the model is called.
- Everything the model or a visitor produces is shown as text, never inserted as HTML.
- The REST API keeps its normal authentication.

Questions and pasted schemas are sent to the configured model, which the page tells visitors. For a
public demo, set `store.url: null` so nothing visitors type is saved.

## Host a public demo on Hugging Face Spaces (free)

`deploy/huggingface/` in the repo contains a ready-made Space: a `Dockerfile`, the Space `README.md`
and a public-demo config (sample store only, strict rate limit, REST API closed, nothing stored).

1. Create a free account at https://huggingface.co and a **new Space**: SDK **Docker**, blank
   template, free CPU hardware.
2. Upload the three files from `deploy/huggingface/` to the Space (web upload works).
3. In the Space's **Settings → Variables and secrets**, add the secret `GROQ_API_KEY`.

The Space builds the image, creates the sample database and serves the playground at its URL.
