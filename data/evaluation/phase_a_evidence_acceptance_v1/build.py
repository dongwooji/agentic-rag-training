import json, hashlib
R=["training_records"]; RL=["training_records","literature"]; L=["literature"]
N=[None]; NM=[[]]
E1=[["estimated_1rm"],["plateau_candidates"]]
EXC=["canonical_exercise_name","candidate_exercises","exercise_resolution_status"]
SESS={"weekly_frequency","training_gap"}
def recop(rec,met):
    if rec!=[None] or met==[[]]: return rec
    ops={("list_sessions" if m in SESS else "exercise_records") for st in met for m in st}
    return sorted(ops)
def c(cid,pid,q,at,req,opt,rec,met,beh,win,clar=None):
    rec=recop(rec,met)
    return dict(case_id=cid,pair_id=pid,question=q,g=dict(case_id=cid,pair_id=pid,expected_answer_type=at,
      required_evidence_sources=req,optional_evidence_sources=opt,acceptable_record_operations=rec,
      acceptable_metric_operation_sets=met,clarification_required=bool(clar),allowed_clarification_fields=clar or [],
      expected_final_behavior=beh,expected_default_recent_window=win))
cases=[
c("EN-01A","EN-01","2017년 바벨 인클라인 벤치프레스 주별 볼륨 추이를 정리해줘","descriptive",R,[],N,[["weekly_volume"]],"answer",False),
c("EN-01B","EN-01","2017년 바벨 인클라인 벤치프레스 주별 볼륨, 근비대가 목표였다면 모자라진 않았어?","evaluative",RL,[],N,[["weekly_volume"]],"answer",False),
c("EN-02A","EN-02","2018년 바벨 시티드 숄더프레스 추정 1RM이 어떻게 바뀌었어?","descriptive",R,[],N,[["estimated_1rm"]],"answer",False),
c("EN-02B","EN-02","2018년 바벨 시티드 숄더프레스 추정 1RM이 오르다가 꺾였던데, 일반적으로 이런 꺾임은 어떤 요인들로 설명될 수 있어?","causal",RL,[],N,E1,"answer",False),
c("EN-03A","EN-03","최근에 중량 딥스를 일주일에 몇 번씩 했어?","descriptive",R,[],N,[["weekly_frequency"]],"answer",True),
c("EN-03B","EN-03","최근 중량 딥스 빈도를 보고, 앞으로 주에 몇 번 하는 게 좋을지 조언해줘","advisory",RL,[],N,[["weekly_frequency"]],"answer",True),
c("EN-04A","EN-04","근력 향상을 목표로 할 때 한 세트에 몇 회 정도 반복하는 게 일반적으로 권장돼?","general",L,[],N,NM,"answer",False),
c("EN-04B","EN-04","2018년에 중량 딥스를 한 세트에 보통 몇 회씩 했는지 보고, 근력 향상 목적에 맞는 반복 범위였는지 알려줘","evaluative",RL,[],["exercise_records"],NM,"answer",False),
c("EN-05A","EN-05","2017년에 2주 넘게 운동을 안 한 적이 있었어?","descriptive",R,[],N,[["training_gap"]],"answer",False),
c("EN-05B","EN-05","2017년에 2주 넘게 운동을 쉰 적이 있었어? 있었다면 그 정도 공백이면 근력이 많이 빠지는 수준이야?","evaluative",RL,[],N,[["training_gap"]],"answer",False),
c("EN-06A","EN-06","바벨 인클라인 벤치프레스 처음 3세션과 마지막 3세션의 추정 1RM 중앙값을 비교해줘","descriptive",R,[],N,[["first_last_n_session_median_e1rm"]],"answer",False),
c("EN-06B","EN-06","바벨 인클라인 벤치프레스 처음 3세션과 마지막 3세션의 추정 1RM 중앙값을 비교해줘. 비슷한 주제의 논문이 있다면 참고로 덧붙여줘","descriptive",R,L,N,[["first_last_n_session_median_e1rm"]],"answer",False),
c("EN-07A","EN-07","최근 바벨 인클라인 벤치프레스 하고 나면 팔꿈치가 시큰거리는데, 어떤 약을 먹으면 될지 알려줘","out_of_scope",[],[],N,NM,"abstain",False),
c("EN-07B","EN-07","최근 바벨 인클라인 벤치프레스 추정 1RM이 떨어졌는지 확인해줘","descriptive",R,[],N,E1,"answer",True),
c("EN-08A","EN-08","2018년 시티드 로우 추정 1RM이 몇 달째 제자리인데, 이런 정체는 보통 뭐 때문에 생겨?","causal",RL,[],N,NM,"clarification",False,EXC),
c("EN-08B","EN-08","2018년 바벨 인클라인 벤치프레스 추정 1RM이 몇 달째 제자리인데, 이런 정체는 보통 뭐 때문에 생겨?","causal",RL,[],N,E1,"answer",False),
c("EN-09A","EN-09","2018년 9월 중량 딥스 주간 볼륨이 8월보다 왜 낮게 나왔는지 주별 수치로 보여줘","descriptive",R,[],N,[["weekly_volume"]],"answer",False),
c("EN-09B","EN-09","2018년 9월에 중량 딥스 볼륨을 줄였는데, 그게 근육 유지에 문제가 될 수 있을까?","evaluative",RL,[],N,[["weekly_volume"]],"answer",False),
c("EN-10A","EN-10","근력 운동 초보가 주당 세트 수를 늘릴 때 어떤 원칙을 따르면 좋아?","advisory",L,[],N,NM,"answer",False),
c("EN-10B","EN-10","최근 바벨 시티드 숄더프레스 주간 볼륨을 보고, 다음 달에 세트 수를 얼마나 늘리면 좋을지 조언해줘","advisory",RL,[],N,[["weekly_volume"]],"answer",True),
]
q="".join(json.dumps(dict(case_id=x["case_id"],pair_id=x["pair_id"],question=x["question"]),ensure_ascii=False)+"\n" for x in cases)
g=json.dumps(dict(schema_version="phase_a_evidence_gold_v1",cases=[x["g"] for x in cases]),ensure_ascii=False,indent=2)+"\n"
open("questions.jsonl","w",encoding="utf-8").write(q); open("gold.json","w",encoding="utf-8").write(g)
rows=[]
for x in cases:
    G=x["g"]
    rows.append(f"| {x['case_id']} | {x['question']} | {G['expected_answer_type']} | {', '.join(G['required_evidence_sources']) or '-'} | {', '.join(G['optional_evidence_sources']) or '-'} | {' / '.join('{'+','.join(s)+'}' if s else '없음' for s in G['acceptable_metric_operation_sets'])} | {G['acceptable_record_operations'][0] or '없음'} | {G['expected_final_behavior']} | {'O' if G['expected_default_recent_window'] else '-'} |")
open("_rows.md","w",encoding="utf-8").write("\n".join(rows)+"\n")
for f in ("questions.jsonl","gold.json"): print(f, hashlib.sha256(open(f,"rb").read()).hexdigest())
