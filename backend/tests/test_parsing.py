from __future__ import annotations

from datetime import datetime

import pytest

from backend.app.parsing import (
    MAX_ENTRY_AUTHORS_CHARS,
    MAX_ENTRY_TITLE_CHARS,
    canonicalize_url,
    clean_html,
    normalize_doi,
    parse_feed,
    parse_untrusted_html,
)


def test_parse_rss_cleans_html_and_normalizes_metadata():
    rss = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel><title>Journal</title><link>https://example.test/</link>
      <item>
        <guid>paper-1</guid><title><![CDATA[<b>Light</b> &amp; matter]]></title>
        <link>https://example.test/paper/?utm_source=rss</link>
        <description><![CDATA[<p>An <em>abstract</em>.</p><script>bad()</script>]]></description>
        <author>Alice; Bob</author><category>optics</category>
        <pubDate>Fri, 24 Jul 2026 12:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""
    metadata, entries = parse_feed(rss, "application/rss+xml")
    assert metadata["title"] == "Journal"
    assert len(entries) == 1
    assert entries[0].title == "Light & matter"
    assert entries[0].summary == "An abstract ."
    assert "bad" not in entries[0].summary
    assert entries[0].url == "https://example.test/paper"
    assert entries[0].categories == ["optics"]
    assert entries[0].published_at == datetime(2026, 7, 24, 12)


def test_parse_arxiv_atom_version_announce_categories_and_doi():
    atom = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
      <title>arXiv sample</title><link href="https://arxiv.org/"/>
      <entry>
        <id>https://arxiv.org/abs/2607.12345v2</id>
        <title>A revised preprint</title>
        <summary>Abstract text.</summary>
        <updated>2026-07-24T12:00:00Z</updated><published>2026-07-23T12:00:00Z</published>
        <author><name>Alice Example</name></author>
        <category term="quant-ph"/><category term="physics.atom-ph"/>
        <arxiv:doi>10.1234/ABC.5</arxiv:doi>
        <arxiv:announce_type>replace</arxiv:announce_type>
        <link href="https://arxiv.org/abs/2607.12345v2" rel="alternate"/>
      </entry>
    </feed>"""
    _, entries = parse_feed(atom, "application/atom+xml")
    entry = entries[0]
    assert entry.arxiv_base_id == "2607.12345"
    assert entry.arxiv_version == 2
    assert entry.version_key == "v2"
    assert entry.announce_type == "replace"
    assert entry.categories == ["physics.atom-ph", "quant-ph"]
    assert entry.doi == "10.1234/abc.5"
    assert entry.published_at == datetime(2026, 7, 23, 12)
    assert entry.updated_at == datetime(2026, 7, 24, 12)


@pytest.mark.parametrize("date, expected", [
    ("2026-10-08T10:00:00+00:00", datetime(2026, 10, 8, 10)),
    ("2026-10-08T18:00:00+08:00", datetime(2026, 10, 8, 10)),
    ("2026-10-08", datetime(2026, 10, 8)),
    ("invalid date", None),
])
def test_parse_rdf_item_date_is_independent_of_channel_date(date, expected):
    rss = f"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns="http://purl.org/rss/1.0/"
        xmlns:dc="http://purl.org/dc/elements/1.1/">
      <channel rdf:about="https://journal.test/"><title>Journal</title>
        <dc:date>2026-10-10T00:00:00Z</dc:date></channel>
      <item rdf:about="https://journal.test/paper"><title>Paper</title>
        <link>https://journal.test/paper</link><dc:date>{date}</dc:date>
      </item>
      <item rdf:about="https://journal.test/undated"><title>Undated</title>
        <link>https://journal.test/undated</link></item>
    </rdf:RDF>""".encode()

    _, entries = parse_feed(rss, "application/rdf+xml")

    assert entries[0].published_at is None
    assert entries[0].updated_at == expected
    assert entries[1].published_at is None
    assert entries[1].updated_at is None


def test_parse_atom_updated_only_and_undated_entries_do_not_invent_publication_dates():
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom">
      <title>Atom sample</title><updated>2026-10-10T00:00:00Z</updated>
      <entry><id>https://example.test/updated</id><title>Updated</title>
        <updated>2026-10-09T00:00:00Z</updated></entry>
      <entry><id>https://example.test/undated</id><title>Undated</title></entry>
    </feed>"""

    _, entries = parse_feed(atom, "application/atom+xml")

    assert entries[0].published_at is None
    assert entries[0].updated_at == datetime(2026, 10, 9)
    assert entries[1].published_at is None
    assert entries[1].updated_at is None


def test_parse_arxiv_rss_creator_as_opaque_credit_text():
    expected_authors = [f"Researcher {index}" for index in range(1, 170)]
    creator = ", ".join(expected_authors)
    assert len(creator) > 500
    rss = f"""<?xml version="1.0"?>
    <rss version="2.0"
         xmlns:arxiv="http://arxiv.org/schemas/atom"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
      <channel><title>quant-ph updates on arXiv.org</title>
        <item>
          <guid>oai:arXiv.org:2506.03998v2</guid>
          <title>Large collaboration</title>
          <link>https://arxiv.org/abs/2506.03998v2</link>
          <arxiv:announce_type>replace</arxiv:announce_type>
          <dc:creator>{creator}</dc:creator>
        </item>
      </channel>
    </rss>""".encode()

    _, entries = parse_feed(rss, "application/rss+xml")

    assert entries[0].authors == [creator]


def test_parse_atom_preserves_source_author_elements():
    atom = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Atom sample</title>
      <entry>
        <id>https://example.test/paper</id><title>Paper</title>
        <link href="https://example.test/paper"/>
        <author><name>Smith, Alice</name></author>
        <author><name>Example Research and Development</name></author>
      </entry>
    </feed>"""

    _, entries = parse_feed(atom, "application/atom+xml")

    assert entries[0].authors == ["Smith, Alice", "Example Research and Development"]


def test_parse_feed_bounds_aggregate_author_data():
    credit_chars = 500
    author_count = MAX_ENTRY_AUTHORS_CHARS // (credit_chars + 3) + 1
    repeated_authors = "".join(
        f"<author><name>{'x' * credit_chars}</name></author>"
        for _ in range(author_count)
    )
    aggregate_atom = f"""<feed xmlns="http://www.w3.org/2005/Atom">
      <title>Atom sample</title><entry><id>https://example.test/two</id>
      <title>Paper</title><link href="https://example.test/two"/>
      {repeated_authors}</entry></feed>""".encode()
    with pytest.raises(ValueError, match="author data exceeds"):
        parse_feed(aggregate_atom, "application/atom+xml")


def test_normalizers():
    assert normalize_doi("https://doi.org/10.1000/XYZ.1") == "10.1000/xyz.1"
    assert canonicalize_url("HTTPS://Example.COM:443/a/?utm_campaign=x&x=1#part") == "https://example.com/a?x=1"
    assert canonicalize_url("javascript:alert(1)") == ""
    assert canonicalize_url("not a URL") == ""
    assert clean_html("<style>x</style><p>Hello&nbsp;world</p>") == "Hello world"


def test_untrusted_html_uses_lxml_instead_of_stdlib_html_parser():
    soup = parse_untrusted_html("<!broken <!broken <p>safe</p>")

    assert soup.builder.NAME == "lxml"
    assert soup.get_text(" ", strip=True) == "safe"


def test_parse_feed_rejects_oversized_fields_and_entry_counts():
    oversized_title = "x" * (MAX_ENTRY_TITLE_CHARS + 1)
    oversized = (
        "<rss version='2.0'><channel><title>Feed</title><item>"
        f"<guid>one</guid><link>https://example.test/one</link><title>{oversized_title}</title>"
        "</item></channel></rss>"
    ).encode()
    with pytest.raises(ValueError, match="entry title exceeds"):
        parse_feed(oversized, "application/rss+xml")

    two_entries = b"""<rss version='2.0'><channel><title>Feed</title>
      <item><guid>one</guid><link>https://example.test/one</link></item>
      <item><guid>two</guid><link>https://example.test/two</link></item>
    </channel></rss>"""
    with pytest.raises(ValueError, match="more than 1 entries"):
        parse_feed(two_entries, "application/rss+xml", max_entries=1)


def test_parse_feed_clears_non_http_entry_and_site_links():
    payload = b"""<rss version='2.0'><channel><title>Feed</title>
      <link>javascript:alert(1)</link>
      <item><guid>javascript:alert(2)</guid><title>Unsafe link</title></item>
    </channel></rss>"""

    metadata, entries = parse_feed(payload, "application/rss+xml")

    assert metadata["site_url"] == ""
    assert entries[0].url == ""
