"""Label-free execution helpers and separate deterministic offline scoring."""
from __future__ import annotations

from copy import deepcopy
from statistics import mean
from time import perf_counter

from src.graph.nodes import WorkflowNodes
from src.evaluation.retrieval_metrics import evidence_group_recall_at_k, complete_evidence_at_k


BOUNDED_STOPS = {"duplicate_query", "budget_exhausted"}


def initial_state(question, literature_question, query, hits, *, validation_status="passed"):
    """Runtime receives text/evidence only, never case IDs, Gold or labels."""
    return dict(original_question=question, literature_subquestion=literature_question,
        task_type="literature_only", selected_tools=["search_literature"],
        first_query_selection={"step2":{"actual_query":literature_question,"validation_status":validation_status}},
        initial_query=query,current_query=query,query_history=[query],
        literature_evidence=deepcopy(hits),fused_evidence=deepcopy(hits),recovery_evidence=[],
        fusion_history=[],grader_history=[],recovery_history=[],tool_results=[],
        retry_count=0,max_retry=2,errors=[])


def nodes(grader, recovery, literature):
    return WorkflowNodes(router=None,tool_executor=None,literature_tool=literature,
                         runtime_grader=grader,recovery_agent=recovery)


def first_assessment(node, state):
    result=deepcopy(state);started=perf_counter()
    result.update(node.grade_evidence(result))
    return result,(perf_counter()-started)*1000


def finish_assessment(node, first, *, recovery_enabled):
    """Same runtime Recovery/search/fusion/grade nodes; never final answer generation."""
    state=deepcopy(first);started=perf_counter()
    if recovery_enabled:
        while state.get("final_status") not in {"answer_ready","abstain_ready","execution_failure"}:
            for operation in [node.generate_recovery_query,node.search_recovery_literature,node.fuse_evidence,node.grade_evidence]:
                state.update(operation(state))
                if state.get("final_status") in {"answer_ready","abstain_ready","execution_failure"}:
                    break
    grade=state.get("grader_result") or {}
    behavior=("answer_ready" if grade.get("evidence_sufficient") else "abstain_ready") if grade.get("execution_status")=="completed" else "execution_failure"
    execution_failure=behavior=="execution_failure" or any(e.get("code") not in BOUNDED_STOPS for e in state.get("errors",[]))
    return dict(state=state,final_behavior=behavior,execution_failure=execution_failure,
                continuation_latency_ms=(perf_counter()-started)*1000)


def score_pair(gold, before, after):
    """Call only after runtime state/results have been saved."""
    def score(ids):
        return dict(egr10=evidence_group_recall_at_k(gold,ids,10),ce10=complete_evidence_at_k(gold,ids,10))
    return dict(initial=score(before),final=score(after))


def grader_disagreements(rows, key):
    a=b=failures=0
    for row in rows:
        condition=row[key]
        if condition["execution_failure"]:
            failures+=1
        grade=condition["grade"]
        if grade.get("execution_status")!="completed":continue
        sufficient=grade.get("evidence_sufficient") is True
        a+=int(row["initial_ce10"]==1 and not sufficient)
        b+=int(row["initial_ce10"]==0 and sufficient)
    return dict(A=a,B=b,total=a+b,execution_failures=failures,inputs=len(rows))


def adoption(rows):
    old,new=grader_disagreements(rows,"old"),grader_disagreements(rows,"new_off")
    accepted=(new["total"]<old["total"] and new["A"]<=old["A"] and new["B"]<=old["B"]
              and new["execution_failures"]<=old["execution_failures"])
    ce_off=mean(r["new_off"]["final_ce10"] for r in rows)
    ce_on=mean(r["new_on"]["final_ce10"] for r in rows)
    fail_off=sum(r["new_off"]["execution_failure"] for r in rows)
    fail_on=sum(r["new_on"]["execution_failure"] for r in rows)
    recovery_accepted=ce_on+1e-12>=ce_off and fail_on<=fail_off
    return dict(old=old,new=new,grader_adopted=accepted,
        recovery=dict(ce_off=ce_off,ce_on=ce_on,execution_failures_off=fail_off,
                      execution_failures_on=fail_on,adopted=recovery_accepted),
        selected_grader="fixed_requirements_v1" if accepted else "legacy",
        selected_recovery_enabled=recovery_accepted if accepted else True)
