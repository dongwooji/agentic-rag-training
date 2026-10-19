"""Public questions are code-owned; never echo model validation reasoning."""


def clarification_questions(interpretation):
    messages = []
    for issue in interpretation.unresolved_fields:
        field = issue.field
        if field in {"question", "evidence_need"} and interpretation.phase_a_semantics is not None:
            text = "기록 확인, 일반적인 설명, 평가나 조언 중 어떤 답변을 원하시나요?"
        elif field in {"time_condition", "start_date", "end_date", "period", "analysis_window"}:
            text = "분석 기간: 어느 기간을 볼까요? 예: 2018년 9월~12월"
        elif field in {"canonical_exercise_name", "candidate_exercises", "exercise_resolution_status", "exercise"}:
            text = "어떤 운동 기록을 볼까요? 운동명과 장비·그립을 알려주세요."
        elif field == "n_sessions":
            text = "처음과 최근 각각 몇 세션을 비교할까요?"
        elif field == "comparison":
            text = "어떤 대상이나 구간을 서로 비교할까요?"
        elif field == "requested_analyses":
            if len(interpretation.requested_analyses) > 1:
                text = "한 번에 하나의 지표를 계산할 수 있습니다. 먼저 확인할 지표를 선택해주세요."
            else:
                text = "어떤 지표를 계산하거나 확인할까요?"
        elif field in {"literature_requested", "literature_subquestion"}:
            text = "문헌에서 어떤 내용을 확인할까요?"
        else:
            text = "확인할 운동·기간·지표를 구체적으로 알려주세요."
        if text not in messages:
            messages.append(text)
    return messages
