"""Eval patience: rate limits are waited out and retried, and never counted as wrong answers."""

import json

import httpx
import pytest
import yaml

from sqlsentry import SQLSentry
from sqlsentry.config import DataSourceConfig, LLMSettings, ProviderConfig, Settings, StoreSettings
from sqlsentry.errors import LLMError
from sqlsentry.evals import format_report, run_eval
from sqlsentry.llm import FakeProvider
from sqlsentry.llm.openai_compatible import OpenAICompatibleProvider, retry_after_seconds
from sqlsentry.store import MemoryStore

RIGHT = {"sql": "SELECT COUNT(*) AS n FROM orders", "explanation": "e"}


def rate_limited(retry_after_s=None):
    details = {"status": 429}
    if retry_after_s is not None:
        details["retry_after_s"] = retry_after_s

    def raise_it(system, messages):
        raise LLMError("HTTP 429: Rate limit reached", details=details)

    return raise_it


def server_error(system, messages):
    raise LLMError("HTTP 500: boom", details={"status": 500})


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / "ds.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "datasource": "store",
                "cases": [
                    {"id": "count", "question": "how many orders", "sql": "SELECT COUNT(*) FROM orders"}
                ],
            }
        )
    )
    return path


@pytest.fixture
def make(sample_db_url, store_policy):
    def _make(*responses):
        fake = FakeProvider(responses, name="fake")
        settings = Settings(
            datasources={"store": DataSourceConfig(url=sample_db_url, policy=store_policy)},
            llm=LLMSettings(default="fake", providers={"fake": ProviderConfig(type="fake")}),
            store=StoreSettings(url=None),
        )
        return SQLSentry(settings, providers={"fake": fake}, store=MemoryStore()), fake

    return _make


def test_waits_as_long_as_the_provider_asks_then_passes(make, dataset):
    sentry, fake = make(rate_limited(7.5), rate_limited(2.0), RIGHT)
    waits = []
    report = run_eval(sentry, dataset, sleep=waits.append)
    assert [r.status for r in report.results] == ["pass"]
    assert waits == [8.5, 3.0]  # the hint plus a 1 s margin
    assert report.rate_limit_waits == 2 and report.rate_limited == 0 and report.accuracy == 1.0


def test_default_wait_when_no_hint(make, dataset):
    sentry, _ = make(rate_limited(), RIGHT)
    waits = []
    run_eval(sentry, dataset, rate_limit_wait_s=12, sleep=waits.append)
    assert waits == [12]


def test_gives_up_and_reports_rate_limited_not_wrong(make, dataset):
    sentry, fake = make(*[rate_limited(1.0)] * 3)
    report = run_eval(sentry, dataset, rate_limit_retries=2, sleep=lambda s: None)
    r = report.results[0]
    assert r.status == "rate_limited" and "after 2 retries" in r.error
    assert len(fake.calls) == 3
    assert report.rate_limited == 1 and report.answered_accuracy == 0.0 and report.passed == 0
    assert "RATE" in format_report(report) and "1 rate-limited" in format_report(report)


def test_other_llm_errors_are_not_retried(make, dataset):
    sentry, fake = make(server_error, RIGHT)
    waits = []
    report = run_eval(sentry, dataset, sleep=waits.append)
    assert report.results[0].status == "error" and waits == [] and len(fake.calls) == 1


def test_wait_is_capped(make, dataset):
    sentry, _ = make(rate_limited(3600), RIGHT)
    waits = []
    run_eval(sentry, dataset, sleep=waits.append)
    assert waits == [120.0]


def test_pace_between_questions(make, tmp_path):
    path = tmp_path / "two.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "datasource": "store",
                "cases": [
                    {"question": "a", "sql": "SELECT COUNT(*) FROM orders"},
                    {"question": "b", "sql": "SELECT COUNT(*) FROM orders"},
                ],
            }
        )
    )
    sentry, _ = make(RIGHT, RIGHT)
    waits = []
    run_eval(sentry, path, pace_s=4, sleep=waits.append)
    assert waits == [4]  # only between questions, not before the first


# ---------------------------------------------------------------- provider retry hints


def _resp(status=429, message="Rate limit reached", headers=None):
    return httpx.Response(status, json={"error": {"message": message}}, headers=headers or {})


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Rate limit reached ... Please try again in 7.5s. Need more tokens?", 7.5),
        ("Please try again in 1m2.5s.", 62.5),
        ("Please try again in 450ms.", 0.45),
        ("Please try again in 2m.", 120.0),
        ("Rate limit reached", None),
    ],
)
def test_retry_hint_from_message(message, expected):
    assert retry_after_seconds(_resp(message=message)) == expected


def test_retry_after_header_wins():
    assert retry_after_seconds(_resp(message="try again in 9s", headers={"retry-after": "3"})) == 3.0


def test_provider_passes_retry_hint_in_error_details():
    p = OpenAICompatibleProvider(
        "groq", ProviderConfig(type="openai_compatible", base_url="http://x/v1", model="m", max_retries=0)
    )
    p._client = httpx.Client(
        base_url="http://x/v1",
        transport=httpx.MockTransport(lambda req: _resp(message="Please try again in 6.25s.")),
    )
    with pytest.raises(LLMError) as e:
        p.complete("s", [], {})
    assert e.value.details == {"status": 429, "retry_after_s": 6.25}
    assert json.dumps(e.value.details)  # serialisable for API error bodies


def daily_limited(system, messages):
    raise LLMError(
        "HTTP 429: Rate limit reached for model `m` on tokens per day (TPD): Limit 200000, Used 199000. "
        "Please try again in 1m16s.",
        details={"status": 429, "retry_after_s": 76.0},
    )


def test_daily_quota_stops_the_run_instead_of_waiting(make, tmp_path):
    path = tmp_path / "three.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "datasource": "store",
                "cases": [
                    {"id": f"q{i}", "question": "q", "sql": "SELECT COUNT(*) FROM orders"} for i in range(3)
                ],
            }
        )
    )
    sentry, fake = make(RIGHT, daily_limited)
    waits = []
    report = run_eval(sentry, path, sleep=waits.append)
    assert [r.status for r in report.results] == ["pass", "rate_limited", "rate_limited"]
    assert report.results[2].error == "not run"
    assert waits == [] and len(fake.calls) == 2  # no waiting, no further model calls
    assert "daily quota" in report.stopped_early
    assert "STOPPED EARLY" in format_report(report) and "(1 question not run)" in format_report(report)


def test_provider_does_not_retry_a_daily_cap(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: pytest.fail("should not wait on a daily cap"))
    calls = []

    def handler(req):
        calls.append(1)
        return _resp(
            message="Rate limit reached on tokens per day (TPD): Limit 200000. Please try again in 5m."
        )

    p = OpenAICompatibleProvider(
        "groq", ProviderConfig(type="openai_compatible", base_url="http://x/v1", model="m", max_retries=2)
    )
    p._client = httpx.Client(base_url="http://x/v1", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as e:
        p.complete("s", [], {})
    assert len(calls) == 1 and e.value.details["retry_after_s"] == 300.0


def test_report_when_nothing_was_answered(make, dataset):
    sentry, _ = make(daily_limited)
    text = format_report(run_eval(sentry, dataset, sleep=lambda s: None))
    assert "no questions were answered" in text and "0/0" not in text
