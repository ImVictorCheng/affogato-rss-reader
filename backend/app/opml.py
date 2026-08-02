from __future__ import annotations

# The standard library module is used only to construct and serialize trusted exports.
import xml.etree.ElementTree as ET  # nosec B405
from collections.abc import Iterator

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Domain, Feed, FeedDomain, Folder
from .sync import validate_http_url


MAX_OPML_NESTING = 64


def _iter_outlines(body: ET.Element) -> Iterator[tuple[ET.Element, str | None]]:
    """Yield outlines in document order without recursively walking untrusted XML."""
    stack = [
        (child, None, 1)
        for child in reversed(body.findall("outline"))
    ]
    while stack:
        element, folder, depth = stack.pop()
        if depth > MAX_OPML_NESTING:
            raise ValueError(
                f"OPML nesting exceeds the {MAX_OPML_NESTING}-level limit"
            )

        xml_url = element.attrib.get("xmlUrl") or element.attrib.get("xmlurl")
        if xml_url:
            yield element, folder
            continue

        folder_name = (
            element.attrib.get("title")
            or element.attrib.get("text")
            or folder
            or ""
        ).strip() or None
        yield element, folder
        stack.extend(
            (child, folder_name, depth + 1)
            for child in reversed(element.findall("outline"))
        )


def export_opml_document(db: Session) -> bytes:
    root = ET.Element("opml", version="2.0")
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = "Affogato RSS Reader subscriptions"
    body = ET.SubElement(root, "body")
    folders = {
        folder.name: ET.SubElement(body, "outline", text=folder.name, title=folder.name)
        for folder in db.scalars(select(Folder).order_by(Folder.position, Folder.name))
    }
    for feed in db.scalars(select(Feed).order_by(Feed.folder, Feed.title)):
        parent = body
        if feed.folder:
            parent = folders.get(feed.folder)
            if parent is None:
                parent = ET.SubElement(body, "outline", text=feed.folder, title=feed.folder)
                folders[feed.folder] = parent
        attrs = {
            "type": "rss",
            "text": feed.title,
            "title": feed.title,
            "xmlUrl": feed.url,
            "affogatoRssReaderEnabled": "true" if feed.enabled else "false",
            "affogatoRssReaderPollMinutes": str(feed.poll_interval_minutes),
        }
        if feed.site_url:
            attrs["htmlUrl"] = feed.site_url
        domains = list(
            db.scalars(
                select(Domain.name)
                .join(FeedDomain, FeedDomain.domain_id == Domain.id)
                .where(FeedDomain.feed_id == feed.id)
                .order_by(Domain.position, Domain.name)
            )
        )
        if domains:
            attrs["affogatoRssReaderDomains"] = ",".join(domains)
        ET.SubElement(parent, "outline", **attrs)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def import_opml_document(db: Session, raw: bytes, settings: Settings) -> dict[str, int]:
    try:
        root = DefusedET.fromstring(
            raw,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (ET.ParseError, DefusedXmlException) as exc:
        raise ValueError("Invalid OPML document") from exc
    body = root.find("body")
    if body is None:
        raise ValueError("OPML body is missing")
    outlines = list(_iter_outlines(body))
    imported = skipped = 0
    known_urls = set(db.scalars(select(Feed.url)))
    known_folders = set(db.scalars(select(Folder.name)))
    next_positions = {
        folder: int(highest) + 1
        for folder, highest in db.execute(
            select(Feed.folder, func.max(Feed.position)).group_by(Feed.folder)
        )
    }

    for child, folder in outlines:
        xml_url = child.attrib.get("xmlUrl") or child.attrib.get("xmlurl")
        if not xml_url:
            folder_name = (
                child.attrib.get("title")
                or child.attrib.get("text")
                or folder
                or ""
            ).strip() or None
            if folder_name and folder_name not in known_folders:
                db.add(Folder(name=folder_name, position=len(known_folders)))
                known_folders.add(folder_name)
            continue
        try:
            validate_http_url(xml_url)
        except ValueError:
            skipped += 1
            continue
        if xml_url in known_urls:
            skipped += 1
            continue
        feed = Feed(
            title=child.attrib.get("title") or child.attrib.get("text") or xml_url,
            url=xml_url,
            site_url=child.attrib.get("htmlUrl"),
            folder=folder.strip() if folder else None,
            position=next_positions.get(folder, 0),
            enabled=child.attrib.get("affogatoRssReaderEnabled", "true").lower() != "false",
            poll_interval_minutes=max(
                15,
                min(
                    1440,
                    int(
                        child.attrib.get(
                            "affogatoRssReaderPollMinutes", settings.default_poll_minutes
                        )
                    ),
                ),
            ),
        )
        db.add(feed)
        next_positions[folder] = feed.position + 1
        db.flush()
        for name in (
            item.strip()
            for item in child.attrib.get("affogatoRssReaderDomains", "").split(",")
            if item.strip()
        ):
            domain = db.scalar(
                select(Domain).where(func.lower(Domain.name) == name.lower())
            )
            if domain is None:
                domain = Domain(name=name, position=0)
                db.add(domain)
                db.flush()
            db.add(FeedDomain(feed_id=feed.id, domain_id=domain.id))
        known_urls.add(xml_url)
        imported += 1

    db.commit()
    return {"imported": imported, "skipped": skipped}
