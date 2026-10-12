"""Read-only diagnosis of saved rankings; no encoder, retrieval or LLM imports."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from .heldout_v2 import retrieval_metric_cases
from .retrieval_metrics import evaluate_retrieval, group_is_covered


CONDITIONS = ('H0', 'H1', 'step2', 'final_v2')
LATIN_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:1RM|[A-Za-z][A-Za-z0-9]*(?:[-'][A-Za-z0-9]+)*)(?![A-Za-z0-9])")
ALLOWED_ABBREVIATIONS_AND_UNITS = {'rir','rpe','1rm','e1rm','kg','lb','lbs','cm','mm','km','m','s','sec','min','ml','g'}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def verify_bundle(folder):
    manifest = read_json(folder/'manifest.json')
    if manifest.get('status') != 'frozen':
        raise ValueError('Expected frozen saved rankings/result')
    for ref in manifest['artifacts']:
        path = folder/ref['path']
        if not path.resolve().is_relative_to(folder.resolve()) or sha256(path) != ref['sha256']:
            raise ValueError('Frozen artifact changed')
    return manifest


def group_map(gold):
    result = {g['evidence_group_id']:g for g in gold['evidence_groups']}
    if len(result) != len(gold['evidence_groups']):
        raise ValueError('Duplicate evidence group IDs')
    return result


def required_ids(gold):
    return {key for key,g in group_map(gold).items() if g['required']}


def covered(group, ids):
    return group_is_covered(group['parent_mapping'], ids)


def section_type(section):
    """Classify the deepest explicit heading; retain the original heading too."""
    for heading in reversed(section.split('>')):
        text = heading.casefold().strip()
        if re.search(r'limitation|strengths? and weaknesses|한계', text):
            return 'limitations'
        if re.search(r'discussion|논의|고찰', text):
            return 'discussion'
        if re.search(r'results?|결과', text):
            return 'results'
        if re.search(r'method|participant|subject|statistical analys|study design|procedure|방법', text):
            return 'methods'
        if re.search(r'conclusion|결론', text):
            return 'conclusions'
        if re.search(r'introduction|background|서론', text):
            return 'introduction'
        if re.search(r'abstract|초록', text):
            return 'abstract'
    return 'other'


def language_statistics(cases):
    records = []
    for case in cases:
        tokens = LATIN_TOKEN.findall(case['question'])
        other = [t for t in tokens if t.casefold() not in ALLOWED_ABBREVIATIONS_AND_UNITS]
        records.append(dict(case_id=case['id'], latin_tokens=tokens,
                            category='english_notation_present' if tokens else 'no_latin_notation',
                            non_abbreviation_or_unit_tokens=other))
    return dict(case_count=len(records), english_notation_including_abbreviations=sum(bool(r['latin_tokens']) for r in records),
                pure_korean_no_latin=sum(not r['latin_tokens'] for r in records),
                terms_beyond_allowed_abbreviations_units=sum(bool(r['non_abbreviation_or_unit_tokens']) for r in records),
                pure_korean_allowing_declared_abbreviations_units=sum(not r['non_abbreviation_or_unit_tokens'] for r in records),
                rule='Original question Latin notation, including acronyms; numbers/punctuation do not count. Post-hoc descriptive only.',
                limitation='Mechanical notation count, not semantic language classification; no LLM.',
                allowed_abbreviations_units=sorted(ALLOWED_ABBREVIATIONS_AND_UNITS), per_case=records)


def gold_comparison(old, new, prefinal, rankings):
    original, current = required_ids(old), required_ids(new)
    prior = required_ids(prefinal)
    added = current-original
    prior_added = added & prior
    final_extra = added-prior
    result = dict(original_required_count=len(original), final_required_count=len(current),
                  net_change=len(current)-len(original), added_required_ids=sorted(added),
                  removed_required_ids=sorted(original-current), promoted_existing_optional_ids=sorted(added & set(group_map(old))),
                  prefinal_added_ids=sorted(prior_added), final_only_added_ids=sorted(final_extra), conditions={})
    groups = group_map(new)
    for name, ranked in rankings.items():
        subsets = {}
        for label, ids in dict(all_added=added,prefinal_added=prior_added,final_only_added=final_extra).items():
            rows = [dict(group_id=key,case_id=groups[key]['case_id'],
                         covered=covered(groups[key],ranked[groups[key]['case_id']])) for key in sorted(ids)]
            found = sum(r['covered'] for r in rows)
            subsets[label] = dict(required_group_count=len(rows), covered_count=found,
                                  group_recall=found/len(rows) if rows else None, groups=rows)
        result['conditions'][name] = subsets
    return result


def final_case_diagnosis(cases, gold, top10, dense10, partial_bm25):
    groups = group_map(gold)
    per_case = []
    counts = Counter()
    known_inside = 0
    for case in cases:
        cid = case['id']
        required = [g for g in groups.values() if g['case_id'] == cid and g['required']]
        rows = []
        known_pool = set(dense10[cid]) | set(partial_bm25[cid]) | set(top10[cid])
        for group in required:
            hit = covered(group,top10[cid])
            inside = covered(group,known_pool)
            typ = section_type(group['source']['section'])
            if not hit:
                counts[typ] += 1
                known_inside += int(inside)
            rows.append(dict(group_id=group['evidence_group_id'],found_top10=hit,
                             paragraph_type=typ,section=group['source']['section'],
                             paragraph_id=group['source'].get('paragraph_id'),
                             chunk_ids=group['parent_mapping']['chunk_ids'], match=group['parent_mapping']['match'],
                             candidate50_status=('known_inside_by_saved_prefix' if inside else 'unknown_not_saved'),
                             candidate50_basis='Saved Dense10 + visible BM25 prefix + fused Top10; no depth50 search'))
        found = sum(r['found_top10'] for r in rows)
        per_case.append(dict(case_id=cid,task_type=case['task_type'],required_group_count=len(required),
                             found_group_count=found, missed_group_count=len(required)-found,groups=rows))
    missing = sum(r['missed_group_count'] for r in per_case)
    return dict(per_case=per_case,required_group_count=sum(r['required_group_count'] for r in per_case),
                found_group_count=sum(r['found_group_count'] for r in per_case),missed_group_count=missing,
                missed_by_paragraph_type=dict(sorted(counts.items())),
                candidate50=dict(measured=False,reason='Held-out source ranks saved at depth10 only; fresh search prohibited',
                                 known_inside_by_saved_prefix=known_inside,unknown=missing-known_inside,
                                 confirmed_outside=None,upper_bound=None,
                                 note='Unknown does not mean outside or corpus coverage gap'))


def build_diagnosis(*, old_cases, final_cases, old_gold, final_gold, prefinal_gold,
                    rankings, dense10, index_ids, corpus_v1, corpus_v2, published_metrics):
    if set(rankings) != set(CONDITIONS):
        raise ValueError('Exactly the four saved conditions are required')
    old_by_id = {c['id']:c for c in old_cases['cases']}
    final_by_id = {c['id']:c for c in final_cases['cases']}
    if len(old_by_id) != len(old_cases['cases']) or len(final_by_id) != len(final_cases['cases']):
        raise ValueError('Duplicate cases')
    if set(old_by_id) != set(final_by_id) or any(old_by_id[cid]['question'] != final_by_id[cid]['question'] for cid in final_by_id):
        raise ValueError('Gold comparison must use identical original questions and IDs')
    ids = set(index_ids)
    if len(ids) != len(index_ids) or ids != {c['chunk_id'] for c in corpus_v2['chunks']}:
        raise ValueError('Frozen search index and corpus v2 membership differ')
    if corpus_v2['embedding_row_count'] != len(index_ids):
        raise ValueError('Frozen embedding matrix rows and IDs differ')
    ranks = {}
    for name,data in rankings.items():
        if set(data) != set(final_by_id):
            raise ValueError('Saved ranking membership differs')
        ranks[name] = {cid:[h['chunk_id'] for h in hits[:10]] for cid,hits in data.items()}
        if any(len(hits)>10 or len({h['chunk_id'] for h in hits})!=len(hits) or
               any(h['chunk_id'] not in ids for h in hits) for hits in data.values()):
            raise ValueError('Invalid saved ranking')
    required = [g for g in final_gold['evidence_groups'] if g['required']]
    links = [chunk for g in required for chunk in g['parent_mapping']['chunk_ids']]
    missing = sorted(set(links)-ids)
    metrics, metric_ids = {}, {}
    for version,cases,gold in (('v1',old_cases,old_gold),('v2_1_final',final_cases,final_gold)):
        adapted,excluded = retrieval_metric_cases(cases,gold)
        metric_ids[version] = {c['id'] for c in adapted}
        metrics[version] = {name:evaluate_retrieval(adapted,r,ks=(10,)) for name,r in ranks.items()}
    if metric_ids['v1'] != metric_ids['v2_1_final']:
        raise ValueError('Gold versions have different metric denominators')
    if metrics['v2_1_final'] != published_metrics:
        raise ValueError('v2.1 rescoring must reproduce PR #10 exactly')
    chosen = [c for c in final_cases['cases'] if c['id'] in metric_ids['v2_1_final']]
    saved_dense = {cid:[h['chunk_id'] for h in hits] for cid,hits in dense10.items()}
    if set(saved_dense) != set(final_by_id) or any(len(v)>10 or not set(v).issubset(ids) for v in saved_dense.values()):
        raise ValueError('Expected saved Dense10 source ranks')
    partial_bm25 = {cid:[h['chunk_id'] for h in hits if h.get('bm25_rank') is not None]
                    for cid,hits in rankings['final_v2'].items()}
    diagnosis = final_case_diagnosis(chosen,final_gold,ranks['final_v2'],saved_dense,partial_bm25)
    return dict(kind='diagnosis_only',search_calls=0,llm_calls=0,database_calls=0,settings_modified=False,
                integrity=dict(required_group_count=len(required),required_chunk_links=len(links),
                               unique_required_chunk_ids=len(set(links)),missing_required_chunk_ids=missing,
                               all_required_ids_in_index=not missing,index_parent_count=len(ids)),
                corpus_counts={name:dict(paper_count=len(corpus['papers']),chunk_count=len(corpus['chunks']),
                                        papers_with_chunks=len({c['paper_id'] for c in corpus['chunks']}))
                               for name,corpus in (('v1',corpus_v1),('v2',corpus_v2))},
                metric_case_count=len(chosen),metrics=metrics,
                gold_change=gold_comparison(old_gold,final_gold,prefinal_gold,ranks),
                final_v2=diagnosis,language=language_statistics(chosen),
                warning='Gold version comparison includes additions AND changed semantic claims/mappings; not a causal isolation of count alone')
