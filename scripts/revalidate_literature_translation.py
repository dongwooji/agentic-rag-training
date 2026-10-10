"""Offline revalidation of immutable model outputs; no secrets or LLM calls."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.evaluation.literature_query_freeze import revalidate_translation_freeze, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    for path in (args.input, args.output):
        if not path.resolve().is_relative_to(ROOT / 'data/evaluation'):
            parser.error('Query freezes must remain within data/evaluation')
    revalidate_translation_freeze(previous=args.input, output=args.output,
                                  validator_source=ROOT / 'src/retrieval/literature_query.py')
    print('Frozen translation SHA-256: ' + sha256(args.output / 'manifest.json'))


if __name__ == '__main__':
    main()
