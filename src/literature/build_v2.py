"""Pinned-selection orchestration and validation; reuses the current JATS builder.

No retrieval, evaluation labels, inference providers, or source discovery here.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET
from urllib.request import urlopen

from .corpus import build_paper_and_chunks, sha256_file, _chunk_section, _slug
from .jats import extract_sections, paper_metadata, node_text, _special_text

SELECTION_SHA256 = "b8aac64d65222093db9370d2cbb954dd0615d36a074cedc3915f3dff8f9b526c"
HASH = re.compile(r"[0-9a-f]{64}\Z")


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def serialize_pinned_article(article: ET.Element) -> bytes:
    """Reproduce preflight's Windows ElementTree.write UTF-8/CRLF bytes.

    Explicit line endings prevent platform-dependent source pin failures.
    This is acquisition serialization, not paragraph text normalization.
    """
    buffer = io.BytesIO()
    with io.TextIOWrapper(buffer, encoding="utf-8", newline="\r\n") as writer:
        ET.ElementTree(article).write(writer, encoding="unicode", xml_declaration=True)
        writer.flush()
        payload = buffer.getvalue()
    return payload


def load_selection(path: Path, expected_hash: str = SELECTION_SHA256,
                   expected_count: int = 26) -> dict:
    if sha256_file(path) != expected_hash:
        raise ValueError("Selection SHA256 mismatch; build prohibited")
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = manifest["paper_selection"]
    if (manifest.get("status") != "SELECTION_FROZEN" or
            manifest.get("MAIN_count") != expected_count or len(rows) != expected_count):
        raise ValueError("Invalid frozen selection count/status")
    for key in ("pmid", "pmcid"):
        if len({r.get(key) for r in rows}) != expected_count:
            raise ValueError(f"Duplicate selection {key}")
    for row in rows:
        if (row.get("selection_role") != "MAIN" or row.get("source_type") != "JATS"
                or not re.fullmatch(r"\d+", str(row.get("pmid", "")))
                or not re.fullmatch(r"PMC\d+", str(row.get("pmcid", "")))
                or not HASH.fullmatch(row["source"].get("source_article_sha256", ""))):
            raise ValueError("Invalid selection identity/source/role")
    return manifest


def acquire_sources(root: Path, rows: list[dict], source_dir: Path,
                    allow_download: bool = False) -> dict[str, Path]:
    """Only acquire the exact manifest URLs; never substitute another source."""
    result = {}
    for row in rows:
        source = row["source"]
        local = source.get("local_path")
        path = root / local if local else source_dir / f"{row['pmcid']}.xml"
        if not path.exists():
            if local or not allow_download:
                raise ValueError(f"Pinned source unavailable: {row['pmcid']}")
            if not source["article_hash_scheme"].startswith("ElementTree.write UTF-8"):
                raise ValueError("Unsupported pinned article serialization")
            with urlopen(source["source_url"], timeout=45) as response:
                payload = response.read()
            transport = ET.fromstring(payload)
            articles = [transport] if transport.tag == "article" else transport.findall("article")
            if len(articles) != 1:
                raise ValueError("Expected exactly one article in pinned transport")
            # Identical serialization to selection preflight, without modifying parser.
            article_bytes = serialize_pinned_article(articles[0])
            if hashlib.sha256(article_bytes).hexdigest() != source["source_article_sha256"]:
                raise ValueError(f"Pinned source hash mismatch: {row['pmcid']}")
            source_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(article_bytes)
            (source_dir / f"{row['pmcid']}.transport.json").write_text(json.dumps({
                "source_url": source["source_url"], "transport_sha256": hashlib.sha256(payload).hexdigest(),
                "pinned_transport_sha256": source.get("transport_response_sha256"),
                "article_sha256": source["source_article_sha256"],
            }, indent=2) + "\n", encoding="utf-8")
        if sha256_file(path) != source["source_article_sha256"]:
            raise ValueError(f"Pinned source hash mismatch: {row['pmcid']}")
        metadata = paper_metadata(path)
        pmcid = str(metadata["pmcid"])
        pmcid = pmcid if pmcid.startswith("PMC") else "PMC" + pmcid
        if str(metadata["pmid"]) != row["pmid"] or pmcid != row["pmcid"]:
            raise ValueError(f"Source identity mismatch: {row['pmcid']}")
        result[row["pmcid"]] = path
    return result


def _body_blocks(body: ET.Element | None) -> list[tuple[str, str]]:
    """Audit current supported direct blocks; rendering remains parser-owned."""
    values = []
    if body is None:
        return values
    for child in body:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "sec":
            values.extend(_body_blocks(child))
        elif tag in {"p", "list", "list-item", "boxed-text", "def-list"}:
            text = node_text(child) if tag == "p" else _special_text(child)
            if text:
                values.append((tag, text))
    return values


def _tokens_preserved(source: list[str], chunks: list[dict], maximum_overlap: int) -> bool:
    """Validate ordered conservation without mistaking repeated source words for overlap."""
    positions = {0}
    previous: list[str] = []
    for chunk in chunks:
        words = chunk["text"].split()
        possible_overlap = [0] + [n for n in range(1, min(maximum_overlap, len(previous), len(words))+1)
                                  if previous[-n:] == words[:n]]
        positions = {p + len(words)-n for p in positions for n in possible_overlap
                     if words[n:] == source[p:p+len(words)-n]}
        if not positions:
            return False
        previous = words
    return len(source) in positions


def _ordinary_body_loss(body: ET.Element | None, rendered: Counter) -> int:
    """Also catch ordinary paragraphs below unexpected, unsupported wrappers."""
    if body is None:
        return 0
    parents = {child: parent for parent in body.iter() for child in parent}
    expected = Counter()
    for node in body.iter():
        if node.tag.rsplit("}", 1)[-1] != "p":
            continue
        ancestors = []
        parent = parents.get(node)
        while parent is not None:
            ancestors.append(parent.tag.rsplit("}", 1)[-1])
            parent = parents.get(parent)
        # Supported special blocks are audited as whole blocks. Tables and footnotes
        # are documented exclusions, not newly introduced ordinary paragraph loss.
        if set(ancestors).intersection({"p", "list", "list-item", "boxed-text", "def-list",
            "table-wrap", "fig", "supplementary-material", "media", "fn", "fn-group"}):
            continue
        value = node_text(node)
        if value:
            expected[value] += 1
    return sum((expected - rendered).values())


def build_once(root: Path, rows: list[dict], sources: dict[str, Path], config: dict) -> dict:
    papers, paragraphs, chunks, statuses = [], [], [], []
    for row in rows:
        path = sources[row["pmcid"]]
        try:
            sections = extract_sections(path)
            # Reuse existing schema. Unreported study/population are not inferred.
            selection = {"pmcid": row["pmcid"], "study_type": "not_reported",
                         "population": "not_reported", "topics": row.get("inclusion_topics", []),
                         "reason": "Frozen MAIN selection; see selection manifest"}
            paper, built_chunks = build_paper_and_chunks(
                xml_path=path, selection=selection, corpus_config=config)
            paper.pop("retrieved_at")  # Date is recorded once in the build manifest.
            if (paper["source_sha256"] != row["source"]["source_article_sha256"]
                    or paper["pmid"] != row["pmid"]):
                raise ValueError("Pinned source hash/identity changed during build")
            paper["source_path"] = path.relative_to(root).as_posix()
            paper["source_type"] = "JATS"
            paper["source_url"] = row["source"]["source_url"]
            frozen_sections = row["provenance"]["section_paragraph_index_and_text_hashes"]
            actual = [{"section_index": i, "section": s.section,
                       "paragraphs": [{"paragraph_index": j, "sha256": text_hash(t)}
                                      for j, t in enumerate(s.paragraphs)]}
                      for i, s in enumerate(sections)]
            preflight_equal = actual == frozen_sections
            xml = ET.parse(path).getroot()
            body_blocks = _body_blocks(xml.find(".//{*}body"))
            rendered_body = Counter(t for s in sections if s.section != "Abstract" for t in s.paragraphs)
            body_loss = max(sum((Counter(t for _, t in body_blocks) - rendered_body).values()),
                            _ordinary_body_loss(xml.find(".//{*}body"), rendered_body))
            special = Counter(t for tag, t in body_blocks if tag != "p")
            special_count = 0
            cursor = 0
            for i, section in enumerate(sections):
                included = not any(x.casefold() in section.section.casefold()
                                   for x in config["chunking"]["excluded_sections"])
                ids = []
                for j, text in enumerate(section.paragraphs, 1):
                    pid = f"{paper['paper_id']}_s{i:03d}_p{j:04d}"
                    ids.append(pid)
                    kind = "abstract" if section.section == "Abstract" else "body"
                    if kind == "body" and special[text]:
                        kind = "special_block"
                        special[text] -= 1
                        special_count += 1
                    paragraphs.append({"paragraph_id": pid, "paper_id": paper["paper_id"],
                                       "pmid": paper["pmid"], "pmcid": paper["pmcid"],
                                       "title": paper["title"], "year": paper["year"],
                                       "section": section.section, "section_index": i,
                                       "paragraph_index": j, "text": text, "text_sha256": text_hash(text),
                                       "block_kind": kind, "included_in_chunks": included,
                                       "source_path": paper["source_path"], "source_type": "JATS",
                                       "source_url": paper["source_url"],
                                       "source_sha256": paper["source_sha256"],
                                       "corpus_version": config["version"]})
                if not included:
                    continue
                # Same chunker call determines ownership for repeated section titles.
                expected_chunks = _chunk_section(section, **{k: config["chunking"][k] for k in
                    ("target_words", "minimum_words", "maximum_words", "overlap_words")})
                for expected in expected_chunks:
                    item = built_chunks[cursor]
                    cursor += 1
                    if any(item[k] != expected[k] for k in ("text", "paragraph_start", "paragraph_end")):
                        raise ValueError("Existing chunker ownership mismatch")
                    item.update(source_type="JATS", source_path=paper["source_path"],
                                source_url=paper["source_url"], section_index=i,
                                source_paragraph_ids=ids[item["paragraph_start"]-1:item["paragraph_end"]])
            if cursor != len(built_chunks):
                raise ValueError("Unassigned chunk")
            abstracts = sum(len(s.paragraphs) for s in sections if s.section == "Abstract")
            warnings = []
            for abstract in xml.findall(".//{*}article-meta/{*}abstract"):
                if abstract.findall("./{*}p") and abstract.findall("./{*}sec"):
                    warnings.append("KNOWN_MIXED_ABSTRACT_DIRECT_P_PRIORITY")
            if xml.findall(".//{*}body//{*}fn-group/{*}fn/{*}p"):
                warnings.append("KNOWN_BODY_FN_GROUP_NOT_INGESTED")
            if not preflight_equal or body_loss or not built_chunks:
                raise ValueError("Unexpected parser loss/preflight change or no chunks")
            statuses.append({"pmid": paper["pmid"], "pmcid": paper["pmcid"],
                             "title": paper["title"], "year": paper["year"],
                             "source_path": paper["source_path"], "source_sha256": paper["source_sha256"],
                             "sections": len(sections), "abstract_paragraph_count": abstracts,
                             "body_paragraph_count": sum(len(s.paragraphs) for s in sections)-abstracts-special_count,
                             "special_block_paragraph_count": special_count,
                             "total_normalized_paragraph_count": sum(len(s.paragraphs) for s in sections),
                             "preflight_exact_match": preflight_equal, "unexpected_parser_loss": body_loss,
                             "parse_status": "PARSE_WARNING" if warnings else "PARSE_OK",
                             "warnings": sorted(set(warnings))})
            papers.append(paper)
            chunks.extend(built_chunks)
        except (ValueError, ET.ParseError, KeyError, IndexError) as error:
            statuses.append({"pmid": row["pmid"], "pmcid": row["pmcid"],
                             "parse_status": "PARSE_FAILED", "error": str(error)})
    return {"papers": sorted(papers, key=lambda r: r["paper_id"]),
            "paragraphs": sorted(paragraphs, key=lambda r: r["paragraph_id"]),
            "chunks": sorted(chunks, key=lambda r: r["chunk_id"]), "parse_status": statuses}


def validate_build(rows: list[dict], build: dict, config: dict) -> dict:
    papers, paragraphs, chunks = (build[k] for k in ("papers", "paragraphs", "chunks"))
    selected = {(r["pmid"], r["pmcid"]) for r in rows}
    built = {(r.get("pmid"), r.get("pmcid")) for r in papers}
    paper_by_id = {r.get("paper_id"): r for r in papers}
    para_by_id = {r.get("paragraph_id"): r for r in paragraphs}
    issues = Counter()
    for kind, records, id_key in (("paper", papers, "paper_id"),
                                  ("paragraph", paragraphs, "paragraph_id"),
                                  ("chunk", chunks, "chunk_id")):
        issues[f"duplicate_{kind}_ids"] = len(records)-len({r.get(id_key) for r in records})
        for record in records:
            issues[f"missing_{kind}_pmid"] += not bool(record.get("pmid"))
            issues[f"missing_{kind}_pmcid"] += not bool(record.get("pmcid"))
            issues[f"missing_{kind}_provenance"] += not all(record.get(k) for k in
                ("source_path", "source_url", "source_type", "source_sha256"))
            malformed = (not record.get(id_key) or record.get("corpus_version") != config["version"]
                         or not isinstance(record.get("title"), str) or not record.get("title")
                         or type(record.get("year")) is not int or not 1 <= record["year"] <= 9999
                         or record.get("source_type") != "JATS"
                         or not re.fullmatch(r"\d+", str(record.get("pmid", "")))
                         or not re.fullmatch(r"PMC\d+", str(record.get("pmcid", "")))
                         or not HASH.fullmatch(str(record.get("source_sha256", ""))))
            parent = paper_by_id.get(record.get("paper_id"))
            if kind != "paper":
                issues[f"orphan_{kind}s"] += parent is None
                issues[f"empty_{kind}s"] += not bool(str(record.get("text", "")).strip())
                section = record.get("section") or ""
                issues[f"missing_{kind}_section"] += not bool(section)
                issues[f"malformed_{kind}_section_path"] += any(not p.strip() for p in section.split(" > "))
                malformed |= (type(record.get("section_index")) is not int or record["section_index"] < 0
                              or record.get("text_sha256") != text_hash(record.get("text", "")))
                if parent:
                    malformed |= any(record.get(k) != parent.get(k) for k in
                                     ("pmid", "pmcid", "title", "year", "source_sha256", "source_path"))
            if kind == "paragraph":
                malformed |= (type(record.get("paragraph_index")) is not int
                              or record["paragraph_index"] < 1)
            if kind == "chunk":
                start, end = record.get("paragraph_start"), record.get("paragraph_end")
                span_ok = type(start) is int and type(end) is int and 1 <= start <= end
                linked = [para_by_id.get(pid) for pid in record.get("source_paragraph_ids", [])]
                ownership = bool(linked) and span_ok and all(p and p["included_in_chunks"] and
                    p["paper_id"] == record["paper_id"] and p["section_index"] == record["section_index"]
                    and p["section"] == record["section"] for p in linked)
                if ownership:
                    ownership = [p["paragraph_index"] for p in linked] == list(range(start, end+1))
                issues["orphan_chunk_spans"] += not ownership
                match = re.fullmatch(re.escape(str(record.get("paper_id"))) + "_" +
                    re.escape(_slug(section)) + r"_(\d{3,})_" + str(record.get("text_sha256", ""))[:8],
                    str(record.get("chunk_id", "")))
                malformed |= match is None or (match is not None and int(match[1]) < 1)
                malformed |= not span_ok or record.get("word_count") != len(record.get("text", "").split())
                issues["chunk_word_limit_exceeded"] += len(record.get("text", "").split()) > (
                    config["chunking"]["maximum_words"] + config["chunking"]["overlap_words"])
            issues[f"malformed_{kind}_metadata"] += bool(malformed)
    groups = defaultdict(list)
    for chunk in chunks:
        groups[(chunk.get("paper_id"), chunk.get("section_index"))].append(chunk)
    # Ordered token conservation checks every included paragraph, across splits/overlap.
    for key in {(p["paper_id"], p["section_index"]) for p in paragraphs if p["included_in_chunks"]}:
        source = sorted((p for p in paragraphs if (p["paper_id"], p["section_index"]) == key),
                        key=lambda p: p["paragraph_index"])
        def order(chunk: dict) -> int:
            parts = str(chunk.get("chunk_id", "")).rsplit("_", 2)
            return int(parts[-2]) if len(parts) == 3 and parts[-2].isdigit() else -1
        ordered = sorted(groups[key], key=order)
        issues["section_token_conservation_failures"] += not _tokens_preserved(
            [w for p in source for w in p["text"].split()], ordered, config["chunking"]["overlap_words"])
    issues["papers_without_chunks"] = sum(not any(c["paper_id"] == p["paper_id"] for c in chunks) for p in papers)
    issues["parse_failed"] = sum(r["parse_status"] == "PARSE_FAILED" for r in build["parse_status"])
    duplicate_texts = {k: v for k, v in Counter(c["text"] for c in chunks).items() if v > 1}
    consistency = {"selected_papers": len(rows), "built_papers": len(papers),
                   "missing_selected_papers": sorted(selected-built), "unexpected_papers": sorted(built-selected),
                   "unexpected_chunk_papers": sorted({(c.get("pmid"), c.get("pmcid")) for c in chunks}-selected)}
    passed = not any(issues.values()) and selected == built and not consistency["unexpected_chunk_papers"]
    return {"all_checks_passed": passed, "issues": dict(sorted(issues.items())), "consistency": consistency,
            "paragraph_statistics": {"total": len(paragraphs), "included": sum(p["included_in_chunks"] for p in paragraphs),
                "duplicates": len(paragraphs)-len({p["text"] for p in paragraphs}),
                "per_paper": dict(Counter(p["pmcid"] for p in paragraphs))},
            "chunk_statistics": {"total": len(chunks), "exact_duplicate_texts": sum(v-1 for v in duplicate_texts.values()),
                "duplicate_text_groups": [{"sha256": text_hash(k), "count": v} for k, v in duplicate_texts.items()],
                "per_paper": dict(Counter(c["pmcid"] for c in chunks)),
                "average_chunks_per_paper": len(chunks)/len(papers) if papers else 0},
            "sections": len({(p["paper_id"], p["section_index"]) for p in paragraphs}),
            "unique_pmid": len({p["pmid"] for p in papers}), "unique_pmcid": len({p["pmcid"] for p in papers})}


def check_evidence(build: dict, checks: list[dict]) -> dict:
    """Post-build preservation only; never passed into parser/chunker/runtime."""
    results = []
    for check in checks:
        matches = []
        for paragraph in build["paragraphs"]:
            if paragraph["pmid"] != check["pmid"] or paragraph["block_kind"] == "abstract":
                continue
            if not re.search(check.get("section_pattern", ".*"), paragraph["section"], re.I):
                continue
            if not all(re.search(pattern, paragraph["text"], re.I) for pattern in check["patterns"]):
                continue
            linked = [c for c in build["chunks"] if paragraph["paragraph_id"] in c["source_paragraph_ids"]]
            if all(re.search(pattern, " ".join(c["text"] for c in linked), re.I) for pattern in check["patterns"]):
                matches.append({"section": paragraph["section"], "paragraph_id": paragraph["paragraph_id"],
                                "paragraph_sha256": paragraph["text_sha256"],
                                "chunk_ids": [c["chunk_id"] for c in linked]})
        results.append({"pmid": check["pmid"], "requirement": check["requirement"],
                        "passed": bool(matches), "matches": matches})
    return {"all_checks_passed": bool(results) and all(r["passed"] for r in results), "checks": results}


def compare_builds(first: dict, second: dict) -> dict:
    checks = {key: first[key] == second[key] for key in ("papers", "paragraphs", "chunks", "parse_status")}
    hashes = [content_hash({k: b[k] for k in ("papers", "paragraphs", "chunks")}) for b in (first, second)]
    return {"result": "PASS" if all(checks.values()) and hashes[0] == hashes[1] else "FAIL",
            "checks": checks, "corpus_content_hashes": hashes,
            "comparison": "Two independent parse/build passes; no build timestamp in content"}


def validate_manifest(manifest: dict) -> None:
    required = ("corpus_version", "selection_manifest", "parser", "chunker", "config", "papers",
                "selected_paper_count", "built_paper_count", "paragraph_count", "chunk_count",
                "corpus_content_hash", "determinism", "build_timestamp", "output_files")
    if not all(manifest.get(k) for k in required):
        raise ValueError("Missing build manifest field")
    if (manifest["selected_paper_count"] != manifest["built_paper_count"]
            or len(manifest["papers"]) != manifest["built_paper_count"]
            or len({(p["pmid"], p["pmcid"]) for p in manifest["papers"]}) != manifest["built_paper_count"]
            or manifest["determinism"] != "PASS" or manifest.get("status") != "BUILD_FROZEN"):
        raise ValueError("Invalid freeze manifest status/count/determinism")
    references = [manifest[k] for k in ("selection_manifest", "parser", "chunker", "config")]
    references += list(manifest["output_files"].values())
    if any(not r.get("path") or not HASH.fullmatch(r.get("sha256", "")) for r in references):
        raise ValueError("Invalid manifest reference/hash")
    if not HASH.fullmatch(manifest["corpus_content_hash"]):
        raise ValueError("Invalid corpus hash")
    if any(not HASH.fullmatch(p.get("source_sha256", "")) for p in manifest["papers"]):
        raise ValueError("Invalid paper source hash")
