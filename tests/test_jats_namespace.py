"""Namespace compatibility without changing JATS extraction semantics."""
import re

import pytest

from src.literature.jats import SectionText, extract_sections, paper_metadata


ARTICLE = """<article xmlns:xlink="http://www.w3.org/1999/xlink"
 xmlns:m="http://www.w3.org/1998/Math/MathML" xmlns:ali="urn:ali">
 <front><journal-meta><journal-title>Exercise Science</journal-title>
 <journal-id journal-id-type="nlm-ta">Fallback Journal</journal-id></journal-meta>
 <article-meta><article-id pub-id-type="pmid">123</article-id>
 <article-id pub-id-type="pmc">PMC42</article-id><article-id pub-id-type="doi">10.123/example</article-id>
 <title-group><article-title>Training <italic>evidence</italic></article-title></title-group>
 <contrib-group><contrib contrib-type="editor"><name><surname>Ignore</surname></name></contrib>
 <contrib contrib-type="author"><name><surname>Park</surname><given-names>Mina</given-names></name></contrib>
 <contrib contrib-type="author"><collab>Science Group</collab></contrib>
 <contrib><name><surname>Lee</surname><given-names>Jun</given-names></name></contrib></contrib-group>
 <pub-date pub-type="ppub"><year>2025</year></pub-date><pub-date pub-type="epub"><year>2024</year></pub-date>
 <permissions><license license-type="open-access"><license-p>Reuse permitted.</license-p>
 <ext-link xlink:href="https://example.org/license">CC BY</ext-link>
 <ali:license_ref>Registry ref</ali:license_ref></license></permissions>
 <abstract><p>The abstract.</p><sec><title>Keywords</title><p>Legacy omitted keyword.</p></sec></abstract>
 </article-meta></front><body><p>Body first.</p><sec><title>Methods</title><p>Before.</p>
 <list><list-item><p>First</p><list><list-item><p>Nested</p></list-item></list></list-item>
 <list-item><p>Second</p></list-item></list>
 <boxed-text><label>Box 1</label><caption><title>Conditions</title></caption>
 <sec><title>Known</title><p>Summary</p></sec></boxed-text>
 <def-list><def-item><term><bold>RIR</bold></term><def><p>Repetitions in reserve</p></def></def-item></def-list>
 <sec><title>Participants</title><p>Twelve adults.</p></sec><p>After.</p></sec>
 <p>Value <m:math><m:mi>x</m:mi><m:mn>2</m:mn></m:math> end.</p></body></article>"""

EXPECTED_SECTIONS = [
    SectionText("Abstract", ("The abstract.",)), SectionText("Body", ("Body first.",)),
    SectionText("Methods", ("Before.", "- First\n  - Nested\n- Second",
        "Box 1\nConditions\nKnown\nSummary", "RIR: Repetitions in reserve")),
    SectionText("Methods > Participants", ("Twelve adults.",)),
    SectionText("Methods", ("After.",)), SectionText("Body", ("Value x 2 end.",)),
]
EXPECTED_METADATA = dict(pmid="123", pmcid="PMC42", doi="10.123/example",
    title="Training evidence", authors=["Park Mina", "Science Group", "Lee Jun"], year=2024,
    journal="Exercise Science", license="open-access Reuse permitted. CC BY Registry ref",
    license_url="https://example.org/license")


def qualify(xml, style, uri="http://example.org/jats"):
    if style == "plain":
        return xml
    if style == "default":
        return xml.replace("<article", f'<article xmlns="{uri}"', 1)
    # Qualify JATS tags only; keep xlink/MathML/ALI declarations and tags intact.
    result = re.sub(r"<(/?)([A-Za-z][\w.:-]*)", lambda m:
        m.group(0) if ":" in m.group(2) else f"<{m.group(1)}j:{m.group(2)}", xml)
    return result.replace("<j:article", f'<j:article xmlns:j="{uri}"', 1)


def write(tmp_path, xml=ARTICLE, style="plain", uri="http://example.org/jats"):
    path = tmp_path / f"{style}.xml"
    path.write_text(qualify(xml, style, uri), encoding="utf-8")
    return path


@pytest.mark.parametrize("uri", ["http://example.org/jats", "urn:another:jats"])
@pytest.mark.parametrize("style", ["default", "prefix"])
def test_complete_output_equals_unqualified_jats(tmp_path, style, uri):
    plain = write(tmp_path)
    namespaced = write(tmp_path, style=style, uri=uri)
    assert extract_sections(plain) == EXPECTED_SECTIONS
    assert paper_metadata(plain) == EXPECTED_METADATA
    # Positive assertions catch silent emptiness as well as namespace inequality.
    assert extract_sections(namespaced) == EXPECTED_SECTIONS
    assert paper_metadata(namespaced) == EXPECTED_METADATA
    assert extract_sections(namespaced) == extract_sections(plain)
    assert paper_metadata(namespaced) == paper_metadata(plain)


@pytest.mark.parametrize("style", ["default", "prefix"])
@pytest.mark.parametrize("abstract,expected", [
    ("<p>Direct abstract.</p>", "Direct abstract."),
    ("<sec><title>Results</title><p>Result text.</p></sec>", "Results Result text."),
    ("<p>Direct abstract.</p><sec><title>Results</title><p>Legacy omitted nested result.</p></sec>", "Direct abstract."),
])
def test_namespace_preserves_current_abstract_selection(tmp_path, style, abstract, expected):
    xml = f"<article><front><article-meta><abstract>{abstract}</abstract></article-meta></front></article>"
    actual = extract_sections(write(tmp_path, xml, style))
    assert actual == [SectionText("Abstract", (expected,))]
    assert actual == extract_sections(write(tmp_path, xml))


@pytest.mark.parametrize("style", ["default", "prefix"])
@pytest.mark.parametrize("tag", ["table-wrap", "fig", "supplementary-material", "media"])
@pytest.mark.parametrize("context", ["body", "box", "paragraph"])
def test_namespaced_exclusions_keep_policy_and_tails(tmp_path, style, tag, context):
    blocked = f"<{tag}><list><list-item><p>EXCLUDED_SENTINEL</p></list-item></list></{tag}>"
    if context == "body":
        body, expected = f"<p>Kept</p>{blocked}", "Kept"
    elif context == "box":
        body, expected = f"<boxed-text><p>Kept</p>{blocked}tail</boxed-text>", "Kept\ntail"
    else:
        body, expected = f"<p>Kept {blocked}tail</p>", "Kept tail"
    xml = f"<article><body>{body}</body></article>"
    actual = extract_sections(write(tmp_path, xml, style))
    assert actual == [SectionText("Body", (expected,))]
    assert actual == extract_sections(write(tmp_path, xml))


@pytest.mark.parametrize("style", ["default", "prefix"])
@pytest.mark.parametrize("dates,expected", [
    ('<pub-date pub-type="collection"><year>2020</year></pub-date><pub-date pub-type="ppub"><year>2021</year></pub-date>', 2021),
    ('<pub-date pub-type="epub"><year>unknown</year></pub-date><pub-date pub-type="collection"><year>2020</year></pub-date>', 2020),
    ('<pub-date pub-type="other"><year>2019</year></pub-date>', 2019),
    ('<pub-date pub-type="epub"><year>unknown</year></pub-date>', None),
])
def test_year_predicates_priority_and_fallback(tmp_path, style, dates, expected):
    xml = f"<article><front><article-meta>{dates}</article-meta></front></article>"
    assert paper_metadata(write(tmp_path, xml, style))["year"] == expected
    assert paper_metadata(write(tmp_path, xml, style)) == paper_metadata(write(tmp_path, xml))


@pytest.mark.parametrize("style", ["default", "prefix"])
@pytest.mark.parametrize("journals,expected", [
    ('<journal-id journal-id-type="iso-abbrev">ISO</journal-id><journal-id journal-id-type="nlm-ta">NLM</journal-id>', "NLM"),
    ('<journal-id journal-id-type="other">Other</journal-id><journal-id journal-id-type="iso-abbrev">ISO</journal-id>', "ISO"),
    ('<journal-id journal-id-type="other">Other</journal-id>', "Other"),
])
def test_journal_fallback_predicates_unchanged(tmp_path, style, journals, expected):
    xml = f"<article><front><journal-meta>{journals}</journal-meta></front></article>"
    assert paper_metadata(write(tmp_path, xml, style))["journal"] == expected


@pytest.mark.parametrize("style", ["default", "prefix"])
@pytest.mark.parametrize("permission,text,url", [
    ('<license license-type="open" href="https://example.org/direct"><p>Reuse</p></license>', "open Reuse", "https://example.org/direct"),
    ('<license><p><ext-link xlink:href="https://example.org/nested">Reuse</ext-link></p></license>', "Reuse", "https://example.org/nested"),
    ('<copyright-statement>Copyright authors</copyright-statement>', "Copyright authors", ""),
])
def test_license_and_namespaced_href_behavior(tmp_path, style, permission, text, url):
    xml = f'<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta><permissions>{permission}</permissions></article-meta></front></article>'
    metadata = paper_metadata(write(tmp_path, xml, style))
    assert (metadata["license"], metadata["license_url"]) == (text, url)
    assert metadata == paper_metadata(write(tmp_path, xml))


@pytest.mark.parametrize("style", ["default", "prefix"])
def test_mixed_unqualified_jats_children_and_foreign_namespaces(tmp_path, style):
    # Namespace resets/unqualified children are legitimate mixed structural input.
    namespaced = qualify(ARTICLE, style)
    if style == "default":
        namespaced = namespaced.replace("<body>", '<body xmlns="">')
    else:
        namespaced = namespaced.replace("<j:body>", "<body>").replace("</j:body>", "</body>")
    path = tmp_path / "mixed.xml"
    path.write_text(namespaced, encoding="utf-8")
    assert paper_metadata(path) == EXPECTED_METADATA
    assert extract_sections(path) == EXPECTED_SECTIONS


@pytest.mark.parametrize("style", ["default", "prefix"])
def test_inline_list_remains_owned_by_namespaced_paragraph(tmp_path, style):
    xml = """<article><body><p>Intro <list><list-item><p>Inline item</p></list-item></list> tail</p>
    <sec sec-type="results"><title/><list-item><p>Direct item</p></list-item></sec></body></article>"""
    assert extract_sections(write(tmp_path, xml, style)) == [
        SectionText("Body", ("Intro Inline item tail",)), SectionText("results", ("- Direct item",))]
