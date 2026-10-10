"""Predeclared length/correction comparisons and representation coverage audit."""

from .child_depth import adoption_decision, combined_metrics
from .retrieval_preparation import METRIC_ATOL
from .literature_query_freeze import sha256
import json

CONDITIONS = ('child_corrected', 'parent256', 'parent512')
SIMPLICITY = {'child_corrected': 0, 'parent256': 1, 'parent512': 2}


def verify_frozen_folder(folder):
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status') != 'frozen':
        raise ValueError('Input must be frozen')
    for item in manifest['artifacts']:
        path = folder/item['path']
        if not path.resolve().is_relative_to(folder.resolve()) or sha256(path) != item['sha256']:
            raise ValueError('Frozen input artifact changed')
    return manifest


def truncation_statistics(lengths, limit):
    if not lengths or any(type(n) is not int or n <= 0 for n in lengths):
        raise ValueError('Nonempty positive token lengths are required')
    if limit not in (128, 256, 512):
        raise ValueError('Undeclared input length')
    count = sum(n > limit for n in lengths)
    return dict(parent_count=len(lengths), max_seq_length=limit, truncated_parent_count=count,
                truncated_parent_ratio=count/len(lengths), total_tokens=sum(lengths),
                token_retention_ratio=sum(min(n, limit) for n in lengths)/sum(lengths),
                minimum=min(lengths), maximum=max(lengths), mean=sum(lengths)/len(lengths))


def select_condition(reference, candidates):
    if set(candidates) != set(CONDITIONS):
        raise ValueError('Exactly the three predeclared conditions are required')
    decisions = {name: dict(reference='step3b', **adoption_decision(reference, candidates[name]))
                 for name in CONDITIONS}
    eligible = [name for name in CONDITIONS if decisions[name]['adopted']]
    finalists = eligible.copy()
    trace = []
    for metric in ('complete_evidence@10', 'evidence_group_recall@10'):
        if len(finalists) < 2:
            break
        values = {name: combined_metrics(candidates[name])['macro'][metric] for name in finalists}
        best = max(values.values())
        finalists = [name for name in finalists if best-values[name] <= METRIC_ATOL]
        trace.append(dict(metric=metric, values=values, finalists=finalists.copy()))
    selected = max(finalists, key=SIMPLICITY.get) if finalists else 'step3b'
    return dict(decisions=decisions, eligible=eligible, tiebreak_trace=trace,
                simplicity_order=['parent512', 'parent256', 'child_corrected'], selected_condition=selected)
