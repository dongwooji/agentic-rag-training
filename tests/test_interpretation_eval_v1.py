"""Synthetic evaluator contracts; no frozen evaluation inputs/provider calls."""
import pytest

from scripts.evaluate_interpretation_v1 import action, parse_cases, projection, score


def label(identifier="D01", route="문헌", actions=None):
    return dict(id=identifier, split="dev", question="저항훈련 연구 요약", expected_route=route,
                allowed_actions=actions or ["답변"])


def observation(route="literature_only", terminal="answer_ready", metadata=None, tools=None, http=200):
    return dict(id="D01", question="저항훈련 연구 요약", route={"route": route}, http_status=http,
                response=dict(final_status=terminal, tools_used=["search_literature"] if tools is None else tools,
                              response_metadata=metadata))


def test_parse_preserves_question_and_projection_excludes_labels():
    text = "| D01 | 저항훈련 연구 요약 | 문헌 | 답변, 되묻기 | 주의 문구 |\n"
    cases = parse_cases(text)
    assert cases[0]["allowed_actions"] == ["답변", "되묻기"]
    assert projection(cases, "dev") == [{"id": "D01", "question": "저항훈련 연구 요약"}]
    assert projection(cases, "test") == []


@pytest.mark.parametrize("text", [
    "| D01 | 질문 | 알 수 없음 | 답변 | 주의 |",
    "| D01 | 질문 | 문헌 | execution_failure | 주의 |",
    "| D01 | 질문 | 문헌 | 답변 | 주의 | extra |",
    "| D01 | 질문 | 문헌 | 답변 | 주의 |\n| D01 | 다른 질문 | 문헌 | 답변 | 주의 |",
])
def test_parse_rejects_invalid_labels_or_duplicate_ids(text):
    with pytest.raises(ValueError):
        parse_cases(text)


@pytest.mark.parametrize("route,expected", [("hybrid", "기록+문헌"), ("log_lookup", "기록"),
    ("log_metric", "기록"), ("literature_only", "문헌"), ("unsupported", "범위 밖")])
def test_exact_router_mapping(route, expected):
    result = score([label(route=expected)], [observation(route=route)])
    assert result["route_correct"] == 1


def test_ambiguous_channel_diagnostic_does_not_change_primary_route_score():
    case = observation(route="ambiguous", terminal="abstain_ready")
    case["question_interpretation"] = dict(personal_record_requested=True, literature_requested=True)
    result = score([label(route="기록+문헌", actions=["되묻기"])], [case])
    assert result["route_correct"] == 0
    assert result["channel_correct_diagnostic"] == 1
    assert result["action_correct"] == 1


@pytest.mark.parametrize("case,expected", [
    (observation(), "답변"),
    (observation(tools=[]), "answer_without_tools"),
    (observation(terminal="abstain_ready", metadata={"clarification_required": True}), "되묻기"),
    (observation(route="ambiguous", terminal="abstain_ready"), "되묻기"),
    (observation(route="unsupported", terminal="abstain_ready"), "보류"),
    (observation(terminal="execution_failure"), "execution_failure"),
    (observation(http=503), "HTTP/transport_failure"),
    (observation(http=None), "HTTP/transport_failure"),
])
def test_action_mapping_preserves_failure_and_clarification(case, expected):
    assert action(case) == expected


@pytest.mark.parametrize("terminal,metadata,failures,unjustified", [
    ("abstain_ready", None, 0, 1),
    ("abstain_ready", {"clarification_required": True}, 0, 1),
    ("execution_failure", None, 1, 0),
])
def test_failure_and_unjustified_counts_are_separate(terminal, metadata, failures, unjustified):
    result = score([label()], [observation(terminal=terminal, metadata=metadata)])
    assert result["action_correct"] == 0
    assert result["execution_failure"] == failures
    assert result["unjustified"] == unjustified


@pytest.mark.parametrize("change", ["id", "question", "missing", "duplicate"])
def test_scoring_rejects_incomplete_changed_or_repeated_observations(change):
    cases = [observation()]
    if change in {"id", "question"}:
        cases[0][change] = "different"
    elif change == "missing":
        cases = []
    else:
        cases *= 2
    with pytest.raises(ValueError):
        score([label()], cases)
