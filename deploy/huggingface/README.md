---
title: sqlsentry playground
emoji: 🛡️
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: apache-2.0
short_description: Ask a database in plain English; get guarded, read-only SQL.
---

# sqlsentry playground

Ask a question in plain English and get commented SQL, an explanation and the rows. Or paste your own
`CREATE TABLE` statements to get SQL for your schema, and try to get something past the read-only guard.

Source and docs: https://github.com/2002sairuthvik/sqlsentry

## Deploying this Space

These files live in `deploy/huggingface/` of the sqlsentry repo.

1. Create a new Space on huggingface.co with the **Docker** SDK (blank template, free CPU).
2. Upload `Dockerfile`, `README.md` and `sqlsentry.yaml` from this folder.
3. In the Space's **Settings → Variables and secrets**, add the secret `GROQ_API_KEY` (free key from
   https://console.groq.com/keys).

The Space builds, then serves the playground. The REST API stays closed (no API keys are configured),
visitors are rate-limited, and nothing they type is stored.
