from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.app.auto_tag import _add_auto_source
from backend.app.models import Entry, EntryTag, EntryTagSource, Tag, Work


@pytest.fixture
def tagged_articles(authenticated_client):
    client, factory, headers = authenticated_client
    with factory() as db:
        tags = [Tag(name=name) for name in ["Zebra", "beta", "Alpha", "Manual"]]
        db.add_all(tags)
        work = Work(dedup_key="tag-order-test")
        db.add(work)
        db.flush()
        entries = [Entry(work_id=work.id, version_key=str(index), title="Tagged article", summary="", url=f"https://example.test/{index}", source_hash="a" * 64) for index in range(2)]
        db.add_all(entries)
        db.flush()
        for entry in entries:
            for tag, confidence in zip(tags, [0.8, 0.9, 0.9, None]):
                link = EntryTag(entry_id=entry.id, tag_id=tag.id)
                db.add(link)
                db.flush()
                db.add(EntryTagSource(entry_tag_id=link.id, source="auto" if confidence is not None else "manual", confidence=confidence))
        db.commit()
        return client, factory, headers, [entry.id for entry in entries], [tag.id for tag in tags]


def test_article_tags_default_to_confidence_then_alphabet(tagged_articles):
    client, _, _, entries, _ = tagged_articles
    detail = client.get(f"/api/v1/entries/{entries[0]}").json()
    assert [(tag["name"], tag["weight"]) for tag in detail["tags"]] == [("Manual", 1), ("Alpha", 0.9), ("beta", 0.9), ("Zebra", 0.8)]
    listed = client.get("/api/v1/entries?view=all").json()["items"]
    assert all(item["tags"] == detail["tags"] for item in listed)


def test_order_weights_persist_per_article_without_changing_confidence(tagged_articles):
    client, factory, headers, entries, tag_ids = tagged_articles
    desired = [tag_ids[0], tag_ids[2], tag_ids[3], tag_ids[1]]
    response = client.put(f"/api/v1/entries/{entries[0]}/tags/order", json={"tag_ids": desired}, headers=headers)
    assert response.status_code == 200, response.text
    assert [(tag["id"], tag["weight"]) for tag in response.json()["tags"]] == list(zip(desired, [4, 3, 2, 1]))
    assert client.get(f"/api/v1/entries/{entries[0]}").json()["tags"] == response.json()["tags"]
    assert [tag["name"] for tag in client.get(f"/api/v1/entries/{entries[1]}").json()["tags"]] == ["Manual", "Alpha", "beta", "Zebra"]
    with factory() as db:
        assert list(db.scalars(select(EntryTag.weight).where(EntryTag.entry_id == entries[1]))) == [None] * 4
        assert sorted(value for value in db.scalars(select(EntryTagSource.confidence)) if value is not None) == [0.8, 0.8, 0.9, 0.9, 0.9, 0.9]
        assert _add_auto_source(db, entries[0], tag_ids[0], 0.99, "new-policy")
        db.commit()
    assert [tag["weight"] for tag in client.get(f"/api/v1/entries/{entries[0]}").json()["tags"]] == [4, 3, 2, 1]


@pytest.mark.parametrize("kind,status", [("missing", 409), ("foreign", 409), ("duplicate", 422), ("empty", 422), ("negative", 422)])
def test_invalid_order_does_not_partially_change_weights(tagged_articles, kind, status):
    client, factory, headers, entries, ids = tagged_articles
    orders = {"missing": ids[:-1], "foreign": ids[:-1] + [999], "duplicate": ids[:-1] + [ids[0]], "empty": [], "negative": ids[:-1] + [-1]}
    response = client.put(f"/api/v1/entries/{entries[0]}/tags/order", json={"tag_ids": orders[kind]}, headers=headers)
    assert response.status_code == status, response.text
    with factory() as db:
        assert all(weight is None for weight in db.scalars(select(EntryTag.weight)))


def test_order_requires_csrf_and_an_existing_article(tagged_articles):
    client, _, headers, entries, ids = tagged_articles
    assert client.put(f"/api/v1/entries/{entries[0]}/tags/order", json={"tag_ids": ids}).status_code == 403
    assert client.put("/api/v1/entries/999/tags/order", json={"tag_ids": ids}, headers=headers).status_code == 404
    client.post("/api/v1/auth/logout", headers=headers)
    assert client.put(f"/api/v1/entries/{entries[0]}/tags/order", json={"tag_ids": ids}, headers=headers).status_code == 401


def test_merging_tags_keeps_article_order_weight(tagged_articles):
    client, factory, headers, entries, ids = tagged_articles
    with factory() as db:
        for link in db.scalars(select(EntryTag).where(EntryTag.entry_id == entries[0])):
            link.weight = 4 if link.tag_id == ids[0] else 1
        db.commit()
    assert client.post(f"/api/v1/tags/{ids[0]}/merge/{ids[1]}", headers=headers).status_code == 200
    tags = client.get(f"/api/v1/entries/{entries[0]}").json()["tags"]
    assert tags[0]["id"] == ids[1]
    assert tags[0]["weight"] == 4
