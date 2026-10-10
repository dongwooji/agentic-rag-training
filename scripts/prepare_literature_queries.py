"""Freeze one generation/translation stage; no DB, Gold or retrieval execution."""

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.literature_query_freeze import (
    freeze_stage, read_frozen, sha256, source_hashes, verify_prompt_approval,
)
from src.retrieval.query_provider import OpenAILiteratureQueryProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('generation','translation'), required=True)
    parser.add_argument('--input', type=Path, required=True, help='Original v1 query freeze / frozen Step 2 generation')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prompt-approval', type=Path, required=True)
    parser.add_argument('--secrets-json', type=Path, help='Opt-in local secrets, never printed or saved')
    args = parser.parse_args()
    for path in (args.input, args.output):
        if (not path.resolve().is_relative_to(ROOT/'data/evaluation')
                or 'heldout' in str(path).casefold().replace('-','').replace('_','')):
            parser.error('Use dev query versions under data/evaluation')
    if args.output.exists():
        parser.error('Output exists; no overwrite or repeat calls')
    verify_prompt_approval(ROOT, args.prompt_approval)
    datasets = read_frozen(args.input, stage='generation' if args.stage=='translation' else None)
    if any(len(rows)!=18 for rows in datasets.values()):
        parser.error('Expected the approved 18 dev + 18 dev-ko questions')
    if args.stage == 'generation':
        # Original questions only. Old Phase A outputs/selection are not reused.
        datasets = {name:[{k:row[k] for k in ('case_id','display_task_type','original_question')} for row in rows]
                    for name,rows in datasets.items()}
    if args.secrets_json:
        # This is the only code path opening the user's JSON; values never leave memory.
        secrets = json.loads(args.secrets_json.read_text(encoding='utf-8-sig'))
        key = secrets.get('OPENAI_API_KEY')
    else:
        key = os.environ.get('OPENAI_API_KEY')
    if not isinstance(key,str) or not key.strip():
        parser.error('OPENAI_API_KEY is required; PGPASSWORD is not needed for query preparation')
    provider = OpenAILiteratureQueryProvider(stage=args.stage, api_key=key)
    freeze_stage(output=args.output, stage=args.stage, datasets=datasets, provider=provider,
                 input_manifest_sha256=sha256(args.input/'manifest.json'), sources=source_hashes(ROOT),
                 verify_sources=lambda: verify_prompt_approval(ROOT,args.prompt_approval))
    print(json.dumps(dict(stage=args.stage, manifest_sha256=sha256(args.output/'manifest.json'),
                          retrieval_executed=False, next_action='stop_for_user_query_review')))


if __name__ == '__main__':
    main()
