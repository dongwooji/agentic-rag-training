"""New synthetic questions only; no evaluation inputs, corpus, DB, or OpenAI."""

from copy import deepcopy

import pytest

from src.graph.interpretation_binding import bind_interpretation
from src.graph.tool_input_resolver import ExerciseCanonicalizer
from src.interpretation.contracts import InterpretationDraft
from src.interpretation.exercise_catalog import candidate_schema
from src.interpretation.interpreter import QuestionInterpreter
from src.interpretation.provider import InterpreterProviderResult
from src.interpretation.validator import GroundingError, validate_interpretation
from src.routing.interpretation_router import InterpretationRouter


LIFT = 'Synthetic Pull (Cable)'
OTHER = 'Synthetic Pull (Machine)'


class Catalog:
    def list_canonical_exercises(self):
        return [LIFT, OTHER]

    def resolve_canonical_exercise(self, name):
        return name if name in self.list_canonical_exercises() else None


def extraction(**updates):
    result = dict(personal_record_requested=False, literature_requested=True,
                  exercise_mention=None, canonical_exercise_name=None, exercise_resolution_status=None,
                  candidate_exercises=[], record_operation=None, session_id=None,
                  time_condition=dict(scope='unresolved', start_date=None, end_date=None, source_text=''),
                  requested_analyses=[], literature_subquestion='운동학 문헌에서 관절 각도 측정의 한계',
                  user_assumptions=[], unresolved_fields=[], clarification_required=False)
    result.update(updates)
    return result


def personal(**updates):
    result = extraction(literature_requested=False, literature_subquestion=None,
                        exercise_mention=LIFT, canonical_exercise_name=LIFT,
                        exercise_resolution_status='resolved', candidate_exercises=[LIFT],
                        record_operation='exercise_records',
                        time_condition=dict(scope='all_records', start_date=None, end_date=None, source_text=''))
    result.update(updates)
    return result


def validate(question, output):
    return validate_interpretation(question, output, source='llm',
                                   canonicalizer=ExerciseCanonicalizer(Catalog()),
                                   canonical_exercises=(LIFT, OTHER))


@pytest.mark.parametrize('scope', ['unresolved', 'all_records'])
def test_general_literature_needs_no_record_period(scope):
    question = '최근 운동학 논문에서 관절 각도 측정의 불확실성을 정리해주세요.'
    result = validate(question, extraction(time_condition=dict(scope=scope, start_date=None, end_date=None, source_text='')))
    assert not result.personal_record_requested
    assert not result.clarification_required
    assert result.unresolved_fields == []
    route = InterpretationRouter().route(result)
    assert route.route == 'literature_only'
    assert route.selected_tools == ('search_literature',)
    assert bind_interpretation(result, route) == {}


@pytest.mark.parametrize('metric', [False, True])
def test_unresolved_period_still_blocks_log_and_metric(metric):
    question = f'보관된 {LIFT}의 weekly frequency를 확인해주세요.' if metric else f'보관된 {LIFT} 세트 항목을 조회해주세요.'
    output = personal(time_condition=dict(scope='unresolved', start_date=None, end_date=None, source_text=''))
    if metric:
        output.update(record_operation=None, requested_analyses=[dict(operation='weekly_frequency', n_sessions=None, source_text='weekly frequency')])
    result = validate(question, output)
    assert result.personal_record_requested
    assert result.personal_record_normalization.mismatch
    assert result.clarification_required
    assert [x.field for x in result.unresolved_fields] == ['time_condition']
    assert InterpretationRouter().route(result).selected_tools == ()


@pytest.mark.parametrize('metric', [False, True])
def test_false_flag_is_normalized_from_record_or_metric_semantics(metric):
    question = f'보관된 {LIFT}의 weekly frequency를 확인해주세요.' if metric else f'보관된 {LIFT} 세트 항목을 조회해주세요.'
    output = personal()
    if metric:
        # Existing binding already infers list_sessions for this Metric.
        output.update(record_operation=None, requested_analyses=[dict(operation='weekly_frequency', n_sessions=None, source_text='weekly frequency')])
    result = validate(question, output)
    assert output['personal_record_requested'] is False
    assert result.personal_record_requested is True
    assert not result.clarification_required
    diagnosis = result.personal_record_normalization
    assert diagnosis.supplied_value is False
    assert diagnosis.derived_value is True
    assert diagnosis.mismatch is True
    assert diagnosis.signals == ['requested_analyses' if metric else 'record_operation']
    route = InterpretationRouter().route(result)
    assert route.route == ('log_metric' if metric else 'log_lookup')
    arguments = bind_interpretation(result, route)
    assert arguments['query_training_log']['operation'] == ('list_sessions' if metric else 'exercise_records')
    if metric:
        assert arguments['compute_metrics']['operation'] == 'weekly_frequency'


def test_explicit_personal_signal_is_independent_of_model_flag():
    result = validate(f'내 기록에서 {LIFT}의 항목을 찾아주세요.', personal(record_operation=None))
    assert result.personal_record_requested
    assert result.personal_record_normalization.signals == ['explicit_personal_request']
    # Missing lookup operation is still a real unresolved argument.
    assert [x.field for x in result.unresolved_fields] == ['record_operation']


def test_exercise_and_session_context_is_personal_but_session_checks_remain():
    result = validate(f'{LIFT}의 세션 syn-session-14를 참고해주세요.', personal(record_operation=None, session_id='syn-session-14'))
    assert result.personal_record_requested
    assert result.personal_record_normalization.signals == ['exercise_session_context']
    assert {x.field for x in result.unresolved_fields} == {'record_operation', 'session_id'}


@pytest.mark.parametrize('supplied_flag', [False, True])
@pytest.mark.parametrize('multiple', [False, True])
def test_exercise_candidates_in_literature_do_not_establish_personal_request(supplied_flag, multiple):
    question = 'Synthetic Pull 동작의 관절 각도 측정 문헌을 설명해주세요.'
    output = extraction(personal_record_requested=supplied_flag, exercise_mention='Synthetic Pull',
                        candidate_exercises=[LIFT, OTHER] if multiple else [LIFT],
                        canonical_exercise_name=None if multiple else LIFT,
                        exercise_resolution_status='ambiguous' if multiple else 'resolved')
    result = validate(question, output)
    assert not result.personal_record_requested
    assert not result.clarification_required
    assert result.candidate_exercises == output['candidate_exercises']
    assert result.canonical_exercise_name is None
    assert result.personal_record_normalization.signals == []
    assert result.personal_record_normalization.mismatch is supplied_flag
    assert InterpretationRouter().route(result).selected_tools == ('search_literature',)


def test_normalized_flag_issue_does_not_block_but_other_issues_are_preserved():
    output = personal(unresolved_fields=[dict(field='personal_record_requested', reason='기록 채널 확인')], clarification_required=True)
    question = f'보관된 {LIFT} 세트 항목을 조회해주세요.'
    result = validate(question, output)
    assert not result.clarification_required
    assert result.personal_record_normalization.mismatch
    output['unresolved_fields'].append(dict(field='comparison', reason='비교 대상 확인'))
    result = validate(question, output)
    assert result.clarification_required
    assert [x.field for x in result.unresolved_fields] == ['comparison']


def test_equal_supplied_and_derived_values_have_no_mismatch():
    result = validate(f'보관된 {LIFT} 세트 항목을 조회해주세요.', personal(personal_record_requested=True))
    assert not result.personal_record_normalization.mismatch
    assert not result.clarification_required


def test_diagnostic_is_output_only_and_survives_interpreter_serialization():
    class Provider:
        def invoke(self, question, *, canonical_exercises):
            assert canonical_exercises == tuple(sorted((LIFT, OTHER)))
            return InterpreterProviderResult(raw_interpretation=deepcopy(output))

    output = personal()
    result = QuestionInterpreter(exercise_repository=Catalog(), provider=Provider()).interpret(f'보관된 {LIFT} 세트 항목을 조회해주세요.')
    assert result.execution_status == 'ready'
    assert result.model_dump(mode='json')['interpretation']['personal_record_normalization']['mismatch'] is True
    assert 'personal_record_normalization' not in InterpretationDraft.model_json_schema()['properties']
    assert 'personal_record_normalization' not in candidate_schema((LIFT, OTHER)).model_json_schema()['properties']


@pytest.mark.parametrize('personal_request', [False, True])
def test_invalid_catalog_candidate_is_rejected_even_after_normalization(personal_request):
    output = personal() if personal_request else extraction(exercise_mention=LIFT)
    output.update(candidate_exercises=['Unregistered Synthetic Pull'], canonical_exercise_name=None)
    with pytest.raises(GroundingError, match='stored catalog'):
        validate(f'{LIFT} 동작 문헌과 항목을 확인해주세요.', output)


def test_original_exercise_mention_and_canonical_conflict_still_fail():
    question = f'보관된 {LIFT} 세트 항목을 조회해주세요.'
    with pytest.raises(GroundingError, match='exercise mention is absent'):
        validate(question, personal(exercise_mention=OTHER))
    with pytest.raises(GroundingError, match='known explicit exercise'):
        validate(question, personal(candidate_exercises=[OTHER], canonical_exercise_name=OTHER))


@pytest.mark.parametrize('supplied_flag', [False, True])
def test_literature_canonical_label_alone_cannot_bypass_catalog_validation(supplied_flag):
    output = extraction(personal_record_requested=supplied_flag, exercise_mention=LIFT,
                        canonical_exercise_name='Unregistered Synthetic Pull')
    with pytest.raises(GroundingError, match='stored catalog'):
        validate(f'{LIFT} 동작의 측정 문헌을 설명해주세요.', output)


@pytest.mark.parametrize('kind', ['invented', 'invalid_format', 'reversed'])
def test_date_grounding_format_and_order_remain_checked_in_literature(kind):
    question = '2039-05-11과 2039-06-22의 측정 방법을 다룬 문헌을 정리해주세요.'
    start, end = '2039-05-11', '2039-06-22'
    if kind == 'invented':
        start = '2039-05-12'
    elif kind == 'invalid_format':
        question = '2039-02-30의 측정 방법 문헌을 정리해주세요.'
        start = end = '2039-02-30'
    else:
        start, end = end, start
    with pytest.raises(ValueError):
        validate(question, extraction(time_condition=dict(scope='explicit', start_date=start, end_date=end, source_text='')))


def test_literature_explicit_date_conditions_cannot_be_discarded():
    question = '2039-05-11과 2039-06-22의 측정 방법 문헌을 정리해주세요.'
    result = validate(question, extraction(time_condition=dict(scope='all_records', start_date=None, end_date=None, source_text='')))
    assert result.clarification_required
    assert [x.field for x in result.unresolved_fields] == ['time_condition']


def test_invented_n_and_unsupported_n_for_metric_are_rejected():
    question = f'{LIFT}의 first 4-session and last 4-session median e1RM을 계산해주세요.'
    output = personal(requested_analyses=[dict(operation='first_last_n_session_median_e1rm', n_sessions=9, source_text='first 4-session and last 4-session')])
    with pytest.raises(GroundingError, match='N is absent'):
        validate(question, output)
    output['requested_analyses'][0].update(operation='weekly_frequency', n_sessions=4)
    with pytest.raises(GroundingError, match='N is not applicable'):
        validate(question, output)


def test_invented_analysis_source_is_rejected():
    output = personal(requested_analyses=[dict(operation='weekly_frequency', n_sessions=None, source_text='외부에서 추가한 문장')])
    with pytest.raises(GroundingError, match='analysis source is absent'):
        validate(f'{LIFT}의 weekly frequency를 계산해주세요.', output)
