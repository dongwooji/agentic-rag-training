"""Run Phase A once per dev question and stop at frozen query review. Never search."""

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.database.config import DatabaseConfig
from src.tools.training_log import PsycopgTrainingRepository
from src.interpretation.exercise_catalog import RepositoryExerciseCatalog
from src.interpretation.interpreter import QuestionInterpreter
from src.interpretation.provider import OpenAIQuestionInterpreterProvider
from src.routing.runtime import RuntimeRouter
from src.evaluation.query_preparation import SnapshotExerciseRepository, freeze_query_preparation, sha256
from src.evaluation.dev_ko import load_frozen_dev_ko


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/evaluation/retrieval_step2_queries_v1')
    parser.add_argument('--rules', type=Path, default=ROOT / 'data/evaluation/retrieval_step2_rules_v1')
    parser.add_argument('--secrets-json', type=Path, help='Explicit user-provided secrets file; values are never printed or saved')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT / 'data/evaluation') or 'heldout' in args.output.name:
        parser.error('Use a new dev-only query directory under data/evaluation')
    if args.output.exists():
        parser.error('Output already exists; no repeat interpretation or overwrite')
    if args.secrets_json:
        secrets = json.loads(args.secrets_json.read_text(encoding='utf-8-sig'))
        for name in ('OPENAI_API_KEY','PGPASSWORD','PGHOST','PGPORT','PGDATABASE','PGUSER'):
            if name in secrets:
                os.environ[name] = str(secrets[name])
    if not os.environ.get('OPENAI_API_KEY') or not os.environ.get('PGPASSWORD'):
        parser.error('OPENAI_API_KEY and PGPASSWORD must be configured before starting Phase A')
    provider = OpenAIQuestionInterpreterProvider()
    repository = PsycopgTrainingRepository(config=DatabaseConfig.from_environment(), password=os.environ['PGPASSWORD'])
    try:
        names = RepositoryExerciseCatalog(repository).names()
    except Exception as exc:
        parser.error('Read-only canonical catalog unavailable: '+type(exc).__name__)
    interpreter = QuestionInterpreter(exercise_repository=SnapshotExerciseRepository(names), provider=provider)
    source_path = ROOT / 'data/evaluation/eval_dataset_v1.json'
    source = json.loads(source_path.read_text(encoding='utf-8'))
    # Gold and case labels never enter the Interpreter or query-selection functions.
    selected = [c for c in source['cases'] if c['category'] in ('hybrid', 'literature_only')]
    ko_folder = ROOT / 'data/evaluation/dev_ko_v1'
    ko = dict(load_frozen_dev_ko(ko_folder, source_path))
    datasets = {name: [{'case_id':c['id'], 'question':c['question'] if name=='dev' else ko[c['id']],
                        'display_task_type':c['category']} for c in selected] for name in ('dev','dev-ko')}
    manifest = freeze_query_preparation(root=ROOT, output=args.output, rules_folder=args.rules, datasets=datasets,
        interpreter=interpreter, legacy_router=RuntimeRouter(), model_config=provider.config,
        input_hashes={'dev':sha256(source_path),'dev-ko-manifest':sha256(ko_folder / 'manifest.json')})
    print(json.dumps({'version':manifest['version'],'manifest_sha256':sha256(args.output / 'manifest.json'),
                      'retrieval_executed':False,'next_action':'stop_for_user_query_review'}))


if __name__ == '__main__':
    main()
