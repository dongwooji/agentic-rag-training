"""Write-once preregistered Grader experiment; no answer generation or DB access."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
PROTOCOL=ROOT/'docs/decisions/grader_fixed_requirements_v1_protocol.json'


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
    with Path(path).open('x',encoding='utf-8',newline='\n') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
def lines(path):return [json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines() if x.strip()]


def verify_protocol():
    expected=PROTOCOL.with_suffix('.sha256').read_text().split()[0]
    if sha(PROTOCOL)!=expected:raise RuntimeError('protocol changed after freeze')
    protocol=read(PROTOCOL)
    for path,value in protocol['source_sha256'].items():
        if sha(ROOT/path)!=value:raise RuntimeError('frozen source changed: '+path)
    return protocol


def verify_seal(output):
    seal=read(output/'implementation_seal.json')
    if seal['protocol_sha256']!=sha(PROTOCOL):raise RuntimeError('protocol differs from implementation seal')
    for path,value in seal['source_sha256'].items():
        if sha(ROOT/path)!=value:raise RuntimeError('implementation changed: '+path)
    current=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if current!=seal['implementation_sha']:raise RuntimeError('implementation commit changed')
    if subprocess.run(['git','diff','--quiet','HEAD'],cwd=ROOT).returncode:raise RuntimeError('uncommitted tracked changes')
    return seal


class ObservedProvider:
    def __init__(self,base,stage,events,journal):
        self.base,self.stage,self.events,self.journal=base,stage,events,journal
        self.model,self.prompt_sha256,self.config_sha256=base.model,base.prompt_sha256,base.config_sha256
    def invoke(self,value):
        number=len(self.events)+1
        started=dict(event_id=number,stage=self.stage,started_at_utc=datetime.now(timezone.utc).isoformat(),
                     input_sha256=hashlib.sha256(value.model_dump_json().encode()).hexdigest())
        with self.journal.open('a',encoding='utf-8') as f:f.write(json.dumps(dict(kind='start',**started))+'\n');f.flush()
        tick=perf_counter()
        result=self.base.invoke(value)
        event=dict(**started,model=result.model,latency_ms=result.latency_ms,
                   wrapper_latency_ms=(perf_counter()-tick)*1000,
                   usage=result.token_usage.model_dump(),error_code=result.error_code.value if result.error_code else None)
        self.events.append(event)
        with self.journal.open('a',encoding='utf-8') as f:f.write(json.dumps(dict(kind='complete',**event))+'\n');f.flush()
        return result


def providers(key,events,journal):
    from src.grading.runtime_grader import RuntimeEvidenceGrader
    from src.grading.runtime_provider import OpenAIRuntimeEvidenceProvider
    from src.grading.fixed_requirements_v1.grader import FixedRequirementsGrader
    from src.grading.fixed_requirements_v1.provider import FixedRequirementsProvider
    from src.recovery.agent import EvidenceRecoveryAgent
    from src.recovery.provider import OpenAIEvidenceRecoveryProvider
    wrap=lambda p,s:ObservedProvider(p,s,events,journal)
    return (RuntimeEvidenceGrader(wrap(OpenAIRuntimeEvidenceProvider(api_key=key),'old_grader')),
            FixedRequirementsGrader(wrap(FixedRequirementsProvider(api_key=key),'new_grader')),
            EvidenceRecoveryAgent(wrap(OpenAIEvidenceRecoveryProvider(api_key=key),'recovery')))


def retrieval(heldout=False):
    """Existing pinned encoder/vectors and runtime retriever; never write vectors."""
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
    import torch
    import numpy as np
    from src.retrieval.dense import MiniLMEncoder
    from src.retrieval.hybrid import load_frozen_literature_assets,FrozenHybridRetriever
    from src.retrieval.runtime_config import load_retrieval_config
    from src.evaluation.retrieval_preparation import MemoryCosineStore
    from src.tools.literature import LiteratureTool
    torch.set_num_threads(4)
    config=load_retrieval_config(ROOT/('config/retrieval_final_v2_corpus_v2_v1.json' if heldout else 'config/retrieval_step3b_translation_v1.json'))
    assets=load_frozen_literature_assets(ROOT,config=config)
    folder=ROOT/'reports/experiments'
    if heldout:
        ids=read(folder/'retrieval_v2_heldout_rankings_v1/parent_chunk_ids.json')
        vectors=np.load(folder/'retrieval_v2_heldout_rankings_v1/parent_embeddings.npy',allow_pickle=False)
    else:
        ids=read(folder/'bge_m3_model_comparison_v1/new_minilm_v1_parent_ids.json')
        vectors=np.load(folder/'bge_m3_model_comparison_v1/new_minilm_v1_parent_embeddings.npy',allow_pickle=False)
    if ids!=[c['chunk_id'] for c in assets.chunks]:raise RuntimeError('frozen vector row order mismatch')
    encoder=MiniLMEncoder(cache_dir=ROOT/'data/models/huggingface',device='cpu',max_seq_length=128)
    retriever=FrozenHybridRetriever(assets=assets,config=config,encoder=encoder,vector_store=MemoryCosineStore(ids,vectors))
    return LiteratureTool(assets=assets,retriever=retriever),{c['chunk_id']:c for c in assets.chunks},config


def join_hits(ranking,corpus):
    ids=[x['chunk_id'] for x in ranking]
    if len(ids)!=10 or len(set(ids))!=10:raise RuntimeError('initial ranking must have ten unique parents')
    return [{**corpus[x['chunk_id']],**x} for x in ranking]


def runtime_inputs_dev(corpus):
    query=read(ROOT/'reports/experiments/bge_m3_model_comparison_v1/queries_dev.json')
    rankings=read(ROOT/'reports/experiments/bge_m3_model_comparison_v1/minilm_dev/rankings.json')
    result=[]
    for split in ['dev','dev-ko']:
        generated=lines(ROOT/f'data/evaluation/retrieval_step2_generation_queries_v1/{split}.jsonl')
        byid={r['case_id']:r for r in generated}
        if len(query[split])!=18:raise RuntimeError('wrong dev denominator')
        for q in query[split]:
            row=byid[q['case_id']];step2=row['result']
            result.append(dict(split=split,case_id=q['case_id'],question=row['original_question'],
                literature_question=step2['actual_query'],validation_status=step2['validation_status'],
                query=q['query'],hits=join_hits(rankings[split][q['case_id']],corpus)))
    return result


def execute(row,grader,recovery,tool,*,enabled,first=None,initial_ms=None):
    from src.evaluation.fixed_requirements_v1 import initial_state,nodes,first_assessment,finish_assessment
    node=nodes(grader,recovery,tool)
    if first is None:
        first,initial_ms=first_assessment(node,initial_state(row['question'],row['literature_question'],row['query'],row['hits'],
            validation_status=row.get('validation_status','passed')))
    finished=finish_assessment(node,first,recovery_enabled=enabled)
    finished['initial_latency_ms']=initial_ms
    return first,finished


def summarize_condition(result,events):
    state=result['state']
    return dict(final_behavior=result['final_behavior'],execution_failure=result['execution_failure'],
        grade=state.get('grader_result') or {},
        final_ids=[x['chunk_id'] for x in state['fused_evidence']],
        retry_count=state['retry_count'],recovery_attempts=len(state['recovery_history']),
        initial_latency_ms=result['initial_latency_ms'],continuation_latency_ms=result['continuation_latency_ms'],
        event_ids=[x['event_id'] for x in events],llm_calls=len(events),
        llm_latency_ms=sum(x['latency_ms'] for x in events),
        input_tokens=sum(x['usage']['input_tokens'] for x in events),output_tokens=sum(x['usage']['output_tokens'] for x in events),
        estimated_cost_usd=sum(x['usage']['estimated_cost_usd'] for x in events),
        errors=[dict(stage=e.get('stage'),code=e.get('code')) for e in state['errors']])


def score_dev(raw):
    from src.evaluation.fixed_requirements_v1 import score_pair,adoption
    # Gold is loaded only after all runtime results have been saved.
    gold={c['id']:c for c in read(ROOT/'data/evaluation/eval_dataset_v1.json')['cases']}
    scored=[]
    for row in raw:
        before=row['initial_ids'];out=dict(split=row['split'],case_id=row['case_id'])
        for key in ['old','new_off','new_on']:
            result=deepcopy(row[key]['summary']);metric=score_pair(gold[row['case_id']],before,result['final_ids'])
            result.update(final_egr10=metric['final']['egr10'],final_ce10=metric['final']['ce10'])
            out[key]=result;out['initial_ce10']=metric['initial']['ce10']
        scored.append(out)
    return scored,adoption(scored)


def run_dev(output,key):
    verify_seal(output)
    if (output/'dev_started.json').exists():raise RuntimeError('dev decision execution already started; no reruns')
    tool,corpus,config=retrieval()
    inputs=runtime_inputs_dev(corpus)
    save(output/'dev_started.json',dict(started_at_utc=datetime.now(timezone.utc).isoformat(),inputs=36,protocol_sha256=sha(PROTOCOL)))
    events=[];old,new,recovery=providers(key,events,output/'dev_llm_events.jsonl')
    raw=[]
    for number,row in enumerate(inputs,1):
        case=dict(split=row['split'],case_id=row['case_id'],initial_ids=[x['chunk_id'] for x in row['hits']])
        offset=len(events)
        _,old_result=execute(row,old,recovery,tool,enabled=False)
        case['old']=dict(summary=summarize_condition(old_result,events[offset:]),state=old_result['state'])
        offset=len(events)
        first,off=execute(row,new,recovery,tool,enabled=False)
        shared_events=deepcopy(events[offset:])
        case['new_off']=dict(summary=summarize_condition(off,shared_events),state=off['state'])
        offset=len(events)
        _,on=execute(row,new,recovery,tool,enabled=True,first=first,initial_ms=off['initial_latency_ms'])
        case['new_on']=dict(summary=summarize_condition(on,[*shared_events,*events[offset:]]),state=on['state'])
        save(output/f'dev_{number:02d}.json',case);raw.append(case)
        print(f'dev {number}/36 saved',flush=True)
    save(output/'dev_raw.json',raw)
    scored,decision=score_dev(raw)
    save(output/'dev_scored.json',scored)
    save(output/'dev_events.json',events)
    decision.update(protocol_sha256=sha(PROTOCOL),implementation_sha=read(output/'implementation_seal.json')['implementation_sha'],
        raw_sha256=sha(output/'dev_raw.json'),scored_sha256=sha(output/'dev_scored.json'),
        decided_at_utc=datetime.now(timezone.utc).isoformat(),final_answer_calls=0)
    save(output/'dev_decision.json',decision)
    save(output/'dev_decision_seal.json',dict(sha256=sha(output/'dev_decision.json')))
    print('dev decision sealed; held-out not executed',flush=True)


def run_heldout(output,key):
    verify_seal(output)
    if sha(output/'dev_decision.json')!=read(output/'dev_decision_seal.json')['sha256']:raise RuntimeError('dev decision changed')
    decision=read(output/'dev_decision.json')
    if (output/'heldout_started.json').exists():raise RuntimeError('held-out diagnostic already started; no reruns')
    # No held-out question or Gold is parsed before this decision check.
    tool,corpus,config=retrieval(True)
    translated=lines(ROOT/'reports/experiments/retrieval_v2_heldout_translation_v1/queries.jsonl')
    rankings=read(ROOT/'reports/experiments/retrieval_v2_heldout_rankings_v1/final_v2_rankings.json')
    input_cases=read(ROOT/'data/evaluation/heldout_v2_v1_1_final/evaluation_cases.json')
    eligible={c['id'] for c in input_cases['cases'] if c['retrieval_metric_eligible']}
    records=[r for r in translated if r['case_id'] in eligible]
    if len(records)!=20:raise RuntimeError('held-out diagnostic denominator differs from frozen 20')
    save(output/'heldout_started.json',dict(started_at_utc=datetime.now(timezone.utc).isoformat(),dev_decision_sha256=sha(output/'dev_decision.json')))
    events=[];old,new,recovery=providers(key,events,output/'heldout_llm_events.jsonl')
    selected=new if decision['selected_grader']=='fixed_requirements_v1' else old
    raw=[]
    for number,r in enumerate(records,1):
        row=dict(question=r['original_question'],literature_question=r['step2']['actual_query'],validation_status=r['step2']['validation_status'],
                 query=r['result']['actual_query'],hits=join_hits(rankings[r['case_id']],corpus))
        offset=len(events)
        _,finished=execute(row,selected,recovery,tool,enabled=decision['selected_recovery_enabled'])
        case=dict(case_id=r['case_id'],initial_ids=[x['chunk_id'] for x in row['hits']],
                  summary=summarize_condition(finished,events[offset:]),state=finished['state'])
        save(output/f'heldout_{number:02d}.json',case);raw.append(case)
        print(f'held-out {number}/20 saved',flush=True)
    save(output/'heldout_raw.json',raw);save(output/'heldout_events.json',events)
    from src.evaluation.fixed_requirements_v1 import score_pair
    gold_data=read(ROOT/'data/evaluation/heldout_v2_v1_1_final/gold_evidence.json')
    from src.evaluation.heldout_v2 import retrieval_metric_cases
    adapted,_=retrieval_metric_cases(input_cases,gold_data)
    gold={x['id']:x for x in adapted}
    scored=[]
    for r in raw:
        g=gold[r['case_id']]
        metric=score_pair(g,r['initial_ids'],r['summary']['final_ids'])
        scored.append(dict(case_id=r['case_id'],**metric,**r['summary']))
    save(output/'heldout_scored.json',dict(diagnostic_only=True,independent_validation=False,
        selected_grader=decision['selected_grader'],recovery_enabled=decision['selected_recovery_enabled'],cases=scored))
    print('held-out diagnostic complete; no decision changes',flush=True)


def write_report(output):
    from collections import Counter
    from statistics import mean
    from src.evaluation.fixed_requirements_v1 import grader_disagreements
    verify_protocol()
    decision=read(output/'dev_decision.json')
    if sha(output/'dev_decision.json')!=read(output/'dev_decision_seal.json')['sha256']:raise RuntimeError('decision changed')
    rows=read(output/'dev_scored.json');heldout=read(output/'heldout_scored.json')
    folder=ROOT/'docs/evaluations/grader_fixed_requirements_v1';folder.mkdir(parents=True,exist_ok=False)
    def compact_grade(g):
        return dict(execution_status=g.get('execution_status'),evidence_sufficient=g.get('evidence_sufficient'),
            error_code=g.get('error_code'),missing_component_ids=g.get('missing_component_ids',[]),
            components=[{k:c.get(k) for k in ['component_id','requirement','status','supporting_chunk_ids']} for c in g.get('components',[])],
            requirements=g.get('requirements'))
    def compact(r):
        q=deepcopy(r)
        if 'grade' in q:q['grade']=compact_grade(q['grade'])
        return q
    public=[]
    for r in rows:
        public.append(dict(split=r['split'],case_id=r['case_id'],initial_ce10=r['initial_ce10'],
                           **{k:compact(r[k]) for k in ['old','new_off','new_on']}))
    held_public=[{**r,'grade':compact_grade(r['grade'])} for r in heldout['cases']]
    save(folder/'dev_results.json',public);save(folder/'dev_decision.json',decision)
    save(folder/'heldout_diagnostic.json',{**heldout,'cases':held_public})
    events={phase:read(output/f'{phase}_events.json') for phase in ['dev','heldout']}
    physical={phase:dict(calls=len(values),by_stage=dict(Counter(x['stage'] for x in values)),
        input_tokens=sum(x['usage']['input_tokens'] for x in values),cached_input_tokens=sum(x['usage']['cached_input_tokens'] for x in values),
        output_tokens=sum(x['usage']['output_tokens'] for x in values),
        llm_latency_ms=sum(x['latency_ms'] for x in values),estimated_cost_usd=sum(x['usage']['estimated_cost_usd'] for x in values))
        for phase,values in events.items()}
    summaries={}
    for split in ['dev','dev-ko','combined']:
        selected=rows if split=='combined' else [r for r in rows if r['split']==split]
        summaries[split]=dict(inputs=len(selected),old=grader_disagreements(selected,'old'),new=grader_disagreements(selected,'new_off'))
    for phase in ['dev','heldout']:
        save(folder/f'{phase}_usage.json',physical[phase])
    protected=read(output/'protected_before.json')
    intentional={'src/graph/nodes.py','src/graph/workflow.py'}
    unchanged=[p for p,h in protected.items() if sha(ROOT/p)==h]
    unexpected=[p for p,h in protected.items() if sha(ROOT/p)!=h and p not in intentional]
    if unexpected:raise RuntimeError('protected files changed: '+repr(unexpected))
    save(folder/'integrity.json',dict(protocol_sha256=sha(PROTOCOL),sources_verified=len(read(PROTOCOL)['source_sha256']),
         protected_unchanged=len(unchanged),intentional_shared_graph_changes=sorted(set(protected)-set(unchanged)),unexpected_changes=unexpected,
         implementation_sha=decision['implementation_sha'],contract_commit='a5a4b44',final_answer_llm_calls=0,
         acceptance_rerun=False,old_grader_files_unchanged=True,service_defaults_changed=False,
         source_artifacts={p.relative_to(ROOT).as_posix():sha(p) for p in output.glob('*.json') if p.name!='protected_before.json'}))
    text=['# 고정 문헌 요구사항 Grader v1: 동결 후 최초 판정 실행','',
      '기준 master: `e7a61b58526b0e2cc048091d7eed2b13f5a38aee`. 계약 동결 commit: `a5a4b44`.',
      f"평가한 구현 SHA: `{decision['implementation_sha']}`. protocol SHA-256: `{sha(PROTOCOL)}`.",'',
      '## 결과와 채택 판정','',
      f"신규 Grader: **{'채택' if decision['grader_adopted'] else '비채택'}**. 신규 Recovery 조건: **{'기준 충족' if decision['recovery']['adopted'] else '채택 보류'}**.",
      '서비스 기본값은 판정과 무관하게 기존 Grader/legacy로 유지한다. 새 버전은 명시적 선택만 가능하다.','',
      '| 분할 | 입력 | 기존 A | 신규 A | 기존 B | 신규 B | 기존 합계 | 신규 합계 | 기존 실패 | 신규 실패 |',
      '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for split,s in summaries.items():
        a,b=s['old'],s['new'];text.append(f"| {split} | {s['inputs']} | {a['A']} | {b['A']} | {a['B']} | {b['B']} | {a['total']} | {b['total']} | {a['execution_failures']} | {b['execution_failures']} |")
    text+=['','A=CE=1인데 부족, B=CE=0인데 충분. 정상 완료 판정만 A/B로 집계하며 실패는 별도 계수하고 분모에서 제외하지 않는다.',
      '판정은 합산36입력만 사용한다: A+B strict 감소 AND A/B 각각 증가 없음 AND 실행 실패 증가 없음. dev-ko는 동일 원본18개의 표현 변형으로 독립36문항이 아니다.','',
      f"신규 Grader의 입력 출처 검증 실패(input_failure): {sum(r['new_off']['grade'].get('error_code')=='input_failure' for r in rows)}건. 이 경우 LLM을 호출하지 않았으며 정상 판정을 얻은 것처럼 처리하지 않는다. 동결 Step 2 검증 실패와 Grader 의미 판정의 실패를 구분해서 해석해야 한다.",'',
      '## Recovery 켬/끔','',
      '| 분할/조건 | 최종 EGR@10 | 최종 CE@10 | 답변/보류/판정 실패 | 실제 실행 오류 요청 | LLM 호출(공유 포함) | 평균 처리 지연(ms) |',
      '|---|---:|---:|---|---:|---:|---:|']
    for split in ['dev','dev-ko','combined']:
        selected=rows if split=='combined' else [r for r in rows if r['split']==split]
        for key in ['new_off','new_on']:
            conditions=[r[key] for r in selected];states=Counter(x['final_behavior'] for x in conditions)
            text.append(f"| {split}/{key} | {mean(x['final_egr10'] for x in conditions):.6f} | {mean(x['final_ce10'] for x in conditions):.6f} | {states['answer_ready']}/{states['abstain_ready']}/{states['execution_failure']} | {sum(x['execution_failure'] for x in conditions)} | {sum(x['llm_calls'] for x in conditions)} | {mean(x['initial_latency_ms']+x['continuation_latency_ms'] for x in conditions):.1f} |")
    text+=['','신규 최초 판정과 요구사항은 각 입력에서 1회만 실행·저장하고 켬/끔이 공유한다. 조건별 호출 합계에는 공유 호출이 중복되므로 전체 실제 호출 비용은 아래 별도 집계를 사용한다.',
      '최종 동작은 마지막 Grader 판정에서 산출한다. 후속 검색/Recovery 오류는 별도 실행 오류 플래그에 남겨 감추지 않는다.',
      'CE(on)>=CE(off) AND 실행 오류(on)<=실행 오류(off)를 만족해야 Recovery 기준을 충족한다. 검색은 기존 bounded Recovery와 runtime fusion을 그대로 사용한다.','',
      '## 문항별 결과','',
      '| 분할/case | 최초 CE | 기존→신규 초기 판정 | 켬/끔 EGR | 켬/끔 CE | 켬 최종 동작 |',
      '|---|---:|---|---|---|---|']
    def label(g):return '실패' if g.get('execution_status')!='completed' else ('충분' if g.get('evidence_sufficient') else '부족')
    for r in rows:
        a,b=r['new_on'],r['new_off'];text.append(f"| {r['split']}/{r['case_id']} | {r['initial_ce10']:.0f} | {label(r['old']['grade'])}→{label(b['grade'])} | {a['final_egr10']:.3f}/{b['final_egr10']:.3f} | {a['final_ce10']:.0f}/{b['final_ce10']:.0f} | {a['final_behavior']} |")
    text+=['','고정 요구사항·문항별 판정·실패 코드·chunk ID는 [dev_results.json](dev_results.json)에 보존했다. 최초 요구 생성과 첫 판정은 같은 호출이므로 요구 생성이 검색 근거로부터 완전히 독립적이지 않다.','',
      '## 판정 후 held-out 진단','',
      f"선택된 버전: `{heldout['selected_grader']}`, Recovery: `{heldout['recovery_enabled']}`. corpus v2 evidence-bearing 20입력 각1회. **diagnostic only**, 독립 검증/채택 평가 아님.",
      f"최초 EGR/CE: {mean(x['initial']['egr10'] for x in heldout['cases']):.6f}/{mean(x['initial']['ce10'] for x in heldout['cases']):.6f}; 최종 EGR/CE: {mean(x['final']['egr10'] for x in heldout['cases']):.6f}/{mean(x['final']['ce10'] for x in heldout['cases']):.6f}.",
      f"최종 동작: {dict(Counter(x['final_behavior'] for x in heldout['cases']))}. 실제 실행 오류 요청: {sum(x['execution_failure'] for x in heldout['cases'])}.",
      'dev decision을 먼저 hash로 봉인한 뒤 진단을 실행했다. 결과를 보고 설정·코드·prompt·Gold·결정을 변경하거나 다시 실행하지 않았다. 세부 결과는 [heldout_diagnostic.json](heldout_diagnostic.json).','',
      '## 호출·비용·지연','',
      '| 단계 | 실제 LLM 호출 | input/cached input/output token | LLM 지연 합계(s) | 저장 가격 기준 추정 USD |',
      '|---|---:|---|---:|---:|']
    for phase,u in physical.items():text.append(f"| {phase} | {u['calls']} | {u['input_tokens']}/{u['cached_input_tokens']}/{u['output_tokens']} | {u['llm_latency_ms']/1000:.3f} | {u['estimated_cost_usd']:.6f} |")
    text+=['','최종 답변 생성·Interpreter·Step 2/3 LLM 호출은 모두 0회다. 최초 Top-10은 저장 ranking 재생이며 Recovery만 검색한다. 지연은 이 Grader/Recovery 실험의 측정값으로 REST E2E 지연이 아니다. 온/오프는 순차 실행과 초기 판정 공유이므로 독립적인 전체 서비스 요청의 지연 비교로 해석하지 않는다.','',
      '## 검증·보존·한계','',
      '- 구현 중 신규 Grader에는 synthetic/unit/mock 입력만 사용했다. 계약·코드·테스트를 고정한 뒤의 dev 최초 실행이 판정 실행이다.',
      '- 최종 답변을 생성하지 않았으므로 문장 품질, 답변 grounding, 전체 서비스 동작 정확도를 평가하지 않는다. answer_ready 비율을 accuracy라고 부르지 않는다.',
      '- Gold는 실행 종료 후 scorer에서만 읽었다. 고정 chunk Gold 충족은 문헌의 의미적 충분성 전체를 대체하지 않으며 불일치 수는 Gold 일치도다.',
      '- 이 변경은 과거 실행 trace 관찰 뒤의 post-hoc 개선이다. 기존 acceptance v1을 재실행하지 않았고 해당 결과는 `6c68ea996f967bcdda47bfed2671aae1ae583969` 기준으로 보존한다.',
      '- corpus/벡터/기존 Grader 코드·prompt·config/기존 평가 결과와 서비스 기본값은 보존했다. 무결성 hash와 실행 SHA는 [integrity.json](integrity.json)에 기록했다.',
      '- 단일 확률적 실행, 이미 분석된 개발 자료, 번역 쌍의 의존성, 모델/시점/캐시 영향이 있다. 일반화 성능 향상으로 주장하지 않는다.',
      '- 테스트·CI와 최종 PR head는 PR 설명과 완료 보고에 별도로 기록한다. Merge하지 않는다.','']
    with (folder/'REPORT.md').open('x',encoding='utf-8',newline='\n') as f:f.write('\n'.join(text))
    save(folder/'manifest.json',dict(version='grader_fixed_requirements_v1_results',
        artifacts={p.name:sha(p) for p in folder.iterdir() if p.is_file()},
        implementation_sha=decision['implementation_sha'],protocol_sha256=sha(PROTOCOL)))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=['dev','heldout','report'],required=True)
    parser.add_argument('--secrets-json',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/grader_fixed_requirements_v1')
    args=parser.parse_args();verify_protocol()
    if args.phase=='report':write_report(args.output);return
    if args.secrets_json is None:parser.error('--secrets-json is required for actual provider execution')
    secrets=read(args.secrets_json);key=secrets.get('OPENAI_API_KEY')
    if not key:raise RuntimeError('explicit secrets file has no provider credential')
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    (run_dev if args.phase=='dev' else run_heldout)(args.output,key)


if __name__=='__main__':main()
