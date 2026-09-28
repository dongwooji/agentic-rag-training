"""Small standard-library client for reproducible PubMed candidate discovery."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import json
import time
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
IDCONV = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
USER_AGENT = "agentic-rag-training/1.0 (literature corpus research)"


def _request(url: str, *, attempts: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=45) as response:
                payload = response.read()
            time.sleep(0.36)
            return payload
        except Exception as error:  # network errors vary across platforms
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def search_pubmed(query: str, *, retmax: int = 20) -> list[str]:
    params = urlencode(
        {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": retmax,
            "sort": "relevance",
            "tool": "agentic_rag_training",
        }
    )
    payload = json.loads(_request(f"{EUTILS}/esearch.fcgi?{params}"))
    return [str(value) for value in payload["esearchresult"]["idlist"]]


def pmcids_to_pmids(pmcids: Iterable[str]) -> dict[str, str]:
    values = list(dict.fromkeys(str(value).upper() for value in pmcids))
    if not values:
        return {}
    params = urlencode(
        {
            "ids": ",".join(values),
            "format": "json",
            "tool": "agentic_rag_training",
        }
    )
    payload = json.loads(_request(f"{IDCONV}?{params}"))
    return {
        str(record["pmcid"]).upper(): str(record["pmid"])
        for record in payload.get("records", [])
        if record.get("pmcid") and record.get("pmid")
    }


def _element_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _publication_year(article: ET.Element) -> int | None:
    paths = (
        ".//ArticleDate/Year",
        ".//JournalIssue/PubDate/Year",
        ".//DateCompleted/Year",
        ".//DateRevised/Year",
    )
    for path in paths:
        value = article.findtext(path)
        if value and value.isdigit():
            return int(value)
    medline_date = article.findtext(".//JournalIssue/PubDate/MedlineDate", "")
    for token in medline_date.replace("-", " ").split():
        if token[:4].isdigit():
            return int(token[:4])
    return None


def _authors(article: ET.Element) -> list[str]:
    values: list[str] = []
    for author in article.findall(".//AuthorList/Author"):
        collective = author.findtext("CollectiveName")
        if collective:
            values.append(" ".join(collective.split()))
            continue
        last = author.findtext("LastName", "").strip()
        initials = author.findtext("Initials", "").strip()
        name = " ".join(part for part in (last, initials) if part)
        if name:
            values.append(name)
    return values


def fetch_pubmed_records(pmids: Iterable[str]) -> list[dict[str, Any]]:
    identifiers = list(dict.fromkeys(str(pmid) for pmid in pmids))
    records: list[dict[str, Any]] = []
    for offset in range(0, len(identifiers), 100):
        batch = identifiers[offset : offset + 100]
        params = urlencode(
            {
                "db": "pubmed",
                "id": ",".join(batch),
                "retmode": "xml",
                "tool": "agentic_rag_training",
            }
        )
        root = ET.fromstring(_request(f"{EUTILS}/efetch.fcgi?{params}"))
        for article in root.findall("PubmedArticle"):
            pmid = article.findtext(".//MedlineCitation/PMID", "").strip()
            identifiers_by_type = {
                node.attrib.get("IdType", "").lower(): (node.text or "").strip()
                for node in article.findall(".//PubmedData/ArticleIdList/ArticleId")
            }
            abstract_parts = []
            for node in article.findall(".//Article/Abstract/AbstractText"):
                label = node.attrib.get("Label", "").strip()
                text = _element_text(node)
                if text:
                    abstract_parts.append(f"{label}: {text}" if label else text)
            records.append(
                {
                    "pmid": pmid,
                    "pmcid": identifiers_by_type.get("pmc", ""),
                    "doi": identifiers_by_type.get("doi", ""),
                    "title": _element_text(article.find(".//Article/ArticleTitle")),
                    "authors": _authors(article),
                    "year": _publication_year(article),
                    "journal": _element_text(article.find(".//Article/Journal/Title")),
                    "publication_types": [
                        _element_text(node)
                        for node in article.findall(".//PublicationTypeList/PublicationType")
                        if _element_text(node)
                    ],
                    "abstract": "\n".join(abstract_parts),
                    "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                }
            )
    return records


def discover_candidates(config: dict[str, Any], *, retmax: int = 20) -> dict[str, Any]:
    topics_by_pmid: defaultdict[str, set[str]] = defaultdict(set)
    search_log: list[dict[str, Any]] = []
    for search in config["searches"]:
        pmids = search_pubmed(search["query"], retmax=retmax)
        search_log.append(
            {
                "topic": search["topic"],
                "query": search["query"],
                "searched_at": date.today().isoformat(),
                "result_pmids": pmids,
            }
        )
        for pmid in pmids:
            topics_by_pmid[pmid].add(search["topic"])

    seed_mapping = pmcids_to_pmids(config.get("seed_pmcids", []))
    for pmcid, pmid in seed_mapping.items():
        topics_by_pmid[pmid].add("curated_seed")

    records = fetch_pubmed_records(topics_by_pmid)
    preferred = {value.casefold() for value in config["preferred_study_types"]}
    for record in records:
        record["topics"] = sorted(topics_by_pmid[record["pmid"]])
        publication_types = {value.casefold() for value in record["publication_types"]}
        title = record["title"].casefold()
        review_signal = any(
            value in publication_types or value in title for value in preferred
        )
        record["candidate_score"] = (
            4 * bool(record["pmcid"])
            + 2 * bool(record["doi"])
            + 2 * review_signal
            + min(len(record["topics"]), 3)
            + int("trained" in (record["title"] + " " + record["abstract"]).casefold())
        )
        record["full_text_status"] = (
            "pmc_available" if record["pmcid"] else "abstract_only"
        )
        record["selection_status"] = "candidate"
        record["selection_reason"] = ""

    records.sort(
        key=lambda row: (
            -int(row["candidate_score"]),
            -(row["year"] or 0),
            row["pmid"],
        )
    )
    return {
        "corpus_version": config["version"],
        "generated_at": date.today().isoformat(),
        "search_log": search_log,
        "seed_pmcid_to_pmid": seed_mapping,
        "candidates": records,
    }
