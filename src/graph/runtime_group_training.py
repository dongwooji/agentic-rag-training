"""Joint lookup only for approved groups; preserve stored rows and identities."""

from dataclasses import replace
import json

from src.tools.training_log import TrainingLogOperation
from src.tools.contracts import ToolError, ToolErrorCode, failure_response, success_response


class RuntimeGroupTrainingTool:
    def __init__(self, tool, relations, *, for_metrics=False, selected_members=None):
        self.tool, self.relations, self.for_metrics = tool, relations, for_metrics
        self.selected_members = selected_members

    def execute(self, request):
        group = self.relations.group_for(request.canonical_exercise_name)
        if self.selected_members is not None:
            if len(self.selected_members) == 1:
                return self.tool.execute(request)
            if not group or set(self.selected_members) != set(group["members"]):
                return failure_response(operation=request.operation,
                    error=ToolError(ToolErrorCode.INVALID_INPUT, "승인되지 않은 공동 조회입니다."), provenance={})
        if not group:
            return self.tool.execute(request)
        operation = TrainingLogOperation(request.operation).value
        provenance = {**self.relations.provenance(), "exercise_group_id": group["id"],
                      "stored_exercise_names": group["members"], "member_results": []}

        def fail(message):
            return failure_response(operation=operation, error=ToolError(ToolErrorCode.DATA_INTEGRITY_ERROR, message),
                                    provenance=provenance)

        if operation not in {"exercise_records", "list_sessions"}:
            return fail("이 운동 그룹의 조회 작업을 지원하지 않습니다.")
        try:
            rows, seen_ids, signatures = [], {}, {}
            for member in group["members"]:
                response = self.tool.execute(replace(request, operation="exercise_records", canonical_exercise_name=member))
                payload = response.to_dict() if hasattr(response, "to_dict") else response
                provenance["member_results"].append({"stored_exercise_name": member, "status": payload["status"],
                                                       "provenance": payload.get("provenance", {})})
                if not payload.get("success"):
                    return fail("운동 그룹의 일부 저장 이름 조회가 실패했습니다. 부분 결과로 계산하지 않습니다.")
                result = payload.get("result") or {}
                if result.get("truncated_at_limit"):
                    return fail("운동 그룹 조회 한도에 도달했습니다. 기간을 좁혀주세요.")
                member_rows = result.get("records")
                if not isinstance(member_rows, list):
                    return fail("운동 그룹 조회 결과의 기록 구조를 확인할 수 없습니다.")
                for item in member_rows:
                    row = dict(item)
                    if not all(key in row for key in ("set_id", "session_id", "started_at", "set_order", "weight", "reps")):
                        return fail("운동 그룹의 set/session 식별 정보를 확인할 수 없습니다.")
                    identifier = row["set_id"]
                    if identifier in seen_ids:
                        if seen_ids[identifier] != row:
                            return fail("동일 set ID의 기록 내용이 충돌합니다.")
                        continue
                    seen_ids[identifier] = dict(row)
                    # Same values across labels are suspected duplicates, not
                    # permission to erase possibly distinct performed sets.
                    fields = ("session_id", "started_at", "workout_name", "set_order", "weight", "weight_unit",
                              "reps", "distance", "distance_unit", "seconds", "notes", "workout_notes")
                    signature = json.dumps([row.get(key) for key in fields], ensure_ascii=False, default=str)
                    signatures.setdefault(signature, set()).add(member)
                    row["stored_exercise_name"] = row.get("exercise_name", member)
                    row["exercise_name"] = group["canonical_label"]
                    rows.append(row)
            overlap = sum(len(names) > 1 for names in signatures.values())
            provenance["cross_label_overlap_signatures"] = overlap
            provenance["overlap_policy"] = group["overlap_policy"]
            if overlap and self.for_metrics:
                return fail("운동 이름 사이에 값이 같은 기록이 있어 중복 여부 확인 전 Metric을 계산하지 않습니다.")
            if len(rows) >= request.limit:
                return fail("운동 그룹 전체 조회 한도에 도달했습니다. 기간을 좁혀주세요.")
            rows.sort(key=lambda row: (str(row["started_at"]), str(row["session_id"]), row["set_order"], str(row["set_id"])))
            return success_response(operation=operation,
                result={"canonical_exercise_name": group["canonical_label"], "records": rows,
                        "record_count": len(rows), "stored_record_count": len(rows),
                        "cross_label_overlap_signatures": overlap, "truncated_at_limit": False,
                        "date_range": {"start_date": request.start_date, "end_date": request.end_date}},
                provenance=provenance, empty=not rows,
                limitations=("승인된 두 저장 이름을 함께 조회한 기록입니다. 운동 이름별 원문과 set ID를 보존합니다.",
                             "저장 기록 수는 실제 수행 세트 수로 확정한 값이 아닙니다. 이름 간 값이 같은 기록은 삭제하지 않았습니다.",
                             "Weight uses the source's unverified unit label."))
        except Exception:
            return fail("운동 그룹 조회 또는 결과 검증에 실패했습니다.")
