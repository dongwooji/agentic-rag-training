"""Behavioral contract for body/section special blocks (no corpus writes)."""
import pytest

from src.literature.jats import SectionText, extract_sections, node_text


def parse(tmp_path, body, front="", back=""):
    path = tmp_path / "article.xml"
    path.write_text(f"<article>{front}<body>{body}</body>{back}</article>", encoding="utf-8")
    return extract_sections(path)


def test_direct_section_list_preserves_items_and_order(tmp_path):
    sections = parse(tmp_path, """<sec><title>Practical Applications</title><list>
      <list-item><p>Recommendation one</p></list-item>
      <list-item><p>Recommendation two</p></list-item></list></sec>""")
    assert sections == [SectionText("Practical Applications", (
        "- Recommendation one\n- Recommendation two",))]


def test_box_owns_title_paragraph_and_nested_list_once(tmp_path):
    sections = parse(tmp_path, """<boxed-text><title>Key Points</title>
      <p>Training dose matters</p><list><list-item><p>Recommendation A</p></list-item>
      </list></boxed-text>""")
    assert sections == [SectionText("Body", (
        "Key Points\nTraining dose matters\n- Recommendation A",))]


def test_definition_relationship_inline_tags_and_order(tmp_path):
    sections = parse(tmp_path, """<sec><title>Abbreviations</title><def-list>
      <def-item><term><bold>RIR</bold></term><def><p>Repetitions <italic>in reserve</italic></p></def></def-item>
      <def-item><term>RT</term><def><p>Resistance training</p></def></def-item>
      </def-list></sec>""")
    assert sections == [SectionText("Abbreviations", (
        "RIR: Repetitions in reserve\nRT: Resistance training",))]


def test_source_order_and_section_provenance(tmp_path):
    sections = parse(tmp_path, """<sec><title>Methods</title><p>Before</p>
      <list><list-item><p>Item</p></list-item></list>
      <boxed-text><p>Condition</p></boxed-text><p>After</p>
      <def-list><def-item><term>A</term><def><p>Definition</p></def></def-item></def-list>
      </sec>""")
    assert sections == [SectionText("Methods", (
        "Before", "- Item", "Condition", "After", "A: Definition"))]


def test_nested_section_flushes_parent_and_resumes_in_source_order(tmp_path):
    sections = parse(tmp_path, """<p>Body first</p><sec><title>Parent</title>
      <p>Parent first</p><sec><title>Child</title><list><list-item><p>Child item</p></list-item></list></sec>
      <p>Parent last</p></sec><p>Body last</p>""")
    assert sections == [SectionText("Body", ("Body first",)),
        SectionText("Parent", ("Parent first",)),
        SectionText("Parent > Child", ("- Child item",)),
        SectionText("Parent", ("Parent last",)), SectionText("Body", ("Body last",))]


def test_inline_list_and_quote_remain_one_legacy_paragraph(tmp_path):
    sections = parse(tmp_path, """<sec><title>Discussion</title><p>Intro
      <list><list-item><p>Inline item</p></list-item></list>Tail
      <disp-quote><p>Quoted conclusion</p></disp-quote></p></sec>""")
    assert sections == [SectionText("Discussion", (
        "Intro Inline item Tail Quoted conclusion",))]


def test_nested_lists_preserve_structure_and_multiple_item_paragraphs(tmp_path):
    sections = parse(tmp_path, """<list><label>Recommendations</label>
      <list-item><label>1.</label><p>First</p><p>Second paragraph</p>
      <list><list-item><p>Nested item</p></list-item></list></list-item>
      <list-item><p>Last</p></list-item></list>""")
    assert sections == [SectionText("Body", (
        "Recommendations\n- 1.\n  First\n  Second paragraph\n  - Nested item\n- Last",))]


def test_box_internal_sections_and_caption_not_independently_emitted(tmp_path):
    sections = parse(tmp_path, """<sec><title>Eligibility</title><boxed-text>
      <label>Box 1</label><caption><title>Conditions</title><p>Caption detail</p></caption>
      <sec><title>Known</title><list><list-item><p>Summary</p></list-item></list></sec>
      <sec><title>Added</title><def-list><def-item><term>CTRL</term><def><p>Control</p></def></def-item></def-list></sec>
      </boxed-text></sec>""")
    assert sections == [SectionText("Eligibility", (
        "Box 1\nConditions Caption detail\nKnown\n- Summary\nAdded\nCTRL: Control",))]


@pytest.mark.parametrize("tag", ["table-wrap", "fig", "supplementary-material", "media"])
@pytest.mark.parametrize("context", ["body", "box", "paragraph"])
def test_existing_exclusions_at_every_depth(tmp_path, tag, context):
    excluded = f"<{tag}><list><list-item><p>EXCLUDED_SENTINEL</p></list-item></list></{tag}>"
    if context == "body":
        body = f"<p>Kept</p>{excluded}"
        expected = [SectionText("Body", ("Kept",))]
    elif context == "box":
        body = f"<boxed-text><p>Kept</p>{excluded}</boxed-text>"
        expected = [SectionText("Body", ("Kept",))]
    else:
        body = f"<p>Kept {excluded}tail</p>"
        expected = [SectionText("Body", ("Kept tail",))]
    assert parse(tmp_path, body) == expected


@pytest.mark.parametrize("tag", ["list", "list-item", "boxed-text", "def-list"])
def test_empty_special_block_does_not_emit(tmp_path, tag):
    assert parse(tmp_path, f"<{tag}>  </{tag}>") == []


@pytest.mark.parametrize("term,definition,expected", [
    ("RIR", "", "RIR"), ("", "Reserve", "Reserve"), ("", "", "")])
def test_incomplete_definition_preserves_nonempty_counterpart(tmp_path, term, definition, expected):
    sections = parse(tmp_path, f"<def-list><def-item><term>{term}</term><def><p>{definition}</p></def></def-item></def-list>")
    assert sections == ([SectionText("Body", (expected,))] if expected else [])


def test_direct_list_item_and_inline_content_tail(tmp_path):
    sections = parse(tmp_path, """<list-item>Start <italic>emphasis</italic> end
      <p>Details</p></list-item>""")
    assert sections == [SectionText("Body", ("- Start emphasis end\n  Details",))]


def test_abstract_and_outside_body_behavior_unchanged(tmp_path):
    front = """<front><article-meta><abstract><p>Direct abstract</p><sec><title>Results</title>
      <p>Legacy omitted nested abstract</p></sec></abstract></article-meta></front>"""
    back = "<back><list><list-item><p>Publisher note</p></list-item></list></back>"
    assert parse(tmp_path, "<p>Body text</p>", front, back) == [
        SectionText("Abstract", ("Direct abstract",)), SectionText("Body", ("Body text",))]


def test_namespace_support_not_added(tmp_path):
    path = tmp_path / "namespaced.xml"
    path.write_text('<article xmlns="urn:jats"><body><list><list-item><p>Not selected</p></list-item></list></body></article>', encoding="utf-8")
    assert extract_sections(path) == []


def test_ordinary_paragraph_node_text_unchanged(tmp_path):
    import xml.etree.ElementTree as ET
    xml = '<p>A <italic>scientific</italic> claim <xref>1</xref><table-wrap><p>Blocked</p></table-wrap> tail.</p>'
    assert parse(tmp_path, xml) == [SectionText("Body", (node_text(ET.fromstring(xml)),))]
    assert node_text(ET.fromstring(xml)) == "A scientific claim 1 tail."
