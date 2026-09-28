"""Parse PubMed Central JATS XML into paper metadata and section paragraphs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import xml.etree.ElementTree as ET


_SPACE = re.compile(r"\s+")
_BLOCK_TEXT_TAGS = {"fig", "table-wrap", "supplementary-material", "media"}


@dataclass(frozen=True)
class SectionText:
    section: str
    paragraphs: tuple[str, ...]


def clean_text(value: str) -> str:
    return _SPACE.sub(" ", value).strip()


def node_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    values: list[str] = []

    def walk(current: ET.Element) -> None:
        if current.text:
            values.append(current.text)
        for child in current:
            local_name = child.tag.rsplit("}", 1)[-1]
            if local_name not in _BLOCK_TEXT_TAGS:
                walk(child)
            if child.tail:
                values.append(child.tail)

    walk(node)
    return clean_text(" ".join(values))


def _first(root: ET.Element, paths: tuple[str, ...]) -> ET.Element | None:
    for path in paths:
        node = root.find(path)
        if node is not None:
            return node
    return None


def _article_ids(root: ET.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in root.findall(".//article-meta/article-id"):
        value = node_text(node)
        id_type = node.attrib.get("pub-id-type", "").casefold()
        if value and id_type:
            values[id_type] = value
    return values


def _authors(root: ET.Element) -> list[str]:
    values: list[str] = []
    for contrib in root.findall(".//article-meta/contrib-group/contrib"):
        if contrib.attrib.get("contrib-type", "author") != "author":
            continue
        collective = node_text(contrib.find("collab"))
        if collective:
            values.append(collective)
            continue
        surname = node_text(contrib.find(".//surname"))
        given = node_text(contrib.find(".//given-names"))
        name = clean_text(f"{surname} {given}")
        if name:
            values.append(name)
    return values


def _year(root: ET.Element) -> int | None:
    for pub_type in ("epub", "ppub", "collection"):
        value = root.findtext(
            f".//article-meta/pub-date[@pub-type='{pub_type}']/year", ""
        ).strip()
        if value.isdigit():
            return int(value)
    for node in root.findall(".//article-meta/pub-date/year"):
        value = (node.text or "").strip()
        if value.isdigit():
            return int(value)
    return None


def _license(root: ET.Element) -> tuple[str, str]:
    license_node = root.find(".//article-meta/permissions/license")
    if license_node is None:
        copyright_node = root.find(".//article-meta/permissions/copyright-statement")
        return node_text(copyright_node), ""
    url = ""
    for key, value in license_node.attrib.items():
        if key.endswith("href"):
            url = value
            break
    if not url:
        for descendant in license_node.iter():
            for key, value in descendant.attrib.items():
                if key.endswith("href") and value.startswith(("http://", "https://")):
                    url = value
                    break
            if url:
                break
    license_type = license_node.attrib.get("license-type", "")
    text = node_text(license_node)
    return clean_text(" ".join(part for part in (license_type, text) if part)), url


def paper_metadata(xml_path: Path) -> dict[str, object]:
    root = ET.parse(xml_path).getroot()
    identifiers = _article_ids(root)
    title = node_text(_first(root, (".//article-meta/title-group/article-title",)))
    journal = node_text(
        _first(
            root,
            (
                ".//journal-meta/journal-title",
                ".//journal-meta/journal-id[@journal-id-type='nlm-ta']",
                ".//journal-meta/journal-id[@journal-id-type='iso-abbrev']",
                ".//journal-meta/journal-id",
            ),
        )
    )
    license_text, license_url = _license(root)
    return {
        "pmid": identifiers.get("pmid", ""),
        "pmcid": identifiers.get("pmc", identifiers.get("pmcid", "")),
        "doi": identifiers.get("doi", ""),
        "title": title,
        "authors": _authors(root),
        "year": _year(root),
        "journal": journal,
        "license": license_text,
        "license_url": license_url,
    }


def _paragraphs(parent: ET.Element) -> list[str]:
    values: list[str] = []
    for paragraph in parent.findall("./p"):
        text = node_text(paragraph)
        if text:
            values.append(text)
    return values


def _walk_section(
    section: ET.Element,
    parent_titles: tuple[str, ...],
    output: list[SectionText],
) -> None:
    title = node_text(section.find("./title")) or section.attrib.get("sec-type", "section")
    path = tuple(value for value in (*parent_titles, title) if value)
    paragraphs = _paragraphs(section)
    if paragraphs:
        output.append(SectionText(" > ".join(path), tuple(paragraphs)))
    for child in section.findall("./sec"):
        _walk_section(child, path, output)


def extract_sections(xml_path: Path) -> list[SectionText]:
    root = ET.parse(xml_path).getroot()
    sections: list[SectionText] = []

    for abstract in root.findall(".//article-meta/abstract"):
        paragraphs = _paragraphs(abstract)
        if not paragraphs:
            text = node_text(abstract)
            paragraphs = [text] if text else []
        if paragraphs:
            sections.append(SectionText("Abstract", tuple(paragraphs)))

    body = root.find(".//body")
    if body is None:
        return sections
    direct_paragraphs = _paragraphs(body)
    if direct_paragraphs:
        sections.append(SectionText("Body", tuple(direct_paragraphs)))
    for section in body.findall("./sec"):
        _walk_section(section, (), sections)
    return sections
