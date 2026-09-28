from src.answer.generator import FinalResponseLayer

from answer_test_support import answer_state


def test_abstention_names_missing_component_and_recovery_attempts() -> None:
    state = answer_state(structured=True, literature=True)
    state.update(
        {
            "final_status": "abstain_ready",
            "retry_count": 2,
            "missing_components": [
                {"component_id": "C2", "requirement": "훈련된 여성의 정량적 효과"}
            ],
        }
    )
    result = FinalResponseLayer().respond(state)

    assert result.final_status == "abstain_ready"
    assert "개인 운동 기록" in result.answer_text
    assert "검색된 문헌 근거" in result.answer_text
    assert "훈련된 여성의 정량적 효과" in result.answer_text
    assert "추가 검색을 2회" in result.answer_text
    assert "답을 확정할 수 없습니다" in result.answer_text
    assert result.error is None


def test_execution_failure_hides_internal_details_from_user_message() -> None:
    state = answer_state()
    state.update(
        {
            "final_status": "execution_failure",
            "errors": [
                {
                    "stage": "tool",
                    "code": "database_error",
                    "message": "password=very-secret stack trace line 42",
                }
            ],
        }
    )
    result = FinalResponseLayer().respond(state)

    assert result.final_status == "execution_failure"
    assert "very-secret" not in result.answer_text
    assert "stack trace" not in result.answer_text
    assert result.error is not None
    assert result.provenance.internal_graph_errors == state["errors"]


def test_router_unsupported_abstention_does_not_claim_recovery() -> None:
    state = answer_state()
    state.update(
        {
            "final_status": "abstain_ready",
            "route": {
                "status": "unsupported",
                "unsupported_reason": "질문에 필요한 데이터 필드가 저장되어 있지 않습니다.",
            },
        }
    )
    result = FinalResponseLayer().respond(state)
    assert "질문에 필요한 데이터 필드" in result.answer_text
    assert "추가 검색" not in result.answer_text
