"""Public questions are code-owned; never echo model validation reasoning."""


def clarification_questions(interpretation):
    messages = []
    for issue in interpretation.unresolved_fields:
        field = issue.field
        if field in {"time_condition", "start_date", "end_date", "period", "analysis_window"}:
            text = "분석 기간: 어느 기간을 볼까요? 예: 2018년 9월~12월"
        elif field in {"canonical_exercise_name", "candidate_exercises", "exercise_resolution_status", "exercise"}:
            text = "어떤 운동 기록을 볼까요? 운동명과 장비·그립을 알려주세요."
        elif field == "n_sessions":
            text = "처음과 최근 각각 몇 세션을 비교할까요?"
        elif field in {"requested_analyses", "comparison"}:
            text = "어떤 지표를 확인할까요? 한 번에 하나의 지표를 선택해주세요."
        elif field in {"literature_requested", "literature_subquestion"}:
            text = "문헌에서 어떤 내용을 확인할까요?"
        else:
            text = "확인할 운동·기간·지표를 구체적으로 알려주세요."
        if text not in messages:
            messages.append(text)
    return messages
