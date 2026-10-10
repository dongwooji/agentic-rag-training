"""Combined dev/dev-ko criteria declared before Step 4/5 ranking."""

from statistics import mean
from .retrieval_preparation import metric_changes, METRIC_ATOL


def combined_metrics(datasets):
    if set(datasets) != {'dev','dev-ko'}:
        raise ValueError('Both development datasets are required')
    per_case = {dataset+':'+cid: values for dataset,data in datasets.items()
                for cid,values in data['per_case'].items()}
    if not per_case or any(not data['per_case'] for data in datasets.values()):
        raise ValueError('Development metrics cannot be empty')
    names = list(next(iter(per_case.values())))
    if any(set(values)!=set(names) for values in per_case.values()):
        raise ValueError('Metric fields differ')
    return dict(case_count=len(per_case), per_case=per_case, macro={
        ('mrr' if name=='reciprocal_rank' else name):mean(v[name] for v in per_case.values()) for name in names})


def adoption_decision(reference, candidate):
    before,after=combined_metrics(reference),combined_metrics(candidate)
    if set(reference['dev']['per_case']) != set(reference['dev-ko']['per_case']):
        raise ValueError('Development datasets must be paired')
    changes=metric_changes(before,after)
    counts=changes['counts']['evidence_group_recall@10']
    checks=dict(combined_complete_non_decreasing=changes['macro']['complete_evidence@10']['delta'] >= -METRIC_ATOL,
                combined_more_improved_than_worsened=counts['improved'] > counts['worsened'])
    return dict(adopted=all(checks.values()),checks=checks,combined=changes,
                datasets={d:metric_changes(reference[d],candidate[d]) for d in ('dev','dev-ko')})
