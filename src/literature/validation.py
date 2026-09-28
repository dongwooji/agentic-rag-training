"""Validation for the frozen literature corpus artifacts."""

from __future__ import annotations

from collections import Counter
from typing import Any


def validate_corpus(
    papers: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    required = set(config["required_paper_metadata"])
    paper_ids = [str(paper["paper_id"]) for paper in papers]
    chunk_ids = [str(chunk["chunk_id"]) for chunk in chunks]
    pmids = [str(paper["pmid"]) for paper in papers]
    pmcids = [str(paper["pmcid"]) for paper in papers]
    dois = [str(paper["doi"]) for paper in papers if paper.get("doi")]
    selected_paper_ids = set(paper_ids)
    chunks_by_paper = Counter(str(chunk["paper_id"]) for chunk in chunks)
    topic_counts = Counter(
        topic for paper in papers for topic in paper.get("topics", [])
    )
    section_counts = Counter(str(chunk["section"]) for chunk in chunks)
    paper_section_counts = Counter(
        (str(chunk["paper_id"]), str(chunk["section"])) for chunk in chunks
    )
    word_counts = [int(chunk["word_count"]) for chunk in chunks]
    short_chunks = [
        chunk
        for chunk in chunks
        if int(chunk["word_count"]) < int(config["chunking"]["minimum_words"])
    ]

    checks = {
        "paper_count_in_target_range": int(config["target_paper_count"]["minimum"])
        <= len(papers)
        <= int(config["target_paper_count"]["maximum"]),
        "paper_ids_unique": len(paper_ids) == len(set(paper_ids)),
        "chunk_ids_unique": len(chunk_ids) == len(set(chunk_ids)),
        "pmids_unique_and_present": bool(pmids)
        and all(pmids)
        and len(pmids) == len(set(pmids)),
        "pmcids_unique_and_present": bool(pmcids)
        and all(pmcids)
        and len(pmcids) == len(set(pmcids)),
        "dois_unique": len(dois) == len(set(dois)),
        "required_metadata_present": all(
            required.issubset(paper) and all(paper.get(field) for field in required)
            for paper in papers
        ),
        "every_paper_has_chunks": all(chunks_by_paper[paper_id] > 0 for paper_id in paper_ids),
        "every_chunk_maps_to_paper": all(
            str(chunk["paper_id"]) in selected_paper_ids for chunk in chunks
        ),
        "chunk_text_and_hash_present": all(
            chunk.get("text") and chunk.get("text_sha256") for chunk in chunks
        ),
        "chunk_word_limit_respected": all(
            count <= int(config["chunking"]["maximum_words"])
            + int(config["chunking"]["overlap_words"])
            for count in word_counts
        ),
        "short_chunks_are_complete_sections": all(
            paper_section_counts[(str(chunk["paper_id"]), str(chunk["section"]))] == 1
            for chunk in short_chunks
        ),
        "corpus_version_constant": all(
            paper["corpus_version"] == config["version"] for paper in papers
        )
        and all(chunk["corpus_version"] == config["version"] for chunk in chunks),
        "source_hash_present": all(
            len(str(paper["source_sha256"])) == 64 for paper in papers
        ),
        "topic_coverage_present": all(
            topic_counts[topic] > 0
            for topic in (
                "training_volume",
                "training_frequency",
                "strength_adaptation",
                "detraining",
                "training_to_failure",
                "periodization_progression",
            )
        ),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise ValueError(f"Literature corpus validation failed: {failed}")

    return {
        "corpus_version": config["version"],
        "all_checks_passed": True,
        "checks": checks,
        "paper_count": len(papers),
        "chunk_count": len(chunks),
        "chunks_per_paper": dict(sorted(chunks_by_paper.items())),
        "topic_counts": dict(sorted(topic_counts.items())),
        "section_count": len(section_counts),
        "section_chunk_counts": dict(section_counts.most_common()),
        "word_counts": {
            "minimum": min(word_counts),
            "maximum": max(word_counts),
            "mean": round(sum(word_counts) / len(word_counts), 2),
            "below_soft_minimum": len(short_chunks),
        },
    }
