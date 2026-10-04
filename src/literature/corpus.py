"""Acquire selected PMC sources and build deterministic paper/chunk artifacts."""

from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable
from urllib.request import Request, urlopen

from .jats import SectionText, extract_sections, paper_metadata


EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
PMC_OAI = "https://www.ncbi.nlm.nih.gov/pmc/oai/oai.cgi"
USER_AGENT = "agentic-rag-training/1.0 (literature corpus research)"
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_SLUG = re.compile(r"[^a-z0-9]+")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_pmc_xml(pmcid: str, output_path: Path, *, attempts: int = 3) -> None:
    numeric_id = pmcid.upper().removeprefix("PMC")
    urls = (
        f"{EUROPE_PMC}/{pmcid}/fullTextXML",
        f"{PMC_OAI}?verb=GetRecord&identifier=oai:pubmedcentral.nih.gov:"
        f"{numeric_id}&metadataPrefix=pmc",
    )
    last_error: Exception | None = None
    for url in urls:
        for attempt in range(attempts):
            try:
                request = Request(url, headers={"User-Agent": USER_AGENT})
                with urlopen(request, timeout=60) as response:
                    payload = response.read()
                if not payload.lstrip().startswith(b"<"):
                    raise ValueError(f"Unexpected non-XML response for {pmcid}")
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(payload)
                time.sleep(0.36)
                return
            except Exception as error:
                last_error = error
                if attempt + 1 < attempts:
                    time.sleep(2 * (attempt + 1))
    assert last_error is not None
    raise RuntimeError(f"Could not download {pmcid} from Europe PMC") from last_error


def _slug(value: str) -> str:
    return _SLUG.sub("_", value.casefold()).strip("_")[:48] or "section"


def _split_long_paragraph(text: str, maximum_words: int) -> list[str]:
    sentences = _SENTENCE_BOUNDARY.split(text)
    values: list[str] = []
    current: list[str] = []
    current_count = 0
    for sentence in sentences:
        words = sentence.split()
        if current and current_count + len(words) > maximum_words:
            values.append(" ".join(current))
            current = []
            current_count = 0
        if len(words) > maximum_words:
            for offset in range(0, len(words), maximum_words):
                if current:
                    values.append(" ".join(current))
                    current = []
                    current_count = 0
                values.append(" ".join(words[offset : offset + maximum_words]))
        else:
            current.append(sentence)
            current_count += len(words)
    if current:
        values.append(" ".join(current))
    return values


def _chunk_section(
    section: SectionText,
    *,
    target_words: int,
    minimum_words: int,
    maximum_words: int,
    overlap_words: int,
) -> list[dict[str, Any]]:
    units: list[tuple[str, int]] = []
    for paragraph_index, paragraph in enumerate(section.paragraphs, start=1):
        pieces = _split_long_paragraph(paragraph, maximum_words)
        units.extend((piece, paragraph_index) for piece in pieces)

    chunks: list[dict[str, Any]] = []
    current_texts: list[str] = []
    paragraph_numbers: list[int] = []
    current_words = 0
    # A short source paragraph is not an overlap-only buffer.
    current_new_words = 0
    carried_overlap_words = 0
    for text, paragraph_number in units:
        words = text.split()
        if current_texts and current_words + len(words) > maximum_words:
            if not current_new_words:
                current_texts = []
                paragraph_numbers = []
                current_words = 0
                carried_overlap_words = 0
            else:
                combined = "\n\n".join(current_texts)
                chunks.append(
                    {
                        "text": combined,
                        "paragraph_start": min(paragraph_numbers),
                        "paragraph_end": max(paragraph_numbers),
                    }
                )
                overlap = combined.split()[-overlap_words:] if overlap_words else []
                current_texts = [" ".join(overlap)] if overlap else []
                paragraph_numbers = [max(paragraph_numbers)] if overlap else []
                current_words = len(overlap)
                carried_overlap_words = len(overlap)
                current_new_words = 0
        current_texts.append(text)
        paragraph_numbers.append(paragraph_number)
        current_words += len(words)
        current_new_words += len(words)
        if current_words >= target_words:
            combined = "\n\n".join(current_texts)
            chunks.append(
                {
                    "text": combined,
                    "paragraph_start": min(paragraph_numbers),
                    "paragraph_end": max(paragraph_numbers),
                }
            )
            overlap = combined.split()[-overlap_words:] if overlap_words else []
            current_texts = [" ".join(overlap)] if overlap else []
            paragraph_numbers = [max(paragraph_numbers)] if overlap else []
            current_words = len(overlap)
            carried_overlap_words = len(overlap)
            current_new_words = 0
    if current_texts and current_new_words:
        final_text = "\n\n".join(current_texts)
        unique_tail = final_text.split()[carried_overlap_words:]
        merged_word_count = (
            len(chunks[-1]["text"].split()) + len(unique_tail) if chunks else 0
        )
        if (
            chunks
            and len(unique_tail) < minimum_words
            and merged_word_count <= maximum_words + overlap_words
        ):
            chunks[-1]["text"] = chunks[-1]["text"] + "\n\n" + " ".join(unique_tail)
            chunks[-1]["paragraph_end"] = max(paragraph_numbers)
        else:
            chunks.append(
                {
                    "text": final_text,
                    "paragraph_start": min(paragraph_numbers),
                    "paragraph_end": max(paragraph_numbers),
                }
            )
    return chunks


def build_paper_and_chunks(
    *,
    xml_path: Path,
    selection: dict[str, Any],
    corpus_config: dict[str, Any],
    pubmed_metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    parsed = paper_metadata(xml_path)
    if pubmed_metadata:
        for field in ("pmid", "pmcid", "doi", "title", "authors", "year", "journal"):
            if pubmed_metadata.get(field):
                parsed[field] = pubmed_metadata[field]
    pmcid = str(selection["pmcid"]).upper()
    parsed_pmcid = str(parsed["pmcid"]).upper()
    if parsed_pmcid and parsed_pmcid != pmcid:
        raise ValueError(f"PMCID mismatch for {xml_path}: {parsed_pmcid} != {pmcid}")

    paper_id = f"paper_{pmcid.casefold()}"
    source_hash = sha256_file(xml_path)
    paper = {
        **parsed,
        "paper_id": paper_id,
        "pmcid": pmcid,
        "study_type": selection["study_type"],
        "population": selection["population"],
        "topics": selection["topics"],
        "selection_reason": selection["reason"],
        "source_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/",
        "source_format": "jats_xml",
        "source_sha256": source_hash,
        "corpus_version": corpus_config["version"],
        "retrieved_at": date.today().isoformat(),
    }

    excluded = tuple(
        value.casefold() for value in corpus_config["chunking"]["excluded_sections"]
    )
    chunks: list[dict[str, Any]] = []
    section_counts: dict[str, int] = {}
    for section in extract_sections(xml_path):
        if any(value in section.section.casefold() for value in excluded):
            continue
        section_chunks = _chunk_section(
            section,
            target_words=int(corpus_config["chunking"]["target_words"]),
            minimum_words=int(corpus_config["chunking"]["minimum_words"]),
            maximum_words=int(corpus_config["chunking"]["maximum_words"]),
            overlap_words=int(corpus_config["chunking"]["overlap_words"]),
        )
        for item in section_chunks:
            section_counts[section.section] = section_counts.get(section.section, 0) + 1
            index = section_counts[section.section]
            text_hash = hashlib.sha256(item["text"].encode("utf-8")).hexdigest()
            chunk_id = f"{paper_id}_{_slug(section.section)}_{index:03d}_{text_hash[:8]}"
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "paper_id": paper_id,
                    "pmid": paper["pmid"],
                    "pmcid": pmcid,
                    "doi": paper["doi"],
                    "title": paper["title"],
                    "year": paper["year"],
                    "study_type": paper["study_type"],
                    "population": paper["population"],
                    "topics": paper["topics"],
                    "section": section.section,
                    "paragraph_start": item["paragraph_start"],
                    "paragraph_end": item["paragraph_end"],
                    "word_count": len(item["text"].split()),
                    "text": item["text"],
                    "source_url": paper["source_url"],
                    "source_sha256": source_hash,
                    "text_sha256": text_hash,
                    "corpus_version": paper["corpus_version"],
                }
            )
    return paper, chunks


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
