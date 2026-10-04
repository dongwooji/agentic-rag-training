"""Counterfactual chunking of frozen v1 sources; no corpus/retrieval writes.

The parser, settings and input papers stay fixed. A pre-fix source snapshot is
used only to run the two historical chunking functions in memory. Gold is not
read except as opaque bytes for integrity hashes.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.literature.corpus import _chunk_section, _slug
from src.literature.jats import SectionText, extract_sections


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def normalized_tokens(text):
    return re.findall(r'\w+|[^\w\s]', unicodedata.normalize('NFKC', text).casefold())


class Coverage:
    """Same local 5-token coverage measure as the preservation audit."""
    def __init__(self, chunks):
        self.units = [(c['chunk_id'], normalized_tokens(c['text'])) for c in chunks]
        self.grams = defaultdict(set)
        for key, values in self.units:
            for i in range(len(values) - 4):
                self.grams[tuple(values[i:i+5])].add(key)

    def match(self, text):
        values = normalized_tokens(text)
        covered = set()
        mapped = defaultdict(set)
        if len(values) < 5:
            for key, target in self.units:
                if any(target[i:i+len(values)] == values for i in range(len(target)-len(values)+1)):
                    covered.update(range(len(values)))
                    mapped[key].update(range(len(values)))
        else:
            for i in range(len(values)-4):
                for key in self.grams.get(tuple(values[i:i+5]), ()):
                    covered.update(range(i, i+5))
                    mapped[key].update(range(i, i+5))
        threshold = min(len(values), max(5, min(12, int(len(values)*0.2))))
        return {'complete': bool(values) and len(covered) == len(values),
                'coverage': len(covered)/len(values) if values else None,
                'chunk_ids': sorted(k for k, v in mapped.items() if len(v) >= threshold)}


def historical_chunker(source):
    """Compile only the historical split/chunk functions, no module side effects."""
    parsed = ast.parse(source)
    wanted = {'_split_long_paragraph', '_chunk_section'}
    functions = [n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    boundary = [n for n in parsed.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == '_SENTENCE_BOUNDARY' for t in n.targets)]
    if len(functions) != 2 or len(boundary) != 1:
        raise ValueError('Historical chunker source structure is unexpected')
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                             *boundary, *functions], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {'re': re, 'SectionText': SectionText}
    exec(compile(module, '<historical-chunker>', 'exec'), namespace)
    return namespace['_chunk_section']


def stats(chunks):
    sizes = [len(c['text'].split()) for c in chunks]
    return {'count': len(sizes), 'mean_words': statistics.mean(sizes),
            'median_words': statistics.median(sizes), 'min_words': min(sizes), 'max_words': max(sizes)}


def duplicates(chunks):
    ids = Counter(c['chunk_id'] for c in chunks)
    same_section = Counter((c['pmcid'], c['section'], ' '.join(c['text'].split())) for c in chunks)
    global_text = Counter(' '.join(c['text'].split()) for c in chunks)
    return {'duplicate_chunk_ids': sum(n-1 for n in ids.values() if n > 1),
            'same_paper_section_identical_text_extras': sum(n-1 for n in same_section.values() if n > 1),
            'global_identical_text_groups': sum(n > 1 for n in global_text.values()),
            'global_identical_text_extras': sum(n-1 for n in global_text.values() if n > 1)}


def run(root, output, audit, before_source, protected_hashes):
    root, output, audit = root.resolve(), output.resolve(), audit.resolve()
    if not output.is_relative_to(root/'reports/diagnostics'):
        raise ValueError('Output must be a new reports/diagnostics directory')
    if output.exists():
        raise FileExistsError('Refusing to overwrite diagnostic output')
    expected_hashes = json.loads(protected_hashes.read_text(encoding='utf-8'))
    changed = [p for p, h in expected_hashes.items() if not (root/p).is_file() or sha(root/p) != h]
    if changed:
        raise ValueError(f'Protected inputs changed: {changed}')
    config = json.loads((root/'config/literature_corpus_v1.json').read_text(encoding='utf-8'))['chunking']
    params = {key: config[key] for key in ['target_words', 'minimum_words', 'maximum_words', 'overlap_words']}
    papers = read_jsonl(root/'data/literature/processed/papers.jsonl')
    existing = read_jsonl(root/'data/literature/processed/chunks.jsonl')
    old = historical_chunker(before_source)
    audit_papers = json.loads((audit/'per_paper.json').read_text(encoding='utf-8'))
    old_losses = {(p['pmcid'], t['section_index'], t['paragraph_index']): t
                  for p in audit_papers for t in p['chunking_loss_candidates']}
    ordinary = [r for r in read_jsonl(audit/'block_audit.jsonl') if r['ordinary_body_narrative']]
    if len(papers) != 22 or len(existing) != 488 or len(old_losses) != 105 or len(ordinary) != 811:
        raise ValueError('Unexpected frozen audit/corpus inventory')
    generated = {'before': [], 'after': []}
    per_paper = []
    transitions = []
    all_paragraphs = []
    changed_sections = []
    remaining = []
    for paper in sorted(papers, key=lambda p: p['pmcid']):
        pmc = paper['pmcid']
        section_counts = {'before': Counter(), 'after': Counter()}
        paper_chunks = {'before': [], 'after': []}
        for i, section in enumerate(extract_sections(root/'data/literature/raw'/f'{pmc}.xml')):
            if any(s.casefold() in section.section.casefold() for s in config['excluded_sections']):
                continue
            results = {'before': old(section, **params), 'after': _chunk_section(section, **params)}
            section_chunks = {'before': [], 'after': []}
            for phase, items in results.items():
                for item in items:
                    section_counts[phase][section.section] += 1
                    h = hashlib.sha256(item['text'].encode('utf-8')).hexdigest()
                    cid = f"{paper['paper_id']}_{_slug(section.section)}_{section_counts[phase][section.section]:03d}_{h[:8]}"
                    record = dict(item, pmcid=pmc, section=section.section, section_index=i,
                                  chunk_id=cid, text_sha256=h, word_count=len(item['text'].split()))
                    section_chunks[phase].append(record)
                    paper_chunks[phase].append(record)
            indices = {phase: Coverage(values) for phase, values in section_chunks.items()}
            if results['before'] != results['after']:
                expected = any(k[0] == pmc and k[1] == i for k in old_losses)
                changed_sections.append({'pmcid': pmc, 'section_index': i, 'section': section.section,
                                         'before_count': len(results['before']), 'after_count': len(results['after']),
                                         'known_loss_section': expected})
            for j, text in enumerate(section.paragraphs):
                row = {'pmcid': pmc, 'section_index': i, 'paragraph_index': j, 'section': section.section,
                       'text': text, 'before': indices['before'].match(text), 'after': indices['after'].match(text)}
                all_paragraphs.append(row)
                if not row['after']['complete']:
                    remaining.append(row)
                if (pmc, i, j) in old_losses:
                    transitions.append(row)
        generated['before'].extend(paper_chunks['before'])
        generated['after'].extend(paper_chunks['after'])
        per_paper.append({'pmcid': pmc, 'before': stats(paper_chunks['before']), 'after': stats(paper_chunks['after'])})
    # Verify the historical comparison reproduces the real artifacts, including
    # text-derived IDs and paragraph metadata, before reporting a counterfactual.
    keys = ['chunk_id', 'pmcid', 'section', 'paragraph_start', 'paragraph_end', 'text', 'text_sha256', 'word_count']
    projected = lambda values: {v['chunk_id']: {k: v[k] for k in keys} for v in values}
    if len(generated['before']) != 488 or projected(generated['before']) != projected(existing):
        raise ValueError('Historical implementation does not reproduce frozen chunks exactly')
    by_section = {phase: defaultdict(list) for phase in generated}
    for phase, values in generated.items():
        for c in values:
            by_section[phase][(c['pmcid'], c['section'])].append(c)
    body_rows = []
    indices = {phase: {key: Coverage(values) for key, values in grouped.items()} for phase, grouped in by_section.items()}
    for r in ordinary:
        body_rows.append({'pmcid': r['pmcid'], 'source_path': r['source_path'], 'section': r['section_path'],
                          'before': indices['before'].get((r['pmcid'], r['section_path']), Coverage([])).match(r['comparison_text']),
                          'after': indices['after'].get((r['pmcid'], r['section_path']), Coverage([])).match(r['comparison_text'])})
    critical = {}
    for pmc in ['PMC10818109', 'PMC12965823', 'PMC6303131', 'PMC8884877']:
        results = [t for t in transitions if t['pmcid'] == pmc and 'results' in t['section'].casefold()]
        critical[pmc] = {'missing_results_paragraphs_before': len(results),
                         'recovered_results_paragraphs': sum(t['after']['complete'] for t in results),
                         'all_recovered': all(t['after']['complete'] for t in results)}
    duplicate_counts = {phase: duplicates(values) for phase, values in generated.items()}
    metrics = {'scope': 'counterfactual preservation only, not retrieval performance or a new corpus release',
               'created_at_utc': datetime.now(timezone.utc).isoformat(), 'parameters': params,
               'historical_source_sha256': hashlib.sha256(before_source.encode('utf-8')).hexdigest(),
               'current_corpus_source_sha256': sha(root/'src/literature/corpus.py'),
               'historical_artifact_reproduction_exact': True,
               'old_missing_paragraphs': 105, 'recovered_missing_paragraphs': sum(t['after']['complete'] for t in transitions),
               'ordinary_body_total': 811, 'ordinary_body_before_preserved': sum(r['before']['complete'] for r in body_rows),
               'ordinary_body_after_preserved': sum(r['after']['complete'] for r in body_rows),
               'all_parser_paragraphs_checked': len(all_paragraphs), 'remaining_parser_paragraph_loss_count': len(remaining),
               'critical_results': critical, 'chunk_statistics': {phase: stats(values) for phase, values in generated.items()},
               'duplicates': duplicate_counts, 'changed_section_count': len(changed_sections),
               'changed_sections_outside_known_loss': [s for s in changed_sections if not s['known_loss_section']],
               'new_empty_chunks': sum(not c['text'].strip() for c in generated['after']),
               'new_chunks_above_existing_max_plus_overlap': sum(c['word_count'] > params['maximum_words']+params['overlap_words'] for c in generated['after']),
               'protected_files_unchanged': len(expected_hashes),
               'id_schema': 'unchanged text-hash and per-section index algorithm; changed text/index yields new counterfactual IDs',
               'comparison_method': 'NFKC/casefold word-punctuation tokens; local 5-gram union; short text contiguous match; section-local IDs'}
    after_hashes = {p: sha(root/p) for p in expected_hashes}
    if after_hashes != expected_hashes:
        raise ValueError('Protected files changed during counterfactual')
    output.mkdir(parents=True)
    def write(name, data):
        (output/name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    write('metrics.json', metrics)
    write('per_paper.json', per_paper)
    write('recovered_paragraphs.json', transitions)
    write('all_parser_paragraphs.json', all_paragraphs)
    write('ordinary_body_preservation.json', body_rows)
    write('changed_sections.json', changed_sections)
    write('protected_hashes_before.json', expected_hashes)
    write('protected_hashes_after.json', after_hashes)
    (output/'counterfactual_chunks.jsonl').write_text(''.join(json.dumps(c, ensure_ascii=False)+'\n' for c in generated['after']), encoding='utf-8')
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--audit', type=Path, default=ROOT/'reports/diagnostics/jats_parser_v1')
    parser.add_argument('--output', type=Path, default=ROOT/'reports/diagnostics/short_section_chunking_v1')
    parser.add_argument('--protected-hashes', type=Path, required=True)
    before = parser.add_mutually_exclusive_group(required=True)
    before.add_argument('--before-source', type=Path)
    before.add_argument('--before-ref')
    args = parser.parse_args()
    if args.before_source:
        source = args.before_source.read_text(encoding='utf-8')
    else:
        source = subprocess.check_output(['git', 'show', f'{args.before_ref}:src/literature/corpus.py'], cwd=args.root).decode('utf-8')
    metrics = run(args.root, args.output, args.audit, source, args.protected_hashes)
    print(json.dumps(metrics, ensure_ascii=True))


if __name__ == '__main__':
    main()
