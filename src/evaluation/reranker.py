"""Evaluation-only candidate coverage diagnosis and predeclared selection."""

from collections import Counter
import math
from statistics import mean, median

from .child_depth import adoption_decision, combined_metrics
from .retrieval_metrics import group_is_covered, required_literature_groups
from .retrieval_preparation import METRIC_ATOL
from src.retrieval.rrf import reciprocal_rank_fusion


def fused_candidate_ids(dense, bm25):
    """Use exactly the union and unchanged RRF order, including empty BM25."""
    ids = lambda rows:[h['chunk_id'] for h in rows]
    return [h.chunk_id for h in reciprocal_rank_fusion(ids(dense),ids(bm25),
            top_k=len(dense)+len(bm25)).hits]


def pool_evidence_upper_bound(cases, pools):
    """Candidate-set bound, not an assertion that ten slots attain it."""
    if set(pools) != {c['id'] for c in cases}:
        raise ValueError('Upper bound cases and pools must match')
    per_case = {}
    for case in cases:
        groups = required_literature_groups(case)
        if not groups:
            raise ValueError('Upper bound requires evidence groups')
        covered = sum(group_is_covered(g,pools[case['id']]) for g in groups)
        per_case[case['id']] = dict(evidence_group_recall=covered/len(groups),
                                  complete_evidence=float(covered==len(groups)))
    return dict(case_count=len(cases),per_case=per_case,macro={
        field:mean(row[field] for row in per_case.values()) for field in ('evidence_group_recall','complete_evidence')})


def latency_summary(values):
    numbers = list(values)
    if not numbers or any(not math.isfinite(v) or v < 0 for v in numbers):
        raise ValueError('Finite nonnegative per-case CPU latencies are required')
    ordered = sorted(numbers)
    return dict(case_count=len(numbers), mean_ms=mean(numbers), median_ms=median(numbers),
                p95_ms=ordered[math.ceil(.95*len(ordered))-1], max_ms=max(numbers))


def select_reranker(reference, conditions, latencies):
    if set(conditions) != {'A','B'} or set(latencies) != {'A','B'}:
        raise ValueError('Exactly A and B are required')
    decisions = {}
    for name in ('A','B'):
        if set(latencies[name]) != set(combined_metrics(conditions[name])['per_case']):
            raise ValueError('Latency measurements must cover every evaluated case exactly')
        decision = adoption_decision(reference,conditions[name])
        timing = latency_summary(latencies[name].values())
        decision['checks']['mean_cpu_latency_within_3s'] = timing['mean_ms'] <= 3000
        decision.update(adopted=all(decision['checks'].values()),latency=timing,reference='step3b')
        decisions[name] = decision
    eligible = [name for name in ('A','B') if decisions[name]['adopted']]
    finalists = eligible.copy()
    trace = []
    for metric in ('complete_evidence@10','evidence_group_recall@10'):
        if len(finalists) < 2:
            break
        values = {name:combined_metrics(conditions[name])['macro'][metric] for name in finalists}
        best = max(values.values())
        finalists = [name for name in finalists if best-values[name] <= METRIC_ATOL]
        trace.append(dict(metric=metric,values=values,finalists=finalists.copy()))
    return dict(decisions=decisions,eligible=eligible,tiebreak_trace=trace,
                selected_condition=('A' if 'A' in finalists else finalists[0]) if finalists else 'step3b')


def diagnose_missing_groups(cases, top10, pools_a, pools_b):
    """Exclusive a / b-only / c; match=all requires full pool coverage."""
    expected = {c['id'] for c in cases}
    if any(set(rows) != expected for rows in (top10,pools_a,pools_b)):
        raise ValueError('Diagnosis cases and rankings must match')
    per_case = {}
    for case in cases:
        cid = case['id']
        a,b = set(pools_a[cid]),set(pools_b[cid])
        if not a.issubset(b) or not set(top10[cid]).issubset(a):
            raise ValueError('Nested candidate pools and baseline membership are required')
        rows = []
        for index,group in enumerate(required_literature_groups(case)):
            if group_is_covered(group,top10[cid]):
                continue
            bucket = 'a' if group_is_covered(group,a) else 'b' if group_is_covered(group,b) else 'c'
            rows.append(dict(group_index=index, group_id=group.get('group_id',group.get('id')),
                             match=group['match'],category=bucket))
        per_case[cid] = rows
    counts = Counter(row['category'] for rows in per_case.values() for row in rows)
    return dict(case_count=len(cases), missing_case_count=sum(bool(v) for v in per_case.values()),
        missing_group_count=sum(counts.values()), per_case=per_case,
        categories={bucket:dict(group_count=counts[bucket],case_count=sum(
            any(row['category']==bucket for row in rows) for rows in per_case.values())) for bucket in ('a','b','c')},
        b_inclusive_group_count=counts['a']+counts['b'])


def summarize_token_audit(results):
    rows = [row for result in results for row in result['token_audit']]
    if not rows:
        raise ValueError('Token audit cannot be empty')
    parent_lengths = {}
    for row in rows:
        previous = parent_lengths.setdefault(row['chunk_id'],row['parent_tokens'])
        if previous != row['parent_tokens']:
            raise ValueError('Parent token count changed')
    ordered = sorted(row['pair_tokens'] for row in rows)
    return dict(candidate_pairs=len(rows), truncated_pairs=sum(r['truncated'] for r in rows),
        truncated_pair_ratio=sum(r['truncated'] for r in rows)/len(rows),
        unique_parents=len(parent_lengths), unique_parent_body_over512=sum(n>512 for n in parent_lengths.values()),
        unique_parent_body_over512_ratio=sum(n>512 for n in parent_lengths.values())/len(parent_lengths),
        pair_tokens=dict(minimum=min(ordered),mean=mean(ordered),median=median(ordered),
                        p95=ordered[math.ceil(.95*len(ordered))-1],maximum=max(ordered)),
        parent_token_retention_ratio=sum(r['retained_parent_tokens'] for r in rows)/sum(r['parent_tokens'] for r in rows))
