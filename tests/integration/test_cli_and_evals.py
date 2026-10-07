import json
from pathlib import Path

import pytest
import yaml

from sqlsentry.cli import main
from sqlsentry.config import Settings
from sqlsentry.evals import results_match
from sqlsentry.server.auth import hash_key


@pytest.fixture
def config_with_fake(tmp_path, sample_db_path):
    def _write(responses):
        cfg = {
            "llm": {
                "default": "fake",
                "providers": {"fake": {"type": "fake", "options": {"responses": responses}}},
            },
            "datasources": {
                "store": {
                    "url": f"sqlite:///{Path(sample_db_path).as_posix()}",
                    "policy": {
                        "tables": {"include": ["*"], "exclude": ["internal_*"]},
                        "columns": {"hidden": ["customers.email"]},
                        "allow_execute": True,
                    },
                }
            },
            "store": {"url": None},
        }
        path = tmp_path / "sqlsentry.yaml"
        path.write_text(yaml.safe_dump(cfg))
        return str(path)

    return _write


def test_init_creates_working_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "dummy")
    assert main(["init"]) == 0
    assert (tmp_path / "store.db").is_file()
    settings = Settings.from_yaml(tmp_path / "sqlsentry.yaml")
    assert settings.llm.default == "groq" and "store" in settings.datasources
    assert main(["init"]) == 1  # refuses to overwrite
    assert main(["inspect", "store"]) == 0
    out = capsys.readouterr().out
    assert "TABLE customers" in out and "email" not in out.split("# hidden columns")[0]


def test_hash_key(capsys):
    assert main(["hash-key", "sqs_abc"]) == 0
    assert hash_key("sqs_abc") in capsys.readouterr().out
    assert main(["hash-key"]) == 0
    assert "API key" in capsys.readouterr().out


def test_ask_and_execute(config_with_fake, capsys):
    cfg = config_with_fake(
        [{"sql": "-- count\nSELECT COUNT(*) AS n FROM orders", "explanation": "Counts orders."}]
    )
    assert main(["ask", "store", "how many orders", "-c", cfg, "--execute"]) == 0
    out = capsys.readouterr().out
    assert "SELECT" in out and "Counts orders." in out and "900" in out


def test_ask_json(config_with_fake, capsys):
    cfg = config_with_fake([{"sql": "SELECT name FROM products", "explanation": "x"}])
    assert main(["ask", "store", "products", "-c", cfg, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["generation"]["status"] == "ok"


def test_ask_failure_exit_code(config_with_fake, capsys):
    cfg = config_with_fake([{"sql": "DELETE FROM orders"}] * 3)
    assert main(["ask", "store", "delete", "-c", cfg]) == 1
    assert "generation_failed" in capsys.readouterr().err


def test_validate_command(config_with_fake, capsys):
    cfg = config_with_fake([])
    assert main(["validate", "store", "SELECT name FROM products", "-c", cfg]) == 0
    assert main(["validate", "store", "SELECT email FROM customers", "-c", cfg]) == 1


def test_eval(config_with_fake, tmp_path, capsys):
    dataset = tmp_path / "ds.yaml"
    dataset.write_text(
        yaml.safe_dump(
            {
                "datasource": "store",
                "cases": [
                    {"id": "count", "question": "how many orders", "sql": "SELECT COUNT(*) FROM orders"},
                    {"id": "wrong", "question": "how many products", "sql": "SELECT COUNT(*) FROM products"},
                ],
            }
        )
    )
    cfg = config_with_fake(
        [
            {"sql": "SELECT COUNT(id) AS total FROM orders"},  # different SQL, same result -> pass
            {"sql": "SELECT COUNT(*) FROM customers"},  # wrong -> fail
        ]
    )
    assert main(["eval", str(dataset), "-c", cfg, "--min-accuracy", "0.9"]) == 1
    out = capsys.readouterr().out
    assert "PASS  count" in out and "FAIL  wrong" in out and "1/2 = 50%" in out


def test_results_match():
    assert results_match([[1, "a"], [2, "b"]], [[2, "b"], [1, "a"]])
    assert results_match([[1.004, "a"]], [["a", 1.0]])
    assert not results_match([[1]], [[1], [1]])


def test_results_match_allows_extra_columns():
    assert results_match([["Office Chair"]], [["Office Chair", 123.45]])
    assert not results_match([["Office Chair"]], [["Desk Lamp", 123.45]])


def test_shipped_example_config_and_eval_dataset_are_consistent(sample_db_url):
    """Every golden query must pass the guard under the example config's policy."""
    from sqlsentry import SQLSentry
    from sqlsentry.evals import load_dataset

    root = Path(__file__).resolve().parents[2]
    settings = Settings.from_yaml(root / "examples" / "sqlsentry.yaml")
    settings.datasources["store"].url = sample_db_url
    settings.store.url = None
    dataset = load_dataset(root / "evals" / "datasets" / "sample_store.yaml")
    assert len(dataset.cases) >= 20
    with SQLSentry(settings) as sentry:
        for case in dataset.cases:
            result = sentry.execute_sql("store", case.sql)
            assert result.row_count >= 1, case.id
