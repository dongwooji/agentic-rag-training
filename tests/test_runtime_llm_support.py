"""Pinned behavior of the runtime OpenAI Responses helpers.

The expected outcomes were captured from ``src.llm_support`` after it was verified
to match the historical LLM Planner and Grader v1 provider originals
(preserved in the ``legacy-pre-retrieval-v2`` tag) on the same inputs,
including exact error types and messages.
"""

import pytest

from src.llm_support import (
    estimate_cost_usd,
    extract_final_json_text,
    extract_planning_json_text,
)
from src.retrieval.runtime_config import CORPUS_VERSION, EMBEDDING_RUN_ID


class _Obj:
    def __init__(self, **values):
        self.__dict__.update(values)


def _message(text, phase=None, as_object=False):
    content = [{"type": "output_text", "text": text}]
    item = {"type": "message", "content": content}
    if phase is not None:
        item["phase"] = phase
    if as_object:
        return _Obj(type="message", phase=phase, content=[_Obj(type="output_text", text=text)])
    return item


_MULTIPLE_GRADES = ("RuntimeError", "Responses API returned multiple final grades")
_NO_GRADING = ("RuntimeError", "Responses API returned no grading JSON")
_MULTIPLE_PLANNING = (
    "RuntimeError",
    "Responses API returned multiple final planning messages",
)
_COMMENTARY_ONLY = (
    "RuntimeError",
    "Responses API returned commentary but no final planning message",
)
_NO_PLANNING = ("RuntimeError", "Responses API returned no planning JSON")

# (response, extract_final_json_text outcome, extract_planning_json_text outcome)
CASES = [
    (
        {"output": [_message('{"a": 1}', "final_answer")]},
        ("ok", '{"a": 1}'),
        ("ok", '{"a": 1}'),
    ),
    (
        {"output": [_message('{"c": 0}', "commentary"), _message('{"a": 1}', "final_answer")]},
        ("ok", '{"a": 1}'),
        ("ok", '{"a": 1}'),
    ),
    ({"output": [_message('{"u": 1}')]}, ("ok", '{"u": 1}'), ("ok", '{"u": 1}')),
    (
        {"output": [_message('{"u": 1}'), _message('{"u": 2}')]},
        _MULTIPLE_GRADES,
        _MULTIPLE_PLANNING,
    ),
    (
        {"output": [_message('{"f": 1}', "final_answer"), _message('{"f": 2}', "final_answer")]},
        _MULTIPLE_GRADES,
        _MULTIPLE_PLANNING,
    ),
    ({"output": [_message('{"c": 1}', "commentary")]}, _NO_GRADING, _COMMENTARY_ONLY),
    (
        {"output": [_message('{"c": 1}', "commentary")], "output_text": '{"t": 1}'},
        ("ok", '{"t": 1}'),
        _COMMENTARY_ONLY,
    ),
    (
        {"output": [{"type": "reasoning"}], "output_text": '{"t": 1}'},
        ("ok", '{"t": 1}'),
        ("ok", '{"t": 1}'),
    ),
    (
        {"output": [_message("   ")], "output_text": '{"t": 2}'},
        ("ok", '{"t": 2}'),
        ("ok", '{"t": 2}'),
    ),
    ({"output_text": '{"t": 3}'}, ("ok", '{"t": 3}'), ("ok", '{"t": 3}')),
    ({"output_text": "   "}, _NO_GRADING, _NO_PLANNING),
    ({}, _NO_GRADING, _NO_PLANNING),
    (None, _NO_GRADING, _NO_PLANNING),
    (
        _Obj(output=[_message('{"o": 1}', "final_answer", as_object=True)], output_text=None),
        ("ok", '{"o": 1}'),
        ("ok", '{"o": 1}'),
    ),
    (_Obj(output=None, output_text='{"o": 2}'), ("ok", '{"o": 2}'), ("ok", '{"o": 2}')),
]


def _outcome(function, response):
    try:
        return ("ok", function(response))
    except Exception as error:  # noqa: BLE001 - comparing exact failure behavior
        return (type(error).__name__, str(error))


@pytest.mark.parametrize(("response", "expected", "_planning"), CASES)
def test_extract_final_json_text_pinned_behavior(response, expected, _planning):
    assert _outcome(extract_final_json_text, response) == expected


@pytest.mark.parametrize(("response", "_final", "expected"), CASES)
def test_extract_planning_json_text_pinned_behavior(response, _final, expected):
    assert _outcome(extract_planning_json_text, response) == expected


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        ((0, 0, 0), 0.0),
        ((1000, 0, 500), 0.00625),
        ((1000, 400, 500), 0.0058),
        ((100, 400, 7), 0.00012),
        ((123456, 2345, 6789), 0.219571875),
    ],
)
def test_estimate_cost_usd_pinned_behavior(tokens, expected):
    input_tokens, cached, output = tokens
    pricing = {"input": 1.25, "cached_input": 0.125, "output": 10.0}
    assert (
        estimate_cost_usd(
            input_tokens=input_tokens,
            cached_input_tokens=cached,
            output_tokens=output,
            pricing=pricing,
        )
        == expected
    )


def test_runtime_retrieval_identifiers_match_frozen_corpus_and_embedding_run():
    assert EMBEDDING_RUN_ID == "dense_multilingual_minilm_l12_v2_literature_corpus_v1"
    assert CORPUS_VERSION == "literature_corpus_v1"
