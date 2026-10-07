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

Use it to:

- **choose a model:** run the same dataset with each provider;
- **tune context:** check whether a glossary entry or example actually helps;
- **guard against regressions:** `--min-accuracy 0.8` exits non-zero below 80%.

Write your own dataset from real questions your users ask, with SQL you have verified. Avoid
"top N" questions whose cutoff has ties, because any of the tied rows is a correct answer.
