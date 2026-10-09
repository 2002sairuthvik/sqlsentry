# Evals

`sqlsentry eval` runs each question in a dataset through the full pipeline (linking, prompt,
model, guard, repair loop), executes both the generated and the golden SQL, and compares the
result sets. Row order, column order and extra columns don't matter; numbers are compared
after rounding to two decimals.

```bash
sqlsentry sample-db examples/store.db
sqlsentry eval evals/datasets/sample_store.yaml -c examples/sqlsentry.yaml
sqlsentry eval evals/datasets/sample_store.yaml -c examples/sqlsentry.yaml --provider local --json
```

### Free tiers and rate limits

When the provider says "slow down" (HTTP 429), the eval waits as long as the provider asks
(`Retry-After` header, or hints like Groq's "try again in 7.5s") and retries that question, up to
`--rate-limit-retries` times (default 3). A question that is still rate-limited is reported as
`RATE`, separately from wrong answers, and the summary shows accuracy both overall and over the
questions that were actually answered.
`--pace 5` waits 5 seconds between questions to avoid hitting limits in the first place.

Daily quotas are different: Groq's free tier, for example, allows 200,000 tokens per day, and one
run of the sample dataset uses about 35-45k. Waiting can't beat a daily cap, so when the provider
reports one the eval stops immediately with `STOPPED EARLY` instead of retrying. Run again later
or switch models with `--provider`.

Use it to:

- **choose a model:** run the same dataset with each provider;
- **tune context:** check whether a glossary entry or example actually helps;
- **guard against regressions:** `--min-accuracy 0.8` exits non-zero below 80%.

Write your own dataset from real questions your users ask, with SQL you have verified. Avoid
"top N" questions whose cutoff has ties, because any of the tied rows is a correct answer.
