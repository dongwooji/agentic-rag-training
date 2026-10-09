"""Freeze a reviewed dev-ko draft with an explicit, hash-bound user receipt."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.evaluation.dev_ko import freeze_dev_ko


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--approval', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = freeze_dev_ko(args.draft, args.source, args.approval, args.output)
    print(f"Frozen {manifest['dataset_version']}: {manifest['case_count']} questions")


if __name__ == '__main__':
    main()
