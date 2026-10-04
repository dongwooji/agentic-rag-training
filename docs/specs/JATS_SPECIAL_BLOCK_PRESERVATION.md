# JATS special-block preservation contract

## Goal and current failure

Preserve scientific lists, boxes and definition lists in body/section text using
the existing `SectionText(section, paragraphs)` schema. The current walker selects
only direct `p` children, so scientific blocks outside paragraphs are omitted.

## Required behavior

- Visit body/section children in source order. A direct `p` remains an atomic
  paragraph using the existing `node_text`; lists/quotes already inside it are
  consumed there and never emitted separately.
- A direct `list`, `list-item`, `boxed-text` or `def-list` is one logical paragraph.
  Its descendants belong to that block, not to independent emitted paragraphs.
- Represent ordered list items as `- item` lines in source order; indent nested
  item lines. Keep nonempty labels/titles once, without inventing numbering.
- Represent definition items as `term: definition`, retaining inline text and
  source order. A missing term/definition preserves its nonempty counterpart.
- Represent a box using its label/caption/title and paragraphs, lists and
  definitions in source order. Internal section titles remain within the box.
  Use structural traversal, not unfiltered descendant-text concatenation.
- Keep the nearest enclosing body/section path. Flush a section's pending
  paragraphs before visiting a nested section, then resume the same parent path
  if later siblings exist. Body-level sections do not acquire a `Body` prefix.
- Never emit an empty paragraph/section. Never select descendants of excluded
  `table-wrap`, `fig`, `supplementary-material` or `media` blocks. Keep the existing
  inline exclusion/tail behavior.

Selection is structural: no semantic classifier or LLM importance judgment.
Scientific summaries and recommendations are evidence candidates; abbreviations
are interpretation aids, not new empirical findings. The diagnostic separately
counts added low-value/administrative text rather than claiming all blocks are
scientific evidence.

## Non-goals and preservation boundaries

No changes to tables, figures, supplements, namespace handling, abstract handling,
metadata, chunking, retrieval, embeddings, BM25, RRF, query policy, Grader or Gold.
No schema change. Frozen raw XML, papers, chunks, manifests, configuration,
evaluation and existing diagnostic reports remain byte-identical.

## Acceptance criteria

- Recover PMC10579494 summary/condition boxes and their lists, PMC6081873 Key
  points, PMC7994759 purpose/PICOS, PMC9302196 PICO and nine Practical Applications,
  PMC9935748 research questions, and PMC13236796/PMC6081873 abbreviation definitions.
- Preserve all 811 ordinary body paragraphs and the previous 105 short-section
  recoveries under the unchanged chunker.
- Recover the audited ten list containers / 37 list items and two boxes. These
  counts overlap structurally (three lists are owned by a box), so they are not
  twelve independent emitted paragraphs. Recover two definition lists as well.
- No additional duplicate source-block emissions or empty blocks; unchanged
  table-contained list exclusions and abstract/metadata behavior.
- First demonstrate new regression failures on the old implementation, then
  pass the new tests, existing literature tests and the full offline pytest suite.
- Compare the 22 raw articles in memory and write only new diagnostic outputs;
  compare pre/post hashes, including the unchanged chunker and user files.
