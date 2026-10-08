"""Command-line interface.

sqlsentry init                       # starter config + sample database in this folder
sqlsentry inspect store              # exactly what the model will see
sqlsentry ask store "top 5 customers by revenue" --execute
sqlsentry validate store "SELECT * FROM customers"
sqlsentry serve                      # REST API on http://127.0.0.1:8000 (docs at /docs)
sqlsentry eval evals/datasets/sample_store.yaml
sqlsentry export-context store -o store.schema.json   # schema file for SQL-only use elsewhere
sqlsentry hash-key                  # new API key + the digest to put in the config
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from importlib.resources import files
from pathlib import Path

from . import __version__
from .errors import SQLSentryError


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env support for the CLI: KEY=VALUE lines, existing env vars win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _sentry(args):
    from .engine import SQLSentry

    return SQLSentry.from_config(args.config)


# ------------------------------------------------------------------ commands


def cmd_init(args) -> int:
    from .demo import create_sample_db

    target = Path(args.dir)
    target.mkdir(parents=True, exist_ok=True)
    cfg = target / "sqlsentry.yaml"
    if cfg.exists() and not args.force:
        print(f"{cfg} already exists (use --force to overwrite).", file=sys.stderr)
        return 1
    cfg.write_text(files("sqlsentry.demo").joinpath("sqlsentry.example.yaml").read_text(encoding="utf-8"))
    db = create_sample_db(target / "store.db")
    print(f"Created {cfg} and sample database {db}.\n")
    print("Next steps:")
    print("  1. Get a free key at https://console.groq.com/keys and set GROQ_API_KEY (or use a .env file),")
    print("     or run a local model with Ollama and set llm.default: local in sqlsentry.yaml.")
    print('  2. sqlsentry ask store "top 5 customers by revenue" --execute')
    return 0


def cmd_sample_db(args) -> int:
    from .demo import create_sample_db

    print(f"Created {create_sample_db(args.path)}")
    return 0


def cmd_inspect(args) -> int:
    from .prompts import render_schema

    sentry = _sentry(args)
    ds = sentry.datasource(args.datasource)
    catalog = sentry.schema(args.datasource)
    source = f"schema file {ds.schema_file}" if ds.schema_file else "live database"
    print(f"# {args.datasource} ({ds.resolved_dialect}, {ds.mode.replace('_', '-')}, from {source})")
    print("# This is exactly what the model can see.\n")
    print(render_schema(catalog) or "(no tables visible - check policy.tables.include)")
    p = ds.policy
    print(
        f"\n# policy: max_rows={p.max_rows} timeout_s={p.timeout_s} allow_execute={p.allow_execute} "
        f"providers={p.allowed_providers}"
    )
    if p.columns.hidden:
        print(f"# hidden columns: {', '.join(p.columns.hidden)}")
    return 0


def cmd_export_context(args) -> int:
    """Write the policy-filtered schema of a datasource to a JSON bundle.

    Run it inside the network that can reach the database; the bundle then works as a
    schema-only datasource anywhere (``schema_file: bundle.json``) without database access.
    """
    sentry = _sentry(args)
    catalog = sentry.schema(args.datasource)
    out = Path(args.output or f"{args.datasource}.schema.json")
    out.write_text(catalog.model_dump_json(indent=2), encoding="utf-8")
    hidden = sentry.datasource(args.datasource).policy.columns.hidden
    print(f"Wrote {out} ({len(catalog.tables)} tables). The policy was applied: hidden data is not in it.")
    if hidden:
        print(f"Hidden columns left out: {', '.join(hidden)}")
    print(f"Use it as:  schema_file: {out.name}   (plus an explicit dialect) in sqlsentry.yaml")
    return 0


def cmd_ask(args) -> int:
    sentry = _sentry(args)
    if args.execute:
        answer = sentry.ask(
            args.datasource, args.question, provider=args.provider, consumer="cli", max_rows=args.max_rows
        )
        gen, result = answer.generation, answer.result
    else:
        gen = sentry.generate(args.datasource, args.question, provider=args.provider, consumer="cli")
        result = None
    if args.json:
        out = {"generation": gen.model_dump(mode="json")}
        if result:
            out["result"] = result.model_dump(mode="json")
        print(json.dumps(out, indent=2))
        return 0

    if gen.status == "needs_clarification":
        print(f"Clarification needed: {gen.clarification_question}")
        return 0
    print(gen.sql, end="\n\n")
    print(f"Explanation: {gen.explanation}")
    for a in gen.assumptions:
        print(f"Assumption: {a}")
    for w in gen.warnings:
        print(f"Note: {w}")
    print(
        f"[{gen.provider}/{gen.model} | attempts {len(gen.attempts)} | {gen.latency_ms} ms | "
        f"tokens in {gen.usage.input_tokens} / out {gen.usage.output_tokens} | id {gen.id}]"
    )
    if result:
        print()
        _print_table(result.columns, result.rows)
        print(
            f"({result.row_count} rows{', truncated' if result.truncated else ''}, {result.duration_ms} ms)"
        )
    return 0


def cmd_validate(args) -> int:
    r = _sentry(args).validate(args.datasource, args.sql)
    if r.valid:
        print(r.sql)
        for w in r.warnings:
            print(f"Note: {w}")
        return 0
    for e in r.errors:
        print(f"invalid [{e.code}]: {e.message}", file=sys.stderr)
    return 1


def cmd_serve(args) -> int:
    try:
        import uvicorn

        from .server.app import create_app
    except ImportError:
        print("The server needs: pip install 'sqlsentry[server]'", file=sys.stderr)
        return 1
    app = create_app(_sentry(args))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_eval(args) -> int:
    from .evals import format_report, run_eval

    report = run_eval(_sentry(args), args.dataset, provider=args.provider)
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(format_report(report))
    return 0 if report.accuracy >= args.min_accuracy else 1


def cmd_hash_key(args) -> int:
    from .server.auth import generate_key, hash_key

    key = args.key or generate_key()
    if not args.key:
        print(f"API key (give this to the consumer, shown once): {key}")
    print(f"key_sha256 (put this in sqlsentry.yaml):           {hash_key(key)}")
    return 0


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sqlsentry", description="Safety-first natural language to SQL.")
    p.add_argument("--version", action="version", version=f"sqlsentry {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    def with_config(sp):
        sp.add_argument("-c", "--config", help="config file (default: $SQLSENTRY_CONFIG or ./sqlsentry.yaml)")
        return sp

    sp = sub.add_parser("init", help="create a starter config and sample database")
    sp.add_argument("--dir", default=".")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("sample-db", help="create the sample SQLite database")
    sp.add_argument("path", nargs="?", default="store.db")
    sp.set_defaults(func=cmd_sample_db)

    sp = with_config(sub.add_parser("inspect", help="show the policy-filtered schema the model sees"))
    sp.add_argument("datasource")
    sp.set_defaults(func=cmd_inspect)

    sp = with_config(sub.add_parser("ask", help="turn a question into SQL"))
    sp.add_argument("datasource")
    sp.add_argument("question")
    sp.add_argument("-x", "--execute", action="store_true", help="run the SQL (if policy allows)")
    sp.add_argument("--max-rows", type=int, default=20)
    sp.add_argument("--provider", help="LLM provider override")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_ask)

    sp = with_config(sub.add_parser("validate", help="check SQL against the guard and policy"))
    sp.add_argument("datasource")
    sp.add_argument("sql")
    sp.set_defaults(func=cmd_validate)

    sp = with_config(sub.add_parser("export-context", help="save a datasource's filtered schema to a file"))
    sp.add_argument("datasource")
    sp.add_argument("-o", "--output", help="output file (default: <datasource>.schema.json)")
    sp.set_defaults(func=cmd_export_context)

    sp = with_config(sub.add_parser("serve", help="run the REST API"))
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    sp.set_defaults(func=cmd_serve)

    sp = with_config(sub.add_parser("eval", help="measure accuracy on a golden dataset"))
    sp.add_argument("dataset")
    sp.add_argument("--provider")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--min-accuracy", type=float, default=0.0, help="exit 1 below this (0-1), for CI")
    sp.set_defaults(func=cmd_eval)

    sp = sub.add_parser("hash-key", help="generate an API key and its digest")
    sp.add_argument("key", nargs="?", help="hash an existing key instead of generating one")
    sp.set_defaults(func=cmd_hash_key)
    return p


def _print_table(columns: list[str], rows: list[list], max_width: int = 40) -> None:
    cells = [[str(c) for c in columns]] + [["" if v is None else str(v)[:max_width] for v in r] for r in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(columns))]
    for n, row in enumerate(cells):
        print("  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)).rstrip())
        if n == 0:
            print("  ".join("-" * w for w in widths))


def _safe_console() -> None:
    """Model output can contain characters a legacy console code page (e.g. Windows cp1252)
    can't encode; replace them instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _safe_console()
    _load_dotenv()
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    try:
        return args.func(args)
    except SQLSentryError as e:
        print(f"error [{e.code}]: {e.message}", file=sys.stderr)
        for a in e.details.get("attempts", []):
            print(f"  attempt {a['number']} ({a['stage']}): {a.get('error')}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
