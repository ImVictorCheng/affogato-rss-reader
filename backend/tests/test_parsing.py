from __future__ import annotations

from backend.app.parsing import (
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


def test_normalizers():
    assert normalize_doi("https://doi.org/10.1000/XYZ.1") == "10.1000/xyz.1"
    assert canonicalize_url("HTTPS://Example.COM:443/a/?utm_campaign=x&x=1#part") == "https://example.com/a?x=1"
    assert clean_html("<style>x</style><p>Hello&nbsp;world</p>") == "Hello world"


def test_untrusted_html_uses_lxml_instead_of_stdlib_html_parser():
    soup = parse_untrusted_html("<!broken <!broken <p>safe</p>")

    assert soup.builder.NAME == "lxml"
    assert soup.get_text(" ", strip=True) == "safe"
