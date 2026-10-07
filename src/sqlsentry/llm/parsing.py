"""Extract a :class:`SQLCandidate` from model output.

Works with models that have no native JSON mode: tolerates markdown fences, reasoning
tags and surrounding prose, and falls back to a bare ```sql block. Anything else raises
:class:`ParseError`, which the engine feeds back to the model as a repair request.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from ..types import SQLCandidate

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_SQL_FENCE = re.compile(r"```sql\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class ParseError(ValueError):
    pass


def parse_candidate(text: str) -> SQLCandidate:
    text = _THINK.sub("", text or "").strip()
    if not text:
        raise ParseError("The response was empty.")

    for blob in _json_candidates(text):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            try:
                return SQLCandidate.model_validate(data)
            except ValidationError as e:
                raise ParseError(f"The JSON did not match the required schema: {e.errors()[0]['msg']}") from e

    m = _SQL_FENCE.search(text)
    if m and m.group(1).strip():
        return SQLCandidate(
            sql=m.group(1).strip(), assumptions=["Model returned SQL without the JSON wrapper."]
        )
    raise ParseError("The response was not a JSON object with the required keys.")


def _json_candidates(text: str) -> list[str]:
    out = [m.group(1) for m in _JSON_FENCE.finditer(text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        out.append(text[start : end + 1])
    return out
