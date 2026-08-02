from __future__ import annotations

import calendar
import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
from bs4 import BeautifulSoup

ARXIV_RE = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:|oai:arXiv\.org:)"
    r"(?P<base>(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}|[a-z-]+/\d{7}|\d{4}\.\d{4,5}))"
    r"(?:v(?P<version>\d+))?",
    re.IGNORECASE,
)
DOI_RE = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "source"}


@dataclass(slots=True)
class ParsedEntry:
    guid: str | None
    title: str
    summary: str
    content: str | None
    url: str
    authors: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    arxiv_id: str | None = None
    arxiv_base_id: str | None = None
    arxiv_version: int | None = None
    doi: str | None = None
    announce_type: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def source_hash(self) -> str:
        payload = "\0".join(
            [
                self.title,
                self.summary,
                self.content or "",
                self.url,
                json.dumps(self.authors, ensure_ascii=False, sort_keys=True),
                json.dumps(self.categories, ensure_ascii=False, sort_keys=True),
                self.doi or "",
                self.announce_type or "",
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def dedup_key(self) -> str:
        if self.arxiv_base_id:
            return f"arxiv:{self.arxiv_base_id.lower()}"
        if self.doi:
            return f"doi:{self.doi}"
        canonical = canonicalize_url(self.url)
        if canonical:
            return f"url:{canonical}"
        return f"guid:{hashlib.sha256((self.guid or self.title).encode()).hexdigest()}"

    @property
    def version_key(self) -> str:
        if self.arxiv_base_id:
            if self.arxiv_version:
                return f"v{self.arxiv_version}"
            if (self.announce_type or "").lower() in {"replace", "replace-cross"}:
                stamp = (self.updated_at or self.published_at)
                return f"replace:{stamp.isoformat() if stamp else self.source_hash[:16]}"
            return "v1"
        return "default"


def parse_untrusted_html(value: str) -> BeautifulSoup:
    """Parse external markup without the vulnerable stdlib HTMLParser backend."""
    return BeautifulSoup(value, "lxml")


def clean_html(value: str | None) -> str:
    if not value:
        return ""
    soup = parse_untrusted_html(html.unescape(value))
    for element in soup(["script", "style"]):
        element.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def canonicalize_url(value: str | None) -> str:
    if not value:
        return ""
    try:
        parts = urlsplit(value.strip())
        if parts.scheme.lower() not in {"http", "https"}:
            return value.strip()
        host = (parts.hostname or "").lower()
        port = parts.port
        netloc = host
        if port and not ((parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)):
            netloc += f":{port}"
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS])
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))
    except ValueError:
        return value.strip()


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    decoded = html.unescape(value).strip()
    decoded = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", decoded, flags=re.IGNORECASE)
    match = DOI_RE.search(decoded)
    return match.group(1).rstrip(".,;)").lower() if match else None


def struct_time_to_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(value), UTC).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        return None


def _get(entry: Any, *names: str) -> Any:
    for name in names:
        # FeedParserDict.get() contains compatibility aliases that emit deprecation
        # warnings (for example updated_parsed -> published_parsed). Read the
        # underlying mapping directly so missing fields stay genuinely missing.
        value = dict.get(entry, name) if isinstance(entry, dict) else entry.get(name)
        if value not in (None, ""):
            return value
    return None


def _arxiv_metadata(entry: Any, url: str, guid: str | None) -> tuple[str | None, str | None, int | None]:
    candidates = [
        url,
        guid or "",
        str(_get(entry, "arxiv_id", "dc_identifier") or ""),
        str(entry.get("id", "")),
    ]
    for candidate in candidates:
        match = ARXIV_RE.search(candidate)
        if match:
            base = match.group("base")
            version = int(match.group("version")) if match.group("version") else None
            full = f"{base}v{version}" if version else base
            return full, base, version
    return None, None, None


def parse_feed(content: bytes, content_type: str | None = None) -> tuple[dict, list[ParsedEntry]]:
    parsed = feedparser.parse(content, response_headers={"content-type": content_type or ""})
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Invalid feed: {parsed.bozo_exception}")
    output: list[ParsedEntry] = []
    for item in parsed.entries:
        url = str(_get(item, "link", "id") or "").strip()
        if not url:
            continue
        title = clean_html(str(_get(item, "title") or "(untitled)"))
        summary = clean_html(str(_get(item, "summary", "description") or ""))
        content_items = item.get("content") or []
        content = clean_html(content_items[0].get("value")) if content_items else None
        guid = str(_get(item, "id", "guid") or "") or None
        authors = [
            clean_html(str(author.get("name") or ""))
            for author in (item.get("authors") or [])
            if author.get("name")
        ]
        if not authors and item.get("author"):
            authors = [part.strip() for part in re.split(r",|;|\band\b", clean_html(item.author)) if part.strip()]
        categories = sorted({
            str(tag.get("term")).strip()
            for tag in (item.get("tags") or [])
            if tag.get("term")
        })
        arxiv_id, arxiv_base, arxiv_version = _arxiv_metadata(item, url, guid)
        explicit_version = _get(item, "arxiv_version")
        if explicit_version and not arxiv_version:
            match = re.search(r"(\d+)", str(explicit_version))
            arxiv_version = int(match.group(1)) if match else None
            if arxiv_base and arxiv_version:
                arxiv_id = f"{arxiv_base}v{arxiv_version}"
        doi = normalize_doi(str(_get(item, "arxiv_doi", "prism_doi", "doi", "dc_identifier") or ""))
        if not doi:
            doi = normalize_doi(summary)
        announce_type = _get(item, "arxiv_announce_type", "announce_type")
        output.append(
            ParsedEntry(
                guid=guid,
                title=title,
                summary=summary,
                content=content,
                url=canonicalize_url(url),
                authors=authors,
                categories=categories,
                arxiv_id=arxiv_id,
                arxiv_base_id=arxiv_base,
                arxiv_version=arxiv_version,
                doi=doi,
                announce_type=str(announce_type).lower() if announce_type else None,
                published_at=struct_time_to_datetime(_get(item, "published_parsed", "created_parsed")),
                updated_at=struct_time_to_datetime(_get(item, "updated_parsed", "modified_parsed")),
            )
        )
    metadata = {
        "title": clean_html(str(parsed.feed.get("title", ""))),
        "site_url": canonicalize_url(str(parsed.feed.get("link", ""))),
    }
    return metadata, output
