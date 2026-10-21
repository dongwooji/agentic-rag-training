"""Write-once final configuration, query preparation and held-out evaluation.

Runtime query generation receives only text. Gold is opened by the final
evaluation phase after all four ranking files have been persisted.
"""
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np

from src.retrieval.bm25 import BM25Index
from src.retrieval.hybrid import FrozenHybridRetriever, load_frozen_literature_assets
from src.retrieval.literature_query import generate_korean, translate_english
from src.retrieval.runtime_config import RetrievalConfig, load_retrieval_config
from .heldout_v2 import retrieval_metric_cases, validate_dataset
from .literature_query_freeze import sha256, usage_summary, write_new_json
from .retrieval_preparation import MemoryCosineStore, evaluate_rankings, metric_changes


GOLD_VERSION = 'heldout_v2_v1_1_final'
GOLD_SHA256 = '7c491a55e96b70b26b10ce7625c535e1dc521b4d83ab9d4e984d5d2c84df4b42'
CONDITIONS = dict(H0=('retrieval_h0_v1', 'original_question'),
                  H1=('retrieval_h1_v1', 'original_question'),
                  step2=('retrieval_step2_generation_v1', 'generated_ko'),
                  final_v2=('retrieval_step3b_translation_v1', 'translated_both'))
QUERY_SOURCES = ('config/literature_query_generation_v1.md', 'config/literature_query_translation_v1.md',
                 'config/literature_query_model_v1.json', 'src/retrieval/literature_query.py',
                 'src/retrieval/query_provider.py')
EXECUTION_SOURCES = QUERY_SOURCES + (
    'src/retrieval/runtime_config.py', 'src/retrieval/hybrid.py', 'src/retrieval/dense.py',
    'src/retrieval/bm25.py', 'src/retrieval/rrf.py', 'src/evaluation/retrieval_metrics.py',
    'src/evaluation/heldout_v2.py', 'src/evaluation/retrieval_preparation.py',
    'src/evaluation/literature_query_freeze.py', 'src/evaluation/retrieval_final.py',
    'src/tools/literature.py', 'src/llm_support.py',
    'scripts/run_retrieval_final.py', 'docs/EVALUATION.md')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def reference(root, path):
    return dict(path=path.relative_to(root).as_posix(), sha256=sha256(path))


def verify_reference(root, ref):
    path = root / ref['path']
    if not path.resolve().is_relative_to(root.resolve()) or sha256(path) != ref['sha256']:
        raise ValueError('Frozen reference changed: ' + ref['path'])
    return path


def freeze_bundle(folder, **metadata):
    manifest = dict(status='frozen', version=folder.name, frozen_at_utc=datetime.now(timezone.utc).isoformat(),
                    **metadata, artifacts=[dict(path=p.relative_to(folder).as_posix(), sha256=sha256(p))
                                          for p in sorted(folder.rglob('*')) if p.is_file()])
    write_new_json(folder/'manifest.json', manifest)
    return manifest


def verify_bundle(folder):
    manifest = read_json(folder/'manifest.json')
    if manifest['status'] != 'frozen':
        raise ValueError('Bundle is not frozen')
    for ref in manifest['artifacts']:
        verify_reference(folder.resolve(), ref)
    return manifest


def verified_configuration(root, folder):
    manifest = verify_bundle(folder)
    if manifest.get('stage') != 'configuration':
        raise ValueError('Expected frozen final configuration')
    verify_reference(root, manifest['public_manifest'])
    protocol = read_json(folder/'protocol.json')
    for ref in protocol['references'].values():
        verify_reference(root, ref)
    configs = {key: load_retrieval_config(verify_reference(root, ref))
               for key, ref in protocol['configs'].items()}
    if set(configs) != set(CONDITIONS):
        raise ValueError('Final configuration conditions differ')
    return protocol, configs, manifest


def freeze_configuration(root, output):
    """Pin settings before opening evaluation cases or Gold semantics."""
    if output.exists():
        raise FileExistsError('Final configuration already exists; do not overwrite')
    build = root/'data/literature/v2/build_freeze_v1/build_manifest.json'
    build_data = read_json(build)
    if build_data['status'] not in ('FROZEN_BUILD', 'BUILD_FROZEN'):
        raise ValueError('Corpus v2 build is not frozen')
    chunks = verify_reference(root, build_data['output_files']['chunks'])
    gold_dir = root/'data/evaluation'/GOLD_VERSION
    if sha256(gold_dir/'gold_evidence.json') != GOLD_SHA256:
        raise ValueError('Approved Gold hash differs')
    paths = {name:root/'config'/f'retrieval_{name}_corpus_v2_v1.json' for name in CONDITIONS}
    if any(p.exists() for p in paths.values()):
        raise FileExistsError('Configuration version exists')
    configs = {}
    for name, (base, mode) in CONDITIONS.items():
        data = load_retrieval_config(root/'config'/(base+'.json')).model_dump(mode='json')
        data.update(schema_version=4, config_version=paths[name].stem, retrieval_version='retrieval_v2_final_v1',
                    corpus_version=build_data['corpus_version'],
                    embedding_run_id='dense_minilm128_literature_corpus_v2_final_v1', query_mode=mode,
                    corpus_paths=dict(chunks=chunks.relative_to(root).as_posix(), manifest=build.relative_to(root).as_posix()),
                    validation=dict(corpus_chunks_sha256=sha256(chunks), corpus_manifest_sha256=sha256(build),
                                    expected_chunk_count=build_data['chunk_count']))
        configs[name] = RetrievalConfig.model_validate(data).model_dump(mode='json')
    output.mkdir(parents=True)
    for name, data in configs.items():
        write_new_json(paths[name], data)
    refs = {p:reference(root, root/p) for p in EXECUTION_SOURCES}
    for name, (base, _) in CONDITIONS.items():
        refs['historical_'+name] = reference(root, root/'config'/(base+'.json'))
    refs.update(cases=reference(root, gold_dir/'evaluation_cases.json'),
                gold=reference(root, gold_dir/'gold_evidence.json'),
                corpus_manifest=reference(root, build),
                corpus_integrity=reference(root, build.parent/'freeze_integrity.json'),
                scope=reference(root, root/'data/literature/v2/manifests/selection_freeze_v1/supported_scope.json'))
    protocol = dict(version='retrieval_v2_final_v1', gold_version=GOLD_VERSION, expected_case_count=24,
                    configs={name:reference(root, path) for name,path in paths.items()},
                    definitions=configs, references=refs, query_sources=list(QUERY_SOURCES),
                    query_input='original_question_only', query_review='no_human_review_or_modification',
                    retries=0, calls_per_case_per_stage=1, ranking_runs_per_condition=1,
                    gold_opened_only_after_all_rankings=True, service_default='legacy/H0',
                    retrieval_backend='memory_exact_cosine', pgvector_reproduction='separate_follow_up',
                    grouping_fields=['task_type','scope_class','question_family_id','language_group','language','question_language'],
                    language_group_rule='Also report existing scalar case fields containing language or terminology; never infer labels from question text',
                    abstention_correctness='not_measured_retrieval_only',
                    selection_origin='Step 3b post-hoc user decision; not retrospective preregistered adoption')
    write_new_json(output/'protocol.json', protocol)
    freeze_bundle(output, stage='configuration', corpus=build_data['corpus_version'], gold_version=GOLD_VERSION,
                  final_config_sha256=sha256(paths['final_v2']), settings_frozen_before_queries=True,
                  public_manifest=reference(root,output/'protocol.json'))
    return protocol


def freeze_queries(root, configuration, output, stage, provider, generation=None):
    if stage not in ('generation','translation'):
        raise ValueError('Invalid query stage')
    protocol, _, _ = verified_configuration(root, configuration)
    if output.exists():
        raise FileExistsError('Query stage exists; no repeat or overwrite')
    if stage == 'generation':
        cases = read_json(verify_reference(root, protocol['references']['cases']))['cases']
        # Projection excludes classification, log data, Gold and evidence.
        inputs = [dict(case_id=c['id'], original_question=c['question']) for c in cases]
        input_hash = protocol['references']['cases']['sha256']
    else:
        if generation is None:
            raise ValueError('Frozen generation input is required')
        gm = verify_bundle(generation)
        if gm.get('stage') != 'generation' or gm.get('configuration_sha256') != sha256(configuration/'manifest.json'):
            raise ValueError('Generation belongs to a different configuration')
        inputs = [json.loads(line) for line in (generation/'queries.jsonl').read_text(encoding='utf-8').splitlines()]
        input_hash = sha256(generation/'manifest.json')
    if len(inputs) != protocol['expected_case_count'] or len({r['case_id'] for r in inputs}) != len(inputs):
        raise ValueError('Query case count/IDs differ')
    output.mkdir(parents=True)
    records = []
    with (output/'queries.jsonl').open('x', encoding='utf-8') as handle:
        for source in inputs:
            verified_configuration(root, configuration)
            if stage == 'translation':
                verify_bundle(generation)
            text = source['original_question'] if stage == 'generation' else source['result']['actual_query']
            result = generate_korean(provider, text) if stage == 'generation' else translate_english(provider, text)
            record = dict(case_id=source['case_id'], original_question=source['original_question'], result=result)
            if stage == 'translation':
                record['step2'] = source['result']
            handle.write(json.dumps(record, ensure_ascii=False)+'\n')
            handle.flush()
            records.append(record)
    verified_configuration(root, configuration)
    write_new_json(output/'summary.json', usage_summary(records))
    freeze_bundle(output, stage=stage, configuration_sha256=sha256(configuration/'manifest.json'),
                  input_sha256=input_hash, case_count=len(records), query_review='no_human_review',
                  retrieval_executed=False, gold_semantics_used=False)
    return records


def frozen_query_conditions(root, configuration, generation, translation):
    protocol, configs, _ = verified_configuration(root, configuration)
    gm, tm = verify_bundle(generation), verify_bundle(translation)
    config_hash = sha256(configuration/'manifest.json')
    if (gm.get('stage') != 'generation' or tm.get('stage') != 'translation'
            or gm['configuration_sha256'] != config_hash or tm['configuration_sha256'] != config_hash
            or gm['input_sha256'] != protocol['references']['cases']['sha256']
            or tm['input_sha256'] != sha256(generation/'manifest.json')):
        raise ValueError('Query freezes are not linked to this protocol')
    read_rows = lambda p:[json.loads(line) for line in (p/'queries.jsonl').read_text(encoding='utf-8').splitlines()]
    ko, en = read_rows(generation), read_rows(translation)
    questions = []
    cases = read_json(verify_reference(root, protocol['references']['cases']))['cases']
    if len(ko) != protocol['expected_case_count'] or len(en) != len(ko) or len(cases) != len(ko):
        raise ValueError('Frozen query membership differs')
    for c, k, e in zip(cases, ko, en, strict=True):
        if (c['id'] != k['case_id'] or k['case_id'] != e['case_id'] or c['question'] != k['original_question']
                or k['original_question'] != e['original_question'] or e['step2'] != k['result']
                or e['result']['input_query'] != k['result']['actual_query']):
            raise ValueError('Frozen translation input differs from actual Step 2 query')
        questions.append(dict(case_id=k['case_id'], H0=k['original_question'], H1=k['original_question'],
                              step2=k['result']['actual_query'], final_v2=e['result']['actual_query']))
    return questions, protocol, configs


def rank_final(root, configuration, generation, translation, output, encoder):
    """Persist all condition rankings and index definitions before any Gold read."""
    if output.exists():
        raise FileExistsError('Ranking run already exists; never repeat')
    questions, protocol, configs = frozen_query_conditions(root, configuration, generation, translation)
    assets = {name:load_frozen_literature_assets(root, config=cfg) for name,cfg in configs.items()}
    first = assets['final_v2']
    output.mkdir(parents=True)
    write_new_json(output/'inputs.json', dict(configuration_sha256=sha256(configuration/'manifest.json'),
                   generation_sha256=sha256(generation/'manifest.json'), translation_sha256=sha256(translation/'manifest.json'),
                   queries=questions, gold_semantics_read=False))
    vectors = encoder.encode([c['text'] for c in first.chunks])
    with (output/'parent_embeddings.npy').open('xb') as handle:
        np.save(handle, vectors, allow_pickle=False)
    ids = [c['chunk_id'] for c in first.chunks]
    write_new_json(output/'parent_chunk_ids.json', ids)
    indexes = {}
    for name, cfg in configs.items():
        if assets[name].corpus_chunks_sha256 != first.corpus_chunks_sha256 or cfg.dense != configs['final_v2'].dense:
            raise ValueError('Condition parent representation differs')
        store = MemoryCosineStore(ids, vectors)
        retriever = FrozenHybridRetriever(assets=assets[name], encoder=encoder, vector_store=store)
        rankings, latency = {}, {}
        for row in questions:
            response = retriever.search(row[name], top_k=10)
            rankings[row['case_id']] = [asdict(hit) for hit in response.hits]
            latency[row['case_id']] = dict(response.latency_ms)
        write_new_json(output/(name+'_rankings.json'), rankings)
        write_new_json(output/(name+'_latency.json'), latency)
        write_new_json(output/(name+'_dense.json'), {r['case_id']:v for r,v in zip(questions,store.searches,strict=True)})
        metadata = assets[name].bm25_metadata
        write_new_json(output/(name+'_bm25_index.json'), metadata)
        indexes[name] = dict(config=cfg.model_dump(mode='json'), config_sha256=protocol['configs'][name]['sha256'],
                             parent_embedding='parent_embeddings.npy', parent_embedding_sha256=sha256(output/'parent_embeddings.npy'),
                             chunk_ids_sha256=sha256(output/'parent_chunk_ids.json'), bm25_metadata=metadata,
                             bm25_policy=cfg.bm25.score_policy, query_mode=cfg.query_mode)
        print(f'{name}: saved {len(rankings)} rankings', flush=True)
    verified_configuration(root, configuration)
    verify_bundle(generation)
    verify_bundle(translation)
    write_new_json(output/'indexes.json', dict(conditions=indexes, encoder=asdict(encoder.metadata),
                   corpus_sha256=first.corpus_chunks_sha256, database_used=False, parent_count=len(ids)))
    freeze_bundle(output, stage='rankings', gold_semantics_read=False, calls_per_condition=24,
                  configuration_sha256=sha256(configuration/'manifest.json'),
                  generation_sha256=sha256(generation/'manifest.json'), translation_sha256=sha256(translation/'manifest.json'))


def grouped_metrics(cases, metric_cases, rankings, fields):
    metrics_by_id = {c['id']:c for c in metric_cases}
    grouped = {}
    explicit_language = {key for c in cases for key,value in c.items()
                         if ('language' in key or 'terminology' in key) and isinstance(value,(str,bool,int,float))}
    for field in sorted(set(fields) | explicit_language):
        values = sorted({str(c[field]) for c in cases if field in c})
        if not values:
            continue
        grouped[field] = {}
        for value in values:
            selected = [c for c in cases if str(c.get(field)) == value]
            eligible = [metrics_by_id[c['id']] for c in selected if c['id'] in metrics_by_id]
            grouped[field][value] = dict(total_case_count=len(selected), retrieval_eligible_count=len(eligible),
                                        metrics=evaluate_rankings(eligible, rankings))
    return grouped


def evaluate_final(root, configuration, rankings_folder, output):
    """Evaluation phase only: verify saved ranks, THEN open Gold and labels."""
    if output.exists():
        raise FileExistsError('Evaluation already exists; never overwrite')
    protocol, configs, _ = verified_configuration(root, configuration)
    rm = verify_bundle(rankings_folder)
    if rm.get('stage') != 'rankings' or rm.get('configuration_sha256') != sha256(configuration/'manifest.json'):
        raise ValueError('Rankings belong to a different configuration')
    # Gold semantics are not read until every condition's ranking exists and passes integrity.
    ranked = {name:read_json(rankings_folder/(name+'_rankings.json')) for name in CONDITIONS}
    cases = read_json(verify_reference(root, protocol['references']['cases']))
    gold = read_json(verify_reference(root, protocol['references']['gold']))
    build = read_json(verify_reference(root, protocol['references']['corpus_manifest']))
    tables = {name:[json.loads(line) for line in verify_reference(root,ref).read_text(encoding='utf-8').splitlines()]
              for name,ref in build['output_files'].items()}
    scope = read_json(verify_reference(root,protocol['references']['scope']))
    validation = validate_dataset(cases, gold, scope, tables['papers'], tables['paragraphs'], tables['chunks'])
    adapted, excluded = retrieval_metric_cases(cases, gold)
    case_ids = {c['id'] for c in cases['cases']}
    if any(set(r) != case_ids for r in ranked.values()):
        raise ValueError('Saved ranking case set differs')
    metrics = {name:evaluate_rankings(adapted, r) for name,r in ranked.items()}
    groups = {name:grouped_metrics(cases['cases'], adapted, r, protocol['grouping_fields']) for name,r in ranked.items()}
    boundary = [c for c in cases['cases'] if c['task_type'] == 'boundary']
    result = dict(version=output.name, metrics=metrics, grouped_metrics=groups, validation=validation,
                  total_case_count=len(case_ids), retrieval_metric_case_count=len(adapted), excluded_case_ids=excluded,
                  boundary=dict(case_count=len(boundary), case_ids=[c['id'] for c in boundary],
                                expected_behavior_counts=dict(Counter(c['expected_behavior'] for c in boundary)),
                                retrieval_metrics='not_applicable_without_required_evidence',
                                retrieved_counts={name:{c['id']:len(r[c['id']]) for c in boundary} for name,r in ranked.items()}),
                  abstention=dict(measured=False, reason='Retrieval only; no Router/Grader/Recovery/answer execution',
                                  expected_behavior_counts=dict(Counter(c['expected_behavior'] for c in cases['cases']))),
                  changes=dict(H1_vs_H0=metric_changes(metrics['H0'],metrics['H1']),
                               step2_vs_H1=metric_changes(metrics['H1'],metrics['step2']),
                               final_vs_step2=metric_changes(metrics['step2'],metrics['final_v2'])),
                  configuration_sha256=sha256(configuration/'manifest.json'), rankings_manifest_sha256=sha256(rankings_folder/'manifest.json'),
                  gold_version=GOLD_VERSION, gold_sha256=protocol['references']['gold']['sha256'],
                  service_default='legacy/H0', tuning_after_results=False, database_used=False,
                  limitation='Settings chosen on corpus v1 dev; final evaluation uses corpus v2. MRR is first required-chunk rank.')
    output.mkdir(parents=True)
    write_new_json(output/'result.json', result)
    freeze_bundle(output, stage='evaluation', gold_sha256=result['gold_sha256'], rankings_manifest_sha256=result['rankings_manifest_sha256'])
    return result
