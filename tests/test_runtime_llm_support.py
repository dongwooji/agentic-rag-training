"""Runtime copies must stay behavior-identical to the frozen originals."""

import pytest

from src.agent.planner import _extract_planning_json_text as historical_planning
from src.agent.planner import estimate_cost_usd as historical_cost
from src.grading.provider import _extract_final_json_text as historical_final
from src.llm_support import (
    estimate_cost_usd,
    extract_final_json_text,
    extract_planning_json_text,
)
from src.retrieval.baseline import EMBEDDING_RUN_ID as HISTORICAL_EMBEDDING_RUN_ID
from src.retrieval.runtime_config import EMBEDDING_RUN_ID


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


RESPONSES = [
    {"output": [_message('{"a": 1}', "final_answer")]},
    {"output": [_message('{"c": 0}', "commentary"), _message('{"a": 1}', "final_answer")]},
    {"output": [_message('{"u": 1}')]},
    {"output": [_message('{"u": 1}'), _message('{"u": 2}')]},
    {"output": [_message('{"f": 1}', "final_answer"), _message('{"f": 2}', "final_answer")]},
    {"output": [_message('{"c": 1}', "commentary")]},
    {"output": [_message('{"c": 1}', "commentary")], "output_text": '{"t": 1}'},
    {"output": [{"type": "reasoning"}], "output_text": '{"t": 1}'},
    {"output": [_message("   ")], "output_text": '{"t": 2}'},
    {"output_text": '{"t": 3}'},
    {"output_text": "   "},
    {},
    None,
    _Obj(output=[_message('{"o": 1}', "final_answer", as_object=True)], output_text=None),
    _Obj(output=None, output_text='{"o": 2}'),
]


def _outcome(function, response):
    try:
        return ("ok", function(response))
    except Exception as error:  # noqa: BLE001 - comparing exact failure behavior
        return (type(error).__name__, str(error))


@pytest.mark.parametrize("response", RESPONSES)
def test_extract_final_json_text_matches_historical(response):
    assert _outcome(extract_final_json_text, response) == _outcome(historical_final, response)


@pytest.mark.parametrize("response", RESPONSES)
def test_extract_planning_json_text_matches_historical(response):
    assert _outcome(extract_planning_json_text, response) == _outcome(
        historical_planning, response
    )


@pytest.mark.parametrize(
    "tokens",
    [(0, 0, 0), (1000, 0, 500), (1000, 400, 500), (100, 400, 7), (123456, 2345, 6789)],
)
def test_estimate_cost_usd_matches_historical(tokens):
    input_tokens, cached, output = tokens
    pricing = {"input": 1.25, "cached_input": 0.125, "output": 10.0}
    kwargs = dict(
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        output_tokens=output,
        pricing=pricing,
    )
    assert estimate_cost_usd(**kwargs) == historical_cost(**kwargs)


def test_runtime_embedding_run_id_matches_frozen_dense_baseline():
    assert EMBEDDING_RUN_ID == HISTORICAL_EMBEDDING_RUN_ID
