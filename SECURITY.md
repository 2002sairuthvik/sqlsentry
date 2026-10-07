# Security policy

sqlsentry turns untrusted text into SQL that may run against real databases, so security
reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private
[security advisory form](https://github.com/2002sairuthvik/sqlsentry/security/advisories/new)
instead. Include a description, steps to reproduce, and the impact you expect.

You should get an acknowledgement within 7 days.

## What counts as a vulnerability

- Any way to get a non-`SELECT` statement, more than one statement, or a deny-listed
  function past the guard (Layer 1 baseline).
- Any way to read a table or column that the datasource policy hides (Layer 2).
- Any way to bypass API key authentication, grants or scopes.
- Credentials or result rows leaking into logs, prompts or storage.

## Deployment expectations

The guard is one layer of defence, not the only one. Always connect sqlsentry with a
**read-only database user** that can see only what it needs. See `docs/security-model.md`.
