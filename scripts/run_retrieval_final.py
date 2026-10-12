"""Separate write-once final config, query calls, rankings and Gold evaluation."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.retrieval_final import (
    evaluate_final, freeze_configuration, freeze_queries, rank_final, verified_configuration,
)
from src.evaluation.literature_query_freeze import sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True, choices=('config','generation','translation','rankings','evaluation'))
    parser.add_argument('--configuration', type=Path)
    parser.add_argument('--generation', type=Path)
    parser.add_argument('--translation', type=Path)
    parser.add_argument('--rankings', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--secrets-json', type=Path, help='Opt-in secret loader only, never printed or persisted')
    args = parser.parse_args()
    for path in (args.output, args.configuration, args.generation, args.translation, args.rankings):
        if path and not path.resolve().is_relative_to((ROOT/'reports/experiments').resolve()):
            parser.error('Final experiment folders must stay under reports/experiments')
    if args.output.exists():
        parser.error('Output exists; never overwrite or repeat an execution')
    if args.stage == 'config':
        freeze_configuration(ROOT, args.output)
    else:
        if args.configuration is None:
            parser.error('Frozen --configuration is required')
        protocol, configs, _ = verified_configuration(ROOT, args.configuration)
        if args.stage in ('generation','translation'):
            if args.stage == 'translation' and args.generation is None:
                parser.error('Frozen --generation is required')
            from src.retrieval.query_provider import OpenAILiteratureQueryProvider
            if args.secrets_json:
                # Only this explicit loader may open the user's secrets file.
                secrets = json.loads(args.secrets_json.read_text(encoding='utf-8-sig'))
                key = secrets.get('OPENAI_API_KEY')
            else:
                key = os.environ.get('OPENAI_API_KEY')
            if not isinstance(key, str) or not key.strip():
                parser.error('OPENAI_API_KEY must be configured; no calls made')
            provider = OpenAILiteratureQueryProvider(stage=args.stage, api_key=key)
            # A mock or wrongly configured provider must not change the frozen contract.
            expected = ROOT/'config/literature_query_model_v1.json'
            if provider.config_hash != sha256(expected):
                parser.error('Provider configuration differs from the frozen source')
            freeze_queries(ROOT, args.configuration, args.output, args.stage, provider, generation=args.generation)
        elif args.stage == 'rankings':
            if args.generation is None or args.translation is None:
                parser.error('Both query freezes are required')
            os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
            from src.retrieval.dense import MiniLMEncoder
            cfg = configs['final_v2'].dense
            encoder = MiniLMEncoder(model_id=cfg.model_id, model_revision=cfg.model_revision,
                                    batch_size=cfg.batch_size, max_seq_length=cfg.max_sequence_length,
                                    cache_dir=ROOT/'data/models/huggingface', device='cpu')
            rank_final(ROOT, args.configuration, args.generation, args.translation, args.output, encoder)
        else:
            if args.rankings is None:
                parser.error('Frozen --rankings is required')
            result = evaluate_final(ROOT, args.configuration, args.rankings, args.output)
            print(json.dumps({name:m['macro'] for name,m in result['metrics'].items()}))
    print(json.dumps(dict(stage=args.stage, manifest_sha256=sha256(args.output/'manifest.json'))))


if __name__ == '__main__':
    main()
