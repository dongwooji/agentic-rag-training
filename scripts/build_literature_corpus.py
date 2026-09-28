"""Acquire selected PMC XML, parse sections, chunk text, and validate corpus v1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.literature.corpus import (
    build_paper_and_chunks,
    download_pmc_xml,
    sha256_file,
    write_jsonl,
)
from src.literature.validation import validate_corpus


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = json.loads(
        (PROJECT_ROOT / "config" / "literature_corpus_v1.json").read_text(
            encoding="utf-8"
        )
    )
    selection = json.loads(
        (PROJECT_ROOT / "config" / "literature_selection_v1.json").read_text(
            encoding="utf-8"
        )
    )
    candidates = json.loads(
        (
            PROJECT_ROOT
            / "data"
            / "literature"
            / "manifests"
            / "candidates_v1.json"
        ).read_text(encoding="utf-8")
    )
    candidates_by_pmcid = {
        candidate["pmcid"].upper(): candidate
        for candidate in candidates["candidates"]
        if candidate.get("pmcid")
    }
    raw_dir = PROJECT_ROOT / "data" / "literature" / "raw"
    processed_dir = PROJECT_ROOT / "data" / "literature" / "processed"
    reports_dir = PROJECT_ROOT / "reports"
    manifests_dir = PROJECT_ROOT / "data" / "literature" / "manifests"

    papers = []
    chunks = []
    for selected in selection["selected"]:
        pmcid = selected["pmcid"].upper()
        xml_path = raw_dir / f"{pmcid}.xml"
        if args.refresh or not xml_path.exists():
            print(f"Downloading {pmcid}...")
            download_pmc_xml(pmcid, xml_path)
        paper, paper_chunks = build_paper_and_chunks(
            xml_path=xml_path,
            selection=selected,
            corpus_config=config,
            pubmed_metadata=candidates_by_pmcid.get(pmcid),
        )
        papers.append(paper)
        chunks.extend(paper_chunks)

    papers.sort(key=lambda row: row["paper_id"])
    chunks.sort(key=lambda row: row["chunk_id"])
    validation = validate_corpus(papers, chunks, config)
    papers_path = processed_dir / "papers.jsonl"
    chunks_path = processed_dir / "chunks.jsonl"
    write_jsonl(papers_path, papers)
    write_jsonl(chunks_path, chunks)

    selected_by_pmcid = {
        row["pmcid"].upper(): row for row in selection["selected"]
    }
    clinical_terms = (
        "cancer",
        "dementia",
        "stroke",
        "sarcopenia",
        "osteoporosis",
        "rehabilitation",
        "hiv",
        "systemic sclerosis",
    )
    screened_candidates = []
    for candidate in candidates["candidates"]:
        record = dict(candidate)
        pmcid = str(record.get("pmcid", "")).upper()
        title_lower = str(record.get("title", "")).casefold()
        if pmcid in selected_by_pmcid:
            record["selection_status"] = "included"
            record["selection_reason"] = selected_by_pmcid[pmcid]["reason"]
        elif not pmcid:
            record["selection_status"] = "excluded"
            record["selection_reason"] = "No deterministic PMC structured full text."
        elif any(term in title_lower for term in clinical_terms):
            record["selection_status"] = "excluded"
            record["selection_reason"] = "Clinical or rehabilitation population outside corpus v1 scope."
        elif pmcid in {"PMC5005843", "PMC13377779"}:
            record["selection_status"] = "excluded"
            record["selection_reason"] = "Structured full text could not be acquired from the official APIs."
        else:
            record["selection_status"] = "excluded"
            record["selection_reason"] = "Lower-priority or redundant evidence for corpus v1 topic coverage."
        screened_candidates.append(record)

    screened_path = manifests_dir / "screened_candidates_v1.json"
    screened_path.write_text(
        json.dumps(
            {
                "corpus_version": config["version"],
                "candidate_count": len(screened_candidates),
                "included_count": len(papers),
                "candidates": screened_candidates,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    manifest_path = manifests_dir / "corpus_v1.json"
    manifest = {
        "corpus_version": config["version"],
        "selection_date": selection["selection_date"],
        "paper_count": len(papers),
        "chunk_count": len(chunks),
        "papers": [
            {
                key: paper[key]
                for key in (
                    "paper_id",
                    "pmid",
                    "pmcid",
                    "doi",
                    "title",
                    "year",
                    "study_type",
                    "population",
                    "topics",
                    "source_url",
                    "license_url",
                    "source_sha256",
                )
            }
            for paper in papers
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    validation["output_files"] = {
        "papers": {
            "path": str(papers_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "sha256": sha256_file(papers_path),
        },
        "chunks": {
            "path": str(chunks_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "sha256": sha256_file(chunks_path),
        },
        "manifest": {
            "path": str(manifest_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "sha256": sha256_file(manifest_path),
        },
        "screened_candidates": {
            "path": str(screened_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "sha256": sha256_file(screened_path),
        },
    }
    (reports_dir / "literature_corpus_validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Built {validation['paper_count']} papers and "
        f"{validation['chunk_count']} chunks; all validation checks passed."
    )


if __name__ == "__main__":
    main()
