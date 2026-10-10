"""Predeclared Step 2/3 comparisons; development diagnostics, never runtime."""

from .retrieval_preparation import METRIC_ATOL, metric_changes


def adoption_decision(reference, candidate):
    changes = {dataset: metric_changes(reference[dataset], candidate[dataset])
               for dataset in ('dev', 'dev-ko')}
    dev = changes['dev']
    checks = {
        'dev_complete_non_decreasing': dev['macro']['complete_evidence@10']['delta'] >= -METRIC_ATOL,
        'dev_more_improved_than_worsened': (
            dev['counts']['evidence_group_recall@10']['improved'] >
            dev['counts']['evidence_group_recall@10']['worsened']),
        'dev_ko_non_decreasing': all(changes['dev-ko']['macro'][key]['delta'] >= -METRIC_ATOL
                                    for key in ('evidence_group_recall@10', 'complete_evidence@10',
                                                'mrr')),
    }
    return dict(adopted=all(checks.values()), checks=checks, changes=changes)


def cumulative_decisions(metrics):
    """Rejected candidates never become references; order cannot be selected later."""
    adopted = 'H1'
    decisions = {}
    for condition in ('step2', 'step3a', 'step3b'):
        decision = adoption_decision(metrics[adopted], metrics[condition])
        decision['reference'] = adopted
        decisions[condition] = decision
        if decision['adopted']:
            adopted = condition
    return dict(decisions=decisions, selected_condition=adopted)
