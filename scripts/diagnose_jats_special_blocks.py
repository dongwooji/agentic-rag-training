"""Read-only, source-owned comparison of the 22 frozen articles.

Use --before-ref fef9625 or --before-source for the historical parser. Only a new
diagnostic directory is written. Gold/evaluation files are opaque hash inputs.
This measures text preservation, not scientific validity or retrieval quality.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.literature import jats
from src.literature.corpus import _chunk_section, _slug
from scripts.diagnose_short_section_chunking import Coverage, duplicates, read_jsonl, stats

SPECIAL = {"list", "list-item", "boxed-text", "def-list"}
EXCLUDED = {"table-wrap", "fig", "supplementary-material", "media"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def historical_parser(source):
    module = ModuleType("_historical_jats_special_diagnostic")
    sys.modules[module.__name__] = module
    exec(compile(source, "<historical-jats>", "exec"), module.__dict__)
    return module


def inventory(root):
    """Independent XML paths and body-block ownership, including excluded nodes."""
    paths, parents = {}, {}

    def visit(node, path):
        paths[node] = path
        counts = Counter()
        for child in node:
            counts[child.tag] += 1
            parents[child] = node
            visit(child, f"{path}/{child.tag}[{counts[child.tag]}]")

    visit(root, f"/{root.tag}[1]")
    selected = {}

    def body_blocks(node, titles):
        for child in node:
            if child.tag == "sec":
                title = jats.node_text(child.find("./title")) or child.get("sec-type", "section")
                body_blocks(child, (*titles, title))
            elif child.tag == "p" or child.tag in SPECIAL:
                selected[child] = " > ".join(titles) or "Body"

    body = root.find(".//body")
    if body is not None:
        body_blocks(body, ())
    return paths, parents, selected


def traced_extract(module, path, tree, selected, paths):
    """Observe actual renderer calls without changing their values or traversal."""
    emissions = defaultdict(list)
    node_text = module.node_text

    def text(node):
        value = node_text(node)
        if node in selected and node.tag == "p" and value:
            emissions[paths[node]].append(value)
        return value

    with patch.object(module.ET, "parse", return_value=tree), patch.object(module, "node_text", text):
        if hasattr(module, "_special_text"):
            renderer = module._special_text

            def special(node):
                value = renderer(node)
                if node in selected and node.tag in SPECIAL and value:
                    emissions[paths[node]].append(value)
                return value

            with patch.object(module, "_special_text", special):
                sections = module.extract_sections(path)
        else:
            sections = module.extract_sections(path)
    # Counters tolerate identical text at distinct source positions, but catch
    # parent/child re-emission or repeated output beyond those source positions.
    expected = Counter((selected[node], value) for node in selected
                       for value in emissions.get(paths[node], []))
    actual = Counter((s.section, p) for s in sections if s.section != "Abstract" for p in s.paragraphs)
    if actual != expected:
        raise AssertionError("Parser outputs do not match traced source-owned emissions")
    return sections, emissions


def source_spans(node):
    """Leaf text in order; inserted list markers/definition separators are ignored."""
    if node.tag.rsplit("}", 1)[-1] in EXCLUDED:
        return []
    if node.tag in {"p", "title", "label", "caption", "term"}:
        value = jats.node_text(node)
        return [value] if value else []
    result = []
    value = jats.clean_text(node.text or "")
    if value:
        result.append(value)
    for child in node:
        result.extend(source_spans(child))
        value = jats.clean_text(child.tail or "")
        if value:
            result.append(value)
    return result


def spans_preserved(node, text):
    target = jats.clean_text(text)
    cursor = 0
    spans = source_spans(node)
    for span in spans:
        position = target.find(span, cursor)
        if position < 0:
            return False
        cursor = position + len(span)
    return bool(spans)


def coverage_index(records):
    grouped = defaultdict(list)
    for record in records:
        grouped[record["section"]].append(record)
    return {section: Coverage(values) for section, values in grouped.items()}


def generate_chunks(paper, sections, config):
    chunks, counts = [], Counter()
    params = {k: config[k] for k in ("target_words", "minimum_words", "maximum_words", "overlap_words")}
    for section in sections:
        if any(x.casefold() in section.section.casefold() for x in config["excluded_sections"]):
            continue
        for item in _chunk_section(section, **params):
            counts[section.section] += 1
            digest = hashlib.sha256(item["text"].encode("utf-8")).hexdigest()
            cid = f"{paper['paper_id']}_{_slug(section.section)}_{counts[section.section]:03d}_{digest[:8]}"
            chunks.append(dict(item, chunk_id=cid, pmcid=paper["pmcid"], section=section.section,
                               text_sha256=digest, word_count=len(item["text"].split())))
    return chunks


def run(root, output, source, protected_hashes):
    root, output = root.resolve(), output.resolve()
    if not output.is_relative_to(root / "reports/diagnostics") or output.exists():
        raise ValueError("Output must be a new reports/diagnostics directory")
    hashes = json.loads(protected_hashes.read_text(encoding="utf-8"))

    def check_hashes():
        changed = [p for p, h in hashes.items() if not (root / p).is_file() or sha(root / p) != h]
        if changed:
            raise AssertionError(f"Protected files changed: {changed}")
        return {p: sha(root / p) for p in hashes}

    check_hashes()
    old = historical_parser(source)
    audit = root / "reports/diagnostics/jats_parser_v1"
    rows = read_jsonl(audit / "block_audit.jsonl")
    ordinary = [r for r in rows if r["ordinary_body_narrative"]]
    omissions = [r for r in rows if r["status"] == "parser_omitted" and r["block_type"] in
                 {"list", "list-item", "boxed-text", "def-list", "def-item"}]
    losses = [(p["pmcid"], loss) for p in json.loads((audit / "per_paper.json").read_text(encoding="utf-8"))
              for loss in p["chunking_loss_candidates"]]
    papers = read_jsonl(root / "data/literature/processed/papers.jsonl")
    frozen_chunks = read_jsonl(root / "data/literature/processed/chunks.jsonl")
    config = json.loads((root / "config/literature_corpus_v1.json").read_text(encoding="utf-8"))["chunking"]
    if (len(papers), len(frozen_chunks), len(ordinary), len(losses)) != (22, 488, 811, 105):
        raise AssertionError("Unexpected frozen input inventory")
    generated = {"before": [], "after": []}
    recovered, body_checks, loss_checks, per_paper, added, exclusion_checks = [], [], [], [], [], []
    duplicate_emissions = empty_blocks = 0
    for paper in sorted(papers, key=lambda x: x["pmcid"]):
        pmc = paper["pmcid"]
        path = root / "data/literature/raw" / f"{pmc}.xml"
        tree = ET.parse(path)
        paths, parents, selected = inventory(tree.getroot())
        by_path = {v: k for k, v in paths.items()}
        phases, emitted = {}, {}
        chunk_indices, parser_indices = {}, {}
        for phase, module in (("before", old), ("after", jats)):
            phases[phase], emitted[phase] = traced_extract(module, path, tree, selected, paths)
            chunks = generate_chunks(paper, phases[phase], config)
            generated[phase].extend(chunks)
            chunk_indices[phase] = coverage_index(chunks)
            parser_indices[phase] = coverage_index([
                {"section": s.section, "text": p, "chunk_id": f"{i}:{n}"}
                for i, s in enumerate(phases[phase]) for n, p in enumerate(s.paragraphs)])
        assert [(s.section, s.paragraphs) for s in phases["before"] if s.section == "Abstract"] == [
            (s.section, s.paragraphs) for s in phases["after"] if s.section == "Abstract"]
        assert old.paper_metadata(path) == jats.paper_metadata(path)
        duplicate_emissions += sum(max(0, len(values)-1) for values in emitted["after"].values())
        empty_blocks += sum(not p.strip() for s in phases["after"] for p in s.paragraphs)

        def owner(node):
            while node is not None:
                if node.tag.rsplit("}", 1)[-1] in EXCLUDED:
                    return None
                if node in selected:
                    return node
                node = parents.get(node)
            return None

        for row in (r for r in omissions if r["pmcid"] == pmc):
            node = by_path[row["source_path"]]
            owning_node = owner(node)
            origin = paths[owning_node] if owning_node is not None else None
            checks = {phase: bool(origin and len(emitted[phase].get(origin, [])) == 1 and
                                  spans_preserved(node, emitted[phase][origin][0])) for phase in phases}
            section = selected.get(owning_node)
            spans = source_spans(node)
            after_chunks = bool(checks["after"] and spans and all(
                chunk_indices["after"].get(section, Coverage([])).match(span)["complete"] for span in spans))
            recovered.append({"pmcid": pmc, "source_path": paths[node], "type": node.tag,
                              "owning_block": origin, "section": section, "after_chunks": after_chunks, **checks})
        for node in selected:
            origin = paths[node]
            if node.tag in SPECIAL and origin not in emitted["before"]:
                value = emitted["after"].get(origin, [])
                added.append({"pmcid": pmc, "source_path": origin, "type": node.tag,
                              "section": selected[node], "text": value[0] if value else "",
                              "interpretation": "abbreviation interpretation aid; low value as standalone evidence"
                              if node.tag == "def-list" else "scientific summary, recommendation, purpose or methods"})
        for row in (r for r in rows if r["pmcid"] == pmc and r["block_type"] in {"list", "list-item"}
                    and r["status"] == "intentionally_excluded"):
            exclusion_checks.append({"pmcid": pmc, "source_path": row["source_path"],
                                     "type": row["block_type"], "still_excluded": owner(by_path[row["source_path"]]) is None})
        for row in (r for r in ordinary if r["pmcid"] == pmc):
            entry = {"pmcid": pmc, "source_path": row["source_path"], "section": row["section_path"]}
            for phase in phases:
                key = row["section_path"]
                entry[phase] = {"parser": parser_indices[phase].get(key, Coverage([])).match(row["comparison_text"])["complete"],
                                "chunks": chunk_indices[phase].get(key, Coverage([])).match(row["comparison_text"])["complete"],
                                "selected_source_once": len(emitted[phase].get(row["source_path"], [])) == 1}
            body_checks.append(entry)
        for _, loss in (x for x in losses if x[0] == pmc):
            loss_checks.append({"pmcid": pmc, "section": loss["section"], "text": loss["text"],
                                **{phase: chunk_indices[phase].get(loss["section"], Coverage([])).match(loss["text"])["complete"]
                                   for phase in phases}})
        per_paper.append({"pmcid": pmc, "new_logical_blocks": sum(x["pmcid"] == pmc for x in added),
                          "recovered_by_type": dict(Counter(x["type"] for x in recovered if x["pmcid"] == pmc and x["after"])),
                          "all_audited_special_blocks_recovered": all(x["after"] for x in recovered if x["pmcid"] == pmc)})

    previous = read_jsonl(root / "reports/diagnostics/short_section_chunking_v1/counterfactual_chunks.jsonl")
    keys = ("chunk_id", "pmcid", "section", "paragraph_start", "paragraph_end", "text", "text_sha256", "word_count")
    projection = lambda values: [{k: v[k] for k in keys} for v in sorted(values, key=lambda v: v["chunk_id"])]
    assert projection(generated["before"]) == projection(previous), "Before must reproduce prior fixed-chunker diagnostic"
    totals = Counter(x["type"] for x in recovered)
    after_totals = Counter(x["type"] for x in recovered if x["after"])
    assert totals == {"list": 10, "list-item": 37, "boxed-text": 2, "def-list": 2, "def-item": 26}
    assert totals == after_totals and all(not r["before"] for r in recovered)
    assert all(r["after_chunks"] for r in recovered)
    assert all(r[p][k] for r in body_checks for p in ("before", "after") for k in ("parser", "chunks", "selected_source_once"))
    assert all(r["before"] and r["after"] for r in loss_checks)
    assert duplicate_emissions == empty_blocks == 0 and all(r["still_excluded"] for r in exclusion_checks)
    assert len(added) == 11 and all(a["text"] for a in added)
    after_hashes = check_hashes()
    metrics = {"scope": "text preservation counterfactual only; no retrieval evaluation or corpus rebuild",
               "paper_count": 22, "frozen_chunk_count": 488,
               "scientific_list_box_before_omissions": 12, "scientific_list_box_after_omissions": 0,
               "count_caveat": "10 lists + 2 boxes overlap: 3 lists are inside one box. Nine independent list/box blocks; two additional definition blocks.",
               "recovered_by_type": dict(after_totals), "new_logical_blocks": len(added),
               "all_recovered_special_block_leaf_text_preserved_in_chunks": True,
               "recovered_scientific_list_box_papers": 5, "recovered_papers_including_definitions": 6,
               "ordinary_body_total": 811, "ordinary_body_parser_preserved": 811, "ordinary_body_chunk_preserved": 811,
               "previous_short_section_recoveries_retained": 105,
               "duplicate_source_block_emissions": duplicate_emissions, "empty_blocks": empty_blocks,
               "table_list_exclusions_retained": dict(Counter(r["type"] for r in exclusion_checks)),
               "low_value_additions": {"independent_abbreviation_blocks": 2, "term_definition_pairs": 26,
                                       "author_conflict_publisher_copyright_keyword_blocks": 0,
                                       "classification": "manual review of all eleven added blocks; no semantic filter"},
               "abstract_and_metadata_unchanged_all_papers": True,
               "before_reproduces_previous_short_section_diagnostic": True,
               "chunk_statistics": {p: stats(v) for p, v in generated.items()},
               "chunk_duplicates": {p: duplicates(v) for p, v in generated.items()},
               "historical_parser_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
               "current_parser_sha256": sha(root / "src/literature/jats.py"),
               "unchanged_chunker_sha256": sha(root / "src/literature/corpus.py"),
               "protected_files_unchanged": len(hashes), "protected_hash_changes": [],
               "methods": ["Actual renderer calls traced by XML source path, one owning block per emission",
                           "Special-block leaf spans present in source order; inserted bullets/colon ignored",
                           "Ordinary paragraphs/chunk losses: section-local NFKC/casefold five-gram coverage and source ownership",
                           "All newly added blocks manually reviewed; overlapping container/item counts reported separately"]}
    output.mkdir(parents=True)
    for name, data in (("metrics", metrics), ("per_paper", per_paper), ("special_block_recovery", recovered),
                       ("ordinary_body_preservation", body_checks), ("short_section_preservation", loss_checks),
                       ("new_logical_blocks", added), ("excluded_lists", exclusion_checks),
                       ("protected_hashes_before", hashes), ("protected_hashes_after", after_hashes)):
        (output / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/diagnostics/jats_special_blocks_v1")
    parser.add_argument("--protected-hashes", type=Path, required=True)
    before = parser.add_mutually_exclusive_group(required=True)
    before.add_argument("--before-source", type=Path)
    before.add_argument("--before-ref")
    args = parser.parse_args()
    source = args.before_source.read_text(encoding="utf-8") if args.before_source else subprocess.check_output(
        ["git", "show", f"{args.before_ref}:src/literature/jats.py"], cwd=args.root).decode("utf-8")
    print(json.dumps(run(args.root, args.output, source, args.protected_hashes), ensure_ascii=True))


if __name__ == "__main__":
    main()
