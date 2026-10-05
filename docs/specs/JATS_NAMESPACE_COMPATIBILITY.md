# JATS namespace compatibility contract

## Goal and current failure

The same JATS structure without namespaces, with a default namespace, or with a
namespace prefix must produce exactly equal `paper_metadata` and `SectionText`
outputs. Current XPath and tag comparisons use bare names. ElementTree stores
namespaced tags as `{uri}name`, so those lookups can silently return empty
metadata/sections. The current 22 articles have unqualified article roots and
successful body/article-meta selection; this change prevents future input loss.

## Strategy comparison and decision

- A namespace-map XPath requires discovering URIs and managing prefixes for
  default, prefixed and mixed inputs. One fixed URI/prefix would be insufficient.
- Local-name matching requires no namespace registry. ElementTree's existing
  `{*}name` XPath wildcard matches both qualified and unqualified elements while
  retaining current child/descendant paths, predicates and document order.

Choose the second approach: wildcard the existing structural XPath steps and
use one small local-name helper for tag dispatch. Do not strip/mutate the XML
tree, namespace attributes, or foreign inline content. Preserve current xlink
URL/ALI license handling; do not interpret MathML semantics. Structural lookup
remains limited to the existing JATS paths, not an arbitrary XML adapter.

## Required behavior

- Support qualified article, article-meta, body, sec, title, p and abstract.
- Preserve nested section paths, source order, direct-paragraph ownership and
  all list/list-item/boxed-text/def-list representations unchanged.
- Preserve metadata fields, attribute predicates, fallback priority and year
  selection. Both unqualified and namespaced `href` attributes keep working.
- Preserve table-wrap/fig/supplementary-material/media exclusions at all depths.
- Preserve existing abstract selection: direct p wins; otherwise flatten the
  abstract. Namespace compatibility does not redesign mixed/structured abstracts.
- Default and prefixed namespace versions, including two unrelated JATS URIs,
  must match the unqualified fixture by exact structured equality. Foreign
  xlink/MathML/ALI inline elements must not prevent JATS extraction.
- Do not introduce a new exception/validation policy. Explicit regression
  assertions must catch silent empty metadata/body results on valid fixtures.

## Non-goals and protected behavior

No DTD/schema validation, malformed XML recovery, arbitrary XML dialect support,
source adapter, table/figure ingestion, abstract redesign, chunking change,
retrieval/embedding/BM25/RRF/Grader change, Gold use or frozen corpus rebuild.

One previous special-block test explicitly pinned namespace support as a former
non-goal. Replace only that obsolete empty-output assertion with an exact
namespaced special-block output assertion; keep all preservation/exclusion
assertions intact. Historical audit files/reports remain unchanged.

## Acceptance and verification

1. Write namespace tests first; demonstrate failures on the current parser.
2. Pass namespaced metadata, nested sections, abstracts, special blocks,
   exclusions, mixed namespaces and exact no/default/prefix equivalence tests.
3. Compare all 22 raw inputs read-only against the pre-change parser: exact
   metadata, section count/path, paragraph count/text, special-block text and
   in-memory chunks (including text-derived IDs/metadata) must remain identical.
4. Retain ordinary body 811/811, prior short-section recoveries 105/105,
   list/box omissions 0, list items 37/37, boxes 2/2 and definitions 26/26.
5. Run existing special-block/audit/chunking/literature/retrieval tests and full
   pytest. Compare frozen/input/output hashes; write only new diagnostics.
