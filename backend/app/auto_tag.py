"""Controlled, provenance-aware LLM tagging.

Automatic tagging deliberately separates classification from taxonomy growth:
approved tags can be attached immediately, while new topics accumulate as
proposals and are automatically promoted after repeated support across distinct
works. Owners may also promote a candidate manually without the support threshold.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import unicodedata
from collections import defaultdict
from datetime import timedelta
from time import sleep
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .llm import (
    AUTO_TAG_FEATURE,
    LLMConnectionError,
    bind_llm_connection,
    complete_feature_chat,
    get_feature_connection,
    get_llm_connection,
)
from .models import (
    AppSetting,
    AutoTagPreview,
    AutoTagRecord,
    AutoTagSuppression,
    BriefSchedule,
    Domain,
    Entry,
    EntryDomain,
    EntryFeed,
    EntryTag,
    EntryTagSource,
    Feed,
    FeedTag,
    Tag,
    TagAlias,
    TagProposal,
    TagProposalAlias,
    TagProposalSupport,
    utcnow,
)
from .topic_names import (
    normalize_topic_name,
    validate_normalized_topic_name,
)

logger = logging.getLogger("uvicorn.error.auto_tag")

MAX_AUTO_TAGS_PER_ENTRY = 3
AUTO_TAG_BATCH_SIZE = 10
AUTO_TAG_INPUT_CHAR_BUDGET = 24_000
AUTO_TAG_MAX_TAG_CANDIDATES = 100
AUTO_TAG_MAX_PROPOSAL_CANDIDATES = 50
AUTO_TAG_MIN_CONFIDENCE = 0.80
AUTO_TAG_PROMOTION_THRESHOLD = 10
AUTO_TAG_SUPPORT_WINDOW_DAYS = 365
AUTO_TAG_LLM_TIMEOUT_SECONDS = 60.0
AUTO_TAG_RETRY_MINUTES = (1, 2, 4, 8)
AUTO_TAG_PREVIEW_RETRY_SECONDS = (1, 2, 4, 8)
AUTO_TAG_MAX_ATTEMPTS = 5
STALE_RUNNING_MINUTES = 30
AUTO_TAG_POLICY_REVISION = "controlled-topics-v1"
TOPIC_NAMESPACE_LOCK_KEY = "auto_tag_topic_namespace_lock"
AUTO_TAG_CLEANUP_LOCK_KEY = "auto_tag_cleanup_snapshot_lock"
AUTO_TAG_CLEANUP_REVIEW_KEY = "auto_tag_cleanup_review_token"
AUTO_TAG_CLEANUP_SNAPSHOT_REVISION = "legacy-auto-tag-cleanup-v1"
# Retained for a possible return of the provenance cleanup workflow.
# See docs/LEGACY_TAG_CLEANUP.md before enabling it again.
LEGACY_AUTO_TAG_CLEANUP_ENABLED = False
# Dormant preview workflow; restoration: docs/DORMANT_AUTO_TAG_PREVIEW.md.
AUTO_TAG_PREVIEW_ENABLED = False


def auto_tag_preview_enabled() -> bool:
    return AUTO_TAG_PREVIEW_ENABLED


def legacy_auto_tag_cleanup_enabled() -> bool:
    return LEGACY_AUTO_TAG_CLEANUP_ENABLED


class AutoTagFormatError(ValueError):
    """Raised when a completion cannot be safely applied."""


class TopicChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["tag", "proposal", "new"]
    id: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    aliases: list[str] = Field(default_factory=list, max_length=12)
    confidence: float = Field(ge=0, le=1)

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, aliases: list[str]) -> list[str]:
        if any(len(alias) > 120 for alias in aliases):
            raise ValueError("topic aliases must be at most 120 characters")
        for alias in aliases:
            validate_normalized_topic_name(
                alias,
                field_name="Topic alias",
                allow_empty=True,
            )
        return aliases

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str | None) -> str | None:
        if name is not None:
            validate_normalized_topic_name(name, field_name="Topic name")
        return name

    @model_validator(mode="after")
    def validate_reference(self) -> "TopicChoice":
        if self.kind in {"tag", "proposal"} and self.id is None:
            raise ValueError("tag and proposal topics require id")
        if self.kind == "new":
            if self.id is not None:
                raise ValueError("new topics cannot reference an id")
            if not (self.name or "").strip():
                raise ValueError("new topics require a name")
        return self


class EntryClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    entry_id: int = Field(ge=1)
    topics: list[TopicChoice] = Field(default_factory=list, max_length=MAX_AUTO_TAGS_PER_ENTRY)


class BatchClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    entries: list[EntryClassification] = Field(max_length=AUTO_TAG_BATCH_SIZE)


def _is_english_canonical_name(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    return (
        bool(normalize_topic_name(normalized))
        and bool(re.search(r"[A-Za-z]", normalized))
        and all(
            not character.isalpha()
            or "A" <= character <= "Z"
            or "a" <= character <= "z"
            for character in normalized
        )
    )


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def _acquire_setting_lock(db: Session, key: str) -> None:
    """Lock one persistent setting row until the surrounding transaction ends."""

    row = db.get(AppSetting, key)
    if row is None:
        # Migrations seed lock rows, but create_all databases and repaired
        # installations may not have them yet. A savepoint makes concurrent
        # first use safe on PostgreSQL (where a uniqueness error would
        # otherwise abort the transaction) and SQLite alike.
        try:
            with db.begin_nested():
                db.add(AppSetting(key=key, value="0"))
                db.flush()
        except IntegrityError:
            pass
    db.execute(
        update(AppSetting)
        .where(AppSetting.key == key)
        .values(value=AppSetting.value)
    )


def acquire_cleanup_snapshot(db: Session) -> None:
    """Serialize cleanup/provenance writes on SQLite and PostgreSQL.

    PostgreSQL holds a row-level lock for the no-op UPDATE. SQLite obtains its
    database write lock at the same point. In both cases it is retained until
    commit/rollback, so token validation and subsequent provenance writes see
    one serial transaction order.
    """

    _acquire_setting_lock(db, AUTO_TAG_CLEANUP_LOCK_KEY)


def acquire_topic_namespace(db: Session) -> None:
    """Serialize normalized topic-name claims across all four namespaces."""

    # Every taxonomy change can change the cleanup snapshot. Keep one global
    # lock order (cleanup, then namespace) to avoid cross-feature deadlocks.
    acquire_cleanup_snapshot(db)
    _acquire_setting_lock(db, TOPIC_NAMESPACE_LOCK_KEY)


def _setting(db: Session, key: str, default: str) -> str:
    row = db.get(AppSetting, key)
    return row.value if row is not None else default


def _int_setting(db: Session, key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_setting(db, key, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def is_auto_tag_enabled(db: Session, settings: Any = None) -> bool:
    del settings
    return _setting(db, "auto_tag_enabled", "false").lower() == "true"


def auto_tag_growth_mode(db: Session) -> str:
    configured = _setting(db, "auto_tag_growth_mode", "").lower()
    if configured in {"closed", "threshold"}:
        return configured
    return (
        "threshold"
        if _setting(db, "auto_tag_create_new", "false").lower() == "true"
        else "closed"
    )


def auto_tag_create_new(db: Session) -> bool:
    """Compatibility view for clients predating controlled growth."""

    return auto_tag_growth_mode(db) == "threshold"


def auto_tag_preview_required(db: Session) -> bool:
    return auto_tag_preview_enabled() and _setting(db, "auto_tag_preview_required", "true").lower() == "true"


def max_tags_per_entry(db: Session) -> int:
    return _int_setting(
        db, "auto_tag_max_tags_per_entry", MAX_AUTO_TAGS_PER_ENTRY, 1, MAX_AUTO_TAGS_PER_ENTRY
    )


def promotion_threshold(db: Session) -> int:
    return _int_setting(db, "auto_tag_promotion_threshold", AUTO_TAG_PROMOTION_THRESHOLD, 2, 100)


def support_window_days(db: Session) -> int:
    return _int_setting(db, "auto_tag_support_window_days", AUTO_TAG_SUPPORT_WINDOW_DAYS, 30, 3650)


def canonical_language(db: Session) -> str:
    return _setting(db, "auto_tag_canonical_language", "en") or "en"


def _taxonomy_revision(db: Session) -> str:
    """Fingerprint the controlled taxonomy without volatile usage counts."""

    tags = [
        (
            tag.id,
            tag.name,
            tag.normalized_name,
            tag.description or "",
            bool(tag.auto_assignable),
        )
        for tag in db.scalars(select(Tag).order_by(Tag.id))
    ]
    aliases = [
        (alias.tag_id, alias.alias, alias.normalized_alias)
        for alias in db.scalars(select(TagAlias).order_by(TagAlias.id))
    ]
    payload = json.dumps(
        {"tags": tags, "aliases": aliases},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _policy_version(db: Session) -> str:
    connection = get_feature_connection(db, AUTO_TAG_FEATURE)
    payload = {
        "revision": AUTO_TAG_POLICY_REVISION,
        "growth_mode": auto_tag_growth_mode(db),
        "max_tags": max_tags_per_entry(db),
        "threshold": promotion_threshold(db),
        "window_days": support_window_days(db),
        "language": canonical_language(db),
        "connection_id": connection.id if connection else None,
        "model": connection.model if connection else None,
        "taxonomy": _taxonomy_revision(db),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def configure_auto_tag(
    db: Session,
    *,
    enabled: bool,
    create_new: bool | None = None,
    llm_connection_id: int | None = None,
    growth_mode: str | None = None,
    max_tags_per_entry: int | None = None,
    promotion_threshold: int | None = None,
    support_window_days: int | None = None,
    canonical_language: str | None = None,
) -> None:
    if growth_mode is None and create_new is not None:
        growth_mode = "threshold" if create_new else "closed"
    growth_mode = growth_mode or auto_tag_growth_mode(db)
    if growth_mode not in {"closed", "threshold"}:
        raise ValueError("growth_mode must be closed or threshold")

    previous_policy_version = _policy_version(db)
    previous_policy = (
        auto_tag_growth_mode(db),
        globals()["max_tags_per_entry"](db),
        globals()["promotion_threshold"](db),
        globals()["support_window_days"](db),
        globals()["canonical_language"](db),
    )
    requested_policy = (
        growth_mode,
        max_tags_per_entry if max_tags_per_entry is not None else previous_policy[1],
        promotion_threshold if promotion_threshold is not None else previous_policy[2],
        support_window_days if support_window_days is not None else previous_policy[3],
        canonical_language if canonical_language is not None else previous_policy[4],
    )
    if not 1 <= int(requested_policy[1]) <= MAX_AUTO_TAGS_PER_ENTRY:
        raise ValueError(f"max_tags_per_entry must be between 1 and {MAX_AUTO_TAGS_PER_ENTRY}")
    if not 2 <= int(requested_policy[2]) <= 100:
        raise ValueError("promotion_threshold must be between 2 and 100")
    if not 30 <= int(requested_policy[3]) <= 3650:
        raise ValueError("support_window_days must be between 30 and 3650")
    if requested_policy[4] != "en":
        raise ValueError("canonical_language currently supports only en")

    if llm_connection_id is not None:
        requested = get_llm_connection(db, llm_connection_id)
        if requested is None:
            raise ValueError("LLM connection not found")
        bind_llm_connection(db, feature_key=AUTO_TAG_FEATURE, connection=requested)
    if enabled and get_feature_connection(db, AUTO_TAG_FEATURE) is None:
        raise ValueError("Select an existing LLM connection for auto tagging")

    _set_setting(db, "auto_tag_growth_mode", growth_mode)
    _set_setting(db, "auto_tag_create_new", "true" if growth_mode == "threshold" else "false")
    _set_setting(db, "auto_tag_max_tags_per_entry", str(requested_policy[1]))
    _set_setting(db, "auto_tag_promotion_threshold", str(requested_policy[2]))
    _set_setting(db, "auto_tag_support_window_days", str(requested_policy[3]))
    _set_setting(db, "auto_tag_canonical_language", str(requested_policy[4]))
    db.flush()
    policy_changed = (
        requested_policy != previous_policy
        or _policy_version(db) != previous_policy_version
    )
    if policy_changed:
        _set_setting(db, "auto_tag_preview_required", "true")
    if enabled and auto_tag_preview_required(db) and not policy_changed:
        raise ValueError("Run and approve the 50-entry preview before enabling auto tagging")
    if enabled and policy_changed and auto_tag_preview_enabled():
        # Persist the new policy but pause execution until its preview is approved.
        enabled = False
    _set_setting(db, "auto_tag_enabled", "true" if enabled else "false")
    if enabled:
        ensure_auto_tag_queue(db)
    db.commit()


def ensure_auto_tag_queue(db: Session) -> int:
    missing = list(
        db.scalars(
            select(Entry)
            .outerjoin(AutoTagRecord, AutoTagRecord.entry_id == Entry.id)
            .where(AutoTagRecord.id.is_(None))
            .order_by(Entry.id)
        )
    )
    for entry in missing:
        db.add(
            AutoTagRecord(
                entry_id=entry.id,
                source_hash=entry.source_hash,
                status="pending",
                tag_ids=[],
                policy_version=None,
            )
        )

    changed = list(
        db.execute(
            select(AutoTagRecord, Entry.source_hash)
            .join(Entry, Entry.id == AutoTagRecord.entry_id)
            .where(AutoTagRecord.source_hash != Entry.source_hash)
        )
    )
    for record, source_hash in changed:
        record.source_hash = source_hash
        record.status = "pending"
        record.attempts = 0
        record.last_error = None
        record.next_retry_at = None
    if missing or changed:
        db.flush()
    return len(missing) + len(changed)


def _entry_text(entry: Entry, max_chars: int, request_id: int) -> dict[str, Any]:
    # The product privacy promise is title + summary only. Other metadata is
    # intentionally restricted to local candidate ranking.
    return {
        # This is a batch-local ordinal, not the database entry identifier.
        "entry_id": request_id,
        "title": entry.title[:500],
        "summary": (entry.summary or "")[:max_chars],
    }


def _local_context(db: Session, entries: list[Entry]) -> str:
    entry_ids = [entry.id for entry in entries]
    parts = [part for entry in entries for part in (entry.title, entry.summary or "")]
    for entry in entries:
        parts.extend(entry.authors or [])
        parts.extend(entry.categories or [])
    parts.extend(
        db.scalars(
            select(Feed.title)
            .join(EntryFeed, EntryFeed.feed_id == Feed.id)
            .where(EntryFeed.entry_id.in_(entry_ids))
        )
    )
    parts.extend(
        db.scalars(
            select(Domain.name)
            .join(EntryDomain, EntryDomain.domain_id == Domain.id)
            .where(EntryDomain.entry_id.in_(entry_ids))
        )
    )
    return normalize_topic_name(" ".join(part for part in parts if part))


def _rank_score(name: str, description: str, context_words: set[str], popularity: int) -> float:
    candidate_words = set(normalize_topic_name(f"{name} {description}").split())
    overlap = len(candidate_words & context_words)
    return overlap * 1000 + math.log2(max(1, popularity) + 1)


def _candidate_catalog(
    db: Session, entries: list[Entry]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[int], set[int]]:
    context_words = set(_local_context(db, entries).split())
    tag_counts = dict(
        db.execute(
            select(EntryTag.tag_id, func.count(EntryTag.id)).group_by(EntryTag.tag_id)
        ).all()
    )
    aliases_by_tag: dict[int, list[str]] = defaultdict(list)
    for alias in db.scalars(select(TagAlias)):
        aliases_by_tag[alias.tag_id].append(alias.alias)
    tags = list(db.scalars(select(Tag).where(Tag.auto_assignable.is_(True))))
    tags.sort(
        key=lambda tag: (
            _rank_score(
                tag.name,
                f"{tag.description or ''} {' '.join(aliases_by_tag.get(tag.id, []))}",
                context_words,
                int(tag_counts.get(tag.id, 0)),
            ),
            int(tag_counts.get(tag.id, 0)),
            -tag.id,
        ),
        reverse=True,
    )
    tags = tags[:AUTO_TAG_MAX_TAG_CANDIDATES]
    tag_payload = [
        {
            "id": tag.id,
            "name": tag.name,
            "description": (tag.description or "")[:240],
            "aliases": [alias[:120] for alias in aliases_by_tag.get(tag.id, [])[:8]],
        }
        for tag in tags
    ]

    aliases_by_proposal: dict[int, list[str]] = defaultdict(list)
    for alias in db.scalars(select(TagProposalAlias)):
        aliases_by_proposal[alias.proposal_id].append(alias.alias)
    proposal_support_counts = dict(
        db.execute(
            select(
                TagProposalSupport.proposal_id,
                func.count(func.distinct(TagProposalSupport.work_id)),
            )
            .join(Entry, Entry.id == TagProposalSupport.entry_id)
            .where(
                TagProposalSupport.source_hash == Entry.source_hash,
                func.coalesce(Entry.published_at, Entry.created_at)
                >= _support_cutoff(db),
            )
            .group_by(TagProposalSupport.proposal_id)
        ).all()
    )
    proposals = list(db.scalars(select(TagProposal).where(TagProposal.status == "active")))
    proposals.sort(
        key=lambda proposal: (
            _rank_score(
                proposal.name,
                f"{proposal.description or ''} "
                f"{' '.join(aliases_by_proposal.get(proposal.id, []))}",
                context_words,
                int(proposal_support_counts.get(proposal.id, 0)),
            ),
            int(proposal_support_counts.get(proposal.id, 0)),
            -proposal.id,
        ),
        reverse=True,
    )
    proposals = proposals[:AUTO_TAG_MAX_PROPOSAL_CANDIDATES]
    proposal_payload = [
        {
            "id": proposal.id,
            "name": proposal.name,
            "description": (proposal.description or "")[:240],
            "aliases": [
                alias[:120] for alias in aliases_by_proposal.get(proposal.id, [])[:8]
            ],
            "support_count": int(proposal_support_counts.get(proposal.id, 0)),
        }
        for proposal in proposals
    ]
    return tag_payload, proposal_payload, {tag.id for tag in tags}, {p.id for p in proposals}


def _build_batch_prompt(
    db: Session, entries: list[Entry]
) -> tuple[str, str, set[int], set[int]]:
    tags, proposals, _tag_ids, _proposal_ids = _candidate_catalog(db, entries)
    growth_mode = auto_tag_growth_mode(db)
    per_entry_budget = max(
        500,
        min(1200, AUTO_TAG_INPUT_CHAR_BUDGET // max(1, len(entries)) - 500),
    )
    entry_payload = [
        _entry_text(entry, per_entry_budget, request_id)
        for request_id, entry in enumerate(entries, start=1)
    ]
    system = (
        "You classify articles into a small, durable topic taxonomy. Prefer broad, reusable "
        "topics that will group many articles; do not create one-off keywords, people, institutions, "
        "paper titles, projects, locations, or events. You may assign zero topics. Return at most "
        f"{max_tags_per_entry(db)} topics per article and only include choices with confidence >= "
        f"{AUTO_TAG_MIN_CONFIDENCE:.2f}. "
    )
    if growth_mode == "closed":
        system += (
            "This taxonomy is closed: every topic must use kind=tag and an ID from approved_tags. "
            "Never output kind=proposal or kind=new. If no approved tag is a strong semantic fit, "
            "return an empty topics list. "
        )
        example_topics: list[dict[str, Any]] = (
            [{"kind": "tag", "id": tags[0]["id"], "confidence": 0.92}]
            if tags
            else []
        )
    else:
        system += (
            "Reuse an approved tag or active proposal whenever it is semantically suitable. New "
            "topic names must be concise English noun phrases; put Chinese names, common "
            "abbreviations, and synonymous spellings in aliases. "
        )
        example_topics = []
        if tags:
            example_topics.append(
                {"kind": "tag", "id": tags[0]["id"], "confidence": 0.92}
            )
        if proposals:
            example_topics.append(
                {
                    "kind": "proposal",
                    "id": proposals[0]["id"],
                    "confidence": 0.86,
                }
            )
        example_topics.append(
            {
                "kind": "new",
                "name": "Quantum error correction",
                "description": "Methods for protecting quantum information from errors.",
                "aliases": ["QEC", "量子纠错"],
                "confidence": 0.88,
            }
        )
    system += "Output only one JSON object matching the requested schema."
    schema = {
        "entries": [
            {
                "entry_id": 1,
                "topics": example_topics,
            }
        ]
    }
    payload = {
        "approved_tags": tags,
        "active_proposals": proposals if growth_mode == "threshold" else [],
        "articles": entry_payload,
        "required_output_schema_example": schema,
    }

    def serialize() -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    user = serialize()
    # The budget covers the complete prompt, including the controlled catalogs.
    # Preserve the highest-ranked items and both catalog types while trimming tails.
    while len(system) + len(user) > AUTO_TAG_INPUT_CHAR_BUDGET and (
        payload["approved_tags"] or payload["active_proposals"]
    ):
        approved = payload["approved_tags"]
        active = payload["active_proposals"]
        approved_size = sum(len(json.dumps(item, ensure_ascii=False)) for item in approved)
        active_size = sum(len(json.dumps(item, ensure_ascii=False)) for item in active)
        if approved and (not active or approved_size >= active_size):
            approved.pop()
        elif active:
            active.pop()
        user = serialize()

    # Extremely long titles can still consume the remainder when no catalog fits.
    # Shrink article text deterministically; IDs and every article stay in the batch.
    while len(system) + len(user) > AUTO_TAG_INPUT_CHAR_BUDGET:
        article = max(
            payload["articles"],
            key=lambda item: len(item["summary"]) + len(item["title"]),
        )
        overflow = len(system) + len(user) - AUTO_TAG_INPUT_CHAR_BUDGET
        if article["summary"]:
            trim = min(len(article["summary"]), max(1, overflow))
            article["summary"] = article["summary"][:-trim]
        elif len(article["title"]) > 1:
            trim = min(len(article["title"]) - 1, max(1, overflow))
            article["title"] = article["title"][:-trim]
        else:
            raise ValueError("Auto-tag prompt metadata exceeds the input character budget")
        user = serialize()

    tag_ids = {int(item["id"]) for item in payload["approved_tags"]}
    proposal_ids = {int(item["id"]) for item in payload["active_proposals"]}
    return system, user, tag_ids, proposal_ids


def _extract_json_object(payload: str) -> str:
    text = payload.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise AutoTagFormatError("LLM response did not contain a JSON object")
    return text[start : end + 1]


def _parse_batch_payload(payload: str) -> BatchClassification:
    try:
        raw = json.loads(_extract_json_object(payload))
        return BatchClassification.model_validate(raw)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise AutoTagFormatError(f"Invalid auto-tag JSON: {exc}") from exc


def _parse_tag_names(payload: str) -> list[str]:
    """Legacy parser retained for callers upgrading from the old array format."""

    text = payload.strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        values = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    names: list[str] = []
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        name = value.strip()
        key = normalize_topic_name(name)
        if name and len(name) <= 120 and key not in normalized:
            names.append(name)
            normalized.add(key)
    return names[:MAX_AUTO_TAGS_PER_ENTRY]


def _classify_batch(
    db: Session, entries: list[Entry]
) -> tuple[BatchClassification, set[int], set[int]]:
    system_prompt, user_prompt, offered_tag_ids, offered_proposal_ids = _build_batch_prompt(db, entries)
    payload = complete_feature_chat(
        db,
        feature_key=AUTO_TAG_FEATURE,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        settings=get_settings(),
        temperature=0.1,
        timeout_seconds=AUTO_TAG_LLM_TIMEOUT_SECONDS,
    )
    parsed = _parse_batch_payload(payload)
    entry_id_by_request_id = {
        request_id: entry.id for request_id, entry in enumerate(entries, start=1)
    }
    unknown_request_ids = sorted(
        {item.entry_id for item in parsed.entries} - set(entry_id_by_request_id)
    )
    if unknown_request_ids:
        raise AutoTagFormatError(
            f"LLM response referenced unknown batch-local article ids {unknown_request_ids}"
        )
    translated = [
        EntryClassification(
            entry_id=entry_id_by_request_id[item.entry_id],
            topics=item.topics,
        )
        for item in parsed.entries
    ]
    return BatchClassification(entries=translated), offered_tag_ids, offered_proposal_ids


def _validated_entry_results(
    result: BatchClassification,
    entries: list[Entry],
    offered_tag_ids: set[int],
    offered_proposal_ids: set[int],
    *,
    growth_mode: str,
) -> tuple[dict[int, EntryClassification], dict[int, str]]:
    expected_ids = {entry.id for entry in entries}
    grouped: dict[int, list[EntryClassification]] = defaultdict(list)
    for item in result.entries:
        if item.entry_id not in expected_ids:
            raise AutoTagFormatError(
                f"LLM response referenced unknown article id {item.entry_id}"
            )
        grouped[item.entry_id].append(item)

    valid: dict[int, EntryClassification] = {}
    errors: dict[int, str] = {}
    for entry_id, items in grouped.items():
        if len(items) != 1:
            errors[entry_id] = "LLM response returned the article more than once"
            continue
        item = items[0]
        seen: set[str] = set()
        accepted: list[TopicChoice] = []
        error: str | None = None
        for topic in item.topics:
            if topic.kind == "tag" and topic.id not in offered_tag_ids:
                error = f"Unknown or unoffered tag id {topic.id}"
                break
            if topic.kind == "proposal" and topic.id not in offered_proposal_ids:
                error = f"Unknown or unoffered proposal id {topic.id}"
                break
            if topic.kind == "new" and not _is_english_canonical_name(topic.name or ""):
                error = "New topic names must use a valid English canonical name"
                break
            if topic.kind in {"proposal", "new"} and growth_mode != "threshold":
                error = f"Topic kind {topic.kind} is not allowed in closed growth mode"
                break
            if topic.confidence < AUTO_TAG_MIN_CONFIDENCE:
                continue
            key = (
                f"{topic.kind}:{topic.id}"
                if topic.kind != "new"
                else f"new:{normalize_topic_name(topic.name or '')}"
            )
            if key not in seen:
                accepted.append(topic)
                seen.add(key)
        if error:
            errors[item.entry_id] = error
            continue
        valid[item.entry_id] = EntryClassification(
            entry_id=item.entry_id,
            topics=accepted[:MAX_AUTO_TAGS_PER_ENTRY],
        )
    for entry_id in expected_ids - set(valid) - set(errors):
        errors[entry_id] = "LLM response omitted the article"
    return valid, errors


def _resolve_tag(db: Session, value: str) -> Tag | None:
    normalized = normalize_topic_name(value)
    if not normalized:
        return None
    tag = db.scalar(select(Tag).where(Tag.normalized_name == normalized).limit(1))
    if tag is not None:
        return tag
    alias = db.scalar(
        select(TagAlias).where(TagAlias.normalized_alias == normalized).limit(1)
    )
    return db.get(Tag, alias.tag_id) if alias is not None else None


def _resolve_proposal(db: Session, value: str) -> TagProposal | None:
    proposal = _resolve_any_proposal(db, value)
    return proposal if proposal is not None and proposal.status == "active" else None


def _resolve_any_proposal(db: Session, value: str) -> TagProposal | None:
    normalized = normalize_topic_name(value)
    if not normalized:
        return None
    proposal = db.scalar(
        select(TagProposal)
        .where(TagProposal.normalized_name == normalized)
        .limit(1)
    )
    if proposal is not None:
        return proposal
    alias = db.scalar(
        select(TagProposalAlias)
        .where(TagProposalAlias.normalized_alias == normalized)
        .limit(1)
    )
    if alias is None:
        return None
    return db.get(TagProposal, alias.proposal_id)


def _alias_available(db: Session, normalized: str) -> bool:
    if not normalized:
        return False
    return (
        db.scalar(select(Tag.id).where(Tag.normalized_name == normalized).limit(1)) is None
        and db.scalar(
            select(TagAlias.id).where(TagAlias.normalized_alias == normalized).limit(1)
        )
        is None
        and db.scalar(
            select(TagProposal.id).where(
                TagProposal.normalized_name == normalized,
                TagProposal.status == "active",
            ).limit(1)
        )
        is None
        and db.scalar(
            select(TagProposalAlias.id)
            .where(TagProposalAlias.normalized_alias == normalized)
            .limit(1)
        )
        is None
    )


def _add_tag_alias(db: Session, tag: Tag, alias_name: str) -> None:
    alias_name = alias_name.strip()[:120]
    if not alias_name:
        return
    normalized = validate_normalized_topic_name(
        alias_name,
        field_name="Tag alias",
    )
    if normalized == tag.normalized_name:
        return
    resolved = _resolve_tag(db, alias_name)
    if resolved is not None:
        return
    if _alias_available(db, normalized):
        db.add(TagAlias(tag_id=tag.id, alias=alias_name, normalized_alias=normalized))
        db.flush()


def _add_proposal_alias(db: Session, proposal: TagProposal, alias_name: str) -> None:
    alias_name = alias_name.strip()[:120]
    if not alias_name:
        return
    normalized = validate_normalized_topic_name(
        alias_name,
        field_name="Topic alias",
    )
    if normalized == proposal.normalized_name:
        return
    resolved = _resolve_proposal(db, alias_name)
    if resolved is not None:
        return
    if _alias_available(db, normalized):
        db.add(
            TagProposalAlias(
                proposal_id=proposal.id,
                alias=alias_name,
                normalized_alias=normalized,
            )
        )
        db.flush()


def _topic_target(
    db: Session, topic: TopicChoice
) -> tuple[Tag | None, TagProposal | None]:
    if topic.kind == "tag":
        return db.get(Tag, topic.id), None
    if topic.kind == "proposal":
        proposal = db.get(TagProposal, topic.id)
        if proposal is None:
            return None, None
        if proposal.status == "promoted" and proposal.promoted_tag_id:
            return db.get(Tag, proposal.promoted_tag_id), None
        return None, proposal

    assert topic.name is not None
    acquire_topic_namespace(db)
    name = topic.name.strip()
    if not _is_english_canonical_name(name):
        return None, None
    normalized = validate_normalized_topic_name(name, field_name="Topic name")

    # A proposed canonical name can differ while one of its aliases already
    # resolves globally. Reuse that controlled object instead of creating a
    # semantically duplicate proposal.
    values = [name, *topic.aliases]
    for value in values:
        tag = _resolve_tag(db, value)
        if tag is not None:
            return (tag if tag.auto_assignable else None), None
    proposal = None
    for value in values:
        resolved = _resolve_any_proposal(db, value)
        if resolved is None:
            continue
        if resolved.status == "promoted" and resolved.promoted_tag_id:
            promoted_tag = db.get(Tag, resolved.promoted_tag_id)
            return (
                promoted_tag
                if promoted_tag is not None and promoted_tag.auto_assignable
                else None
            ), None
        if resolved.status != "active":
            # Retired/merged proposals continue to own their global names and
            # aliases, so a later model response cannot recreate them.
            return None, None
        proposal = resolved
        break
    if proposal is None:
        proposal = TagProposal(
            name=name[:120],
            normalized_name=normalized,
            description=topic.description.strip()[:1000],
            status="active",
            support_count=0,
        )
        db.add(proposal)
        db.flush()
    elif not proposal.description and topic.description.strip():
        proposal.description = topic.description.strip()[:1000]
    for alias_name in topic.aliases:
        _add_proposal_alias(db, proposal, alias_name)
    return None, proposal


def _entry_tag(db: Session, entry_id: int, tag_id: int) -> EntryTag:
    link = db.scalar(
        select(EntryTag).where(
            EntryTag.entry_id == entry_id,
            EntryTag.tag_id == tag_id,
        )
    )
    if link is None:
        link = EntryTag(entry_id=entry_id, tag_id=tag_id)
        db.add(link)
        db.flush()
    return link


def _automatic_tag_count(db: Session, entry_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(EntryTagSource.id))
            .join(EntryTag, EntryTag.id == EntryTagSource.entry_tag_id)
            .where(EntryTag.entry_id == entry_id, EntryTagSource.source == "auto")
        )
        or 0
    )


def _add_auto_source(
    db: Session,
    entry_id: int,
    tag_id: int,
    confidence: float,
    policy_version: str,
) -> bool:
    if db.scalar(
        select(AutoTagSuppression.id).where(
            AutoTagSuppression.entry_id == entry_id,
            AutoTagSuppression.tag_id == tag_id,
        )
    ) is not None:
        return False
    link = db.scalar(
        select(EntryTag).where(
            EntryTag.entry_id == entry_id,
            EntryTag.tag_id == tag_id,
        )
    )
    if link is None and _automatic_tag_count(db, entry_id) >= max_tags_per_entry(db):
        return False
    if link is None:
        link = _entry_tag(db, entry_id, tag_id)
    source = db.scalar(
        select(EntryTagSource).where(
            EntryTagSource.entry_tag_id == link.id,
            EntryTagSource.source == "auto",
        )
    )
    if source is None:
        if _automatic_tag_count(db, entry_id) >= max_tags_per_entry(db):
            return False
        source = EntryTagSource(
            entry_tag_id=link.id,
            source="auto",
            confidence=confidence,
            policy_version=policy_version,
        )
        db.add(source)
    else:
        source.confidence = confidence
        source.policy_version = policy_version
    db.flush()
    return True


def _remove_stale_auto_sources(db: Session, entry_id: int, keep_tag_ids: set[int]) -> None:
    rows = list(
        db.execute(
            select(EntryTagSource, EntryTag)
            .join(EntryTag, EntryTag.id == EntryTagSource.entry_tag_id)
            .where(
                EntryTag.entry_id == entry_id,
                EntryTagSource.source == "auto",
            )
        )
    )
    for source, link in rows:
        if link.tag_id in keep_tag_ids:
            continue
        db.delete(source)
        db.flush()
        remaining = db.scalar(
            select(EntryTagSource.id)
            .where(EntryTagSource.entry_tag_id == link.id)
            .limit(1)
        )
        if remaining is None:
            db.delete(link)
            db.flush()


def _current_auto_tag_ids(db: Session, entry_id: int) -> list[int]:
    return list(
        db.scalars(
            select(EntryTag.tag_id)
            .join(EntryTagSource, EntryTagSource.entry_tag_id == EntryTag.id)
            .where(
                EntryTag.entry_id == entry_id,
                EntryTagSource.source == "auto",
            )
            .order_by(EntryTag.tag_id)
        )
    )


def add_manual_tag(db: Session, entry_id: int, tag_id: int) -> None:
    acquire_cleanup_snapshot(db)
    if db.get(Entry, entry_id) is None:
        raise ValueError("Entry not found")
    if db.get(Tag, tag_id) is None:
        raise ValueError("Tag not found")
    link = _entry_tag(db, entry_id, tag_id)
    if db.scalar(
        select(EntryTagSource.id).where(
            EntryTagSource.entry_tag_id == link.id,
            EntryTagSource.source == "manual",
        )
    ) is None:
        db.add(EntryTagSource(entry_tag_id=link.id, source="manual"))
    suppression = db.scalar(
        select(AutoTagSuppression).where(
            AutoTagSuppression.entry_id == entry_id,
            AutoTagSuppression.tag_id == tag_id,
        )
    )
    if suppression is not None:
        db.delete(suppression)
    db.commit()


def remove_manual_tag(db: Session, entry_id: int, tag_id: int) -> None:
    acquire_cleanup_snapshot(db)
    link = db.scalar(
        select(EntryTag).where(
            EntryTag.entry_id == entry_id,
            EntryTag.tag_id == tag_id,
        )
    )
    if link is not None:
        db.delete(link)
    if db.scalar(
        select(AutoTagSuppression.id).where(
            AutoTagSuppression.entry_id == entry_id,
            AutoTagSuppression.tag_id == tag_id,
        )
    ) is None:
        db.add(
            AutoTagSuppression(
                entry_id=entry_id,
                tag_id=tag_id,
                reason="owner_removed",
            )
        )
    record = db.scalar(
        select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
    )
    if record is not None:
        record.tag_ids = [value for value in (record.tag_ids or []) if value != tag_id]
    db.commit()


def _support_cutoff(db: Session):
    return utcnow() - timedelta(days=support_window_days(db))


def _current_proposal_support_count(db: Session, proposal_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(func.distinct(TagProposalSupport.work_id)))
            .join(Entry, Entry.id == TagProposalSupport.entry_id)
            .where(
                TagProposalSupport.proposal_id == proposal_id,
                TagProposalSupport.source_hash == Entry.source_hash,
                func.coalesce(Entry.published_at, Entry.created_at)
                >= _support_cutoff(db),
            )
        )
        or 0
    )


def _refresh_proposal_support_count(db: Session, proposal: TagProposal) -> int:
    count = _current_proposal_support_count(db, proposal.id)
    proposal.support_count = count
    return count


def _support_proposal(
    db: Session,
    proposal: TagProposal,
    entry: Entry,
    confidence: float,
) -> None:
    support = db.scalar(
        select(TagProposalSupport).where(
            TagProposalSupport.proposal_id == proposal.id,
            TagProposalSupport.entry_id == entry.id,
        )
    )
    if support is None:
        support = TagProposalSupport(
            proposal_id=proposal.id,
            entry_id=entry.id,
            work_id=entry.work_id,
            source_hash=entry.source_hash,
            confidence=confidence,
        )
        db.add(support)
    else:
        support.work_id = entry.work_id
        support.source_hash = entry.source_hash
        support.confidence = confidence
    db.flush()
    _refresh_proposal_support_count(db, proposal)


def _maybe_promote_proposal(
    db: Session,
    proposal: TagProposal,
    policy_version: str,
    promotion_events: list[dict[str, Any]],
    *,
    manual: bool = False,
) -> Tag | None:
    acquire_topic_namespace(db)
    locked = db.scalar(
        select(TagProposal)
        .where(TagProposal.id == proposal.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        return None
    proposal = locked
    if proposal.status != "active":
        return db.get(Tag, proposal.promoted_tag_id) if proposal.promoted_tag_id else None
    support_count = _refresh_proposal_support_count(db, proposal)
    if not manual and support_count < promotion_threshold(db):
        return None

    tag = _resolve_tag(db, proposal.name)
    if tag is None:
        tag = Tag(
            name=proposal.name,
            normalized_name=proposal.normalized_name,
            description=proposal.description or "",
            origin="manual_promoted" if manual else "auto_promoted",
            auto_assignable=True,
        )
        db.add(tag)
        db.flush()
    elif not tag.auto_assignable:
        return None
    aliases = list(
        db.scalars(
            select(TagProposalAlias).where(TagProposalAlias.proposal_id == proposal.id)
        )
    )
    alias_names = [alias.alias for alias in aliases]
    for alias in aliases:
        db.delete(alias)
    db.flush()
    for alias_name in alias_names:
        _add_tag_alias(db, tag, alias_name)
    proposal.status = "promoted"
    proposal.promoted_tag_id = tag.id

    supports = list(
        db.execute(
            select(TagProposalSupport, Entry)
            .join(Entry, Entry.id == TagProposalSupport.entry_id)
            .where(
                TagProposalSupport.proposal_id == proposal.id,
                TagProposalSupport.source_hash == Entry.source_hash,
            )
            .order_by(TagProposalSupport.confidence.desc(), Entry.id)
        )
    )
    for support, entry in supports:
        if _add_auto_source(db, entry.id, tag.id, support.confidence, policy_version):
            record = db.scalar(
                select(AutoTagRecord).where(AutoTagRecord.entry_id == entry.id)
            )
            if record is not None:
                record.tag_ids = _current_auto_tag_ids(db, entry.id)
    promotion_events.append({
        "proposal_id": proposal.id,
        "name": proposal.name,
        "tag_id": tag.id,
        "support_count": proposal.support_count,
        "threshold": promotion_threshold(db),
        "window_days": support_window_days(db),
        "manual": manual,
    })
    return tag


def _log_promotions(promotion_events: list[dict[str, Any]]) -> None:
    # Call only after the transaction commits; a rollback is not a promotion.
    for promotion in promotion_events:
        logger.info(
            ("Auto-tag topic manually promoted: " if promotion.get("manual") else "Auto-tag topic promoted: ")
            + "proposal_id=%(proposal_id)s name=%(name)r "
            "tag_id=%(tag_id)s support_count=%(support_count)s "
            "threshold=%(threshold)s window_days=%(window_days)s",
            promotion,
        )


def promote_proposal(db: Session, proposal_id: int) -> Tag:
    """Promote an active candidate on request without requiring LLM calls."""
    acquire_topic_namespace(db)
    proposal = db.get(TagProposal, proposal_id)
    if proposal is None:
        raise LookupError("Topic candidate not found")
    if proposal.status not in {"active", "promoted"}:
        raise ValueError("Only active topic candidates can be promoted")
    promotion_events: list[dict[str, Any]] = []
    tag = _maybe_promote_proposal(
        db, proposal, _policy_version(db), promotion_events, manual=True,
    )
    if tag is None:
        raise ValueError("This topic conflicts with a tag that is unavailable for automatic tagging")
    db.commit()
    _log_promotions(promotion_events)
    return tag


def retire_tag_proposals(db: Session, tag_id: int) -> None:
    """Keep deleted promoted topics reserved without recreating them automatically."""

    for proposal in db.scalars(
        select(TagProposal).where(TagProposal.promoted_tag_id == tag_id)
    ):
        proposal.status = "retired"
        proposal.promoted_tag_id = None


def delete_tags(db: Session, tag_ids: list[int]) -> list[int]:
    """Delete selected tags and their links in the caller's transaction."""
    acquire_topic_namespace(db)
    selected_ids = set(tag_ids)
    tags = list(db.scalars(select(Tag).where(Tag.id.in_(selected_ids)).order_by(Tag.id)))
    if not selected_ids or {tag.id for tag in tags} != selected_ids:
        raise ValueError("Some selected tags no longer exist; refresh the tag list")

    schedules = list(db.scalars(select(BriefSchedule)))
    referenced_ids = {
        tag_id
        for schedule in schedules
        if schedule.enabled
        for tag_id in schedule.tag_ids or []
        if tag_id in selected_ids
    }
    if referenced_ids:
        names = ", ".join(tag.name for tag in tags if tag.id in referenced_ids)
        raise ValueError(
            "Tags are used by active brief schedules; remove them from those "
            f"schedules before deleting: {names}"
        )

    for schedule in schedules:
        if selected_ids.intersection(schedule.tag_ids or []):
            schedule.tag_ids = [value for value in schedule.tag_ids if value not in selected_ids]
    for record in db.scalars(select(AutoTagRecord)):
        if selected_ids.intersection(record.tag_ids or []):
            record.tag_ids = [value for value in record.tag_ids if value not in selected_ids]
    for tag in tags:
        retire_tag_proposals(db, tag.id)
        # Foreign-key cascades remove article/feed links, aliases and suppressions.
        db.delete(tag)
    db.flush()
    return [tag.id for tag in tags]


def _apply_classification(
    db: Session,
    record: AutoTagRecord,
    entry: Entry,
    result: EntryClassification,
    policy_version: str,
    promotion_events: list[dict[str, Any]],
) -> None:
    acquire_cleanup_snapshot(db)
    selected: dict[int, float] = {}
    supported: dict[int, tuple[TagProposal, float]] = {}
    # A successful reclassification replaces this article's proposal evidence.
    # Failed/invalid responses never reach this point, so they preserve it.
    affected_proposal_ids = list(
        db.scalars(
            select(TagProposalSupport.proposal_id).where(
                TagProposalSupport.entry_id == entry.id
            )
        )
    )
    db.execute(
        delete(TagProposalSupport).where(TagProposalSupport.entry_id == entry.id)
    )
    db.flush()
    for proposal_id in set(affected_proposal_ids):
        affected = db.get(TagProposal, proposal_id)
        if affected is not None:
            _refresh_proposal_support_count(db, affected)
    for topic in result.topics:
        tag, proposal = _topic_target(db, topic)
        if tag is not None and tag.auto_assignable:
            selected[tag.id] = max(selected.get(tag.id, 0), topic.confidence)
        if proposal is not None:
            _support_proposal(db, proposal, entry, topic.confidence)
            previous = supported.get(proposal.id)
            supported[proposal.id] = (
                proposal,
                max(previous[1] if previous else 0, topic.confidence),
            )

    for proposal, confidence in supported.values():
        promoted = _maybe_promote_proposal(db, proposal, policy_version, promotion_events)
        if promoted is not None:
            selected[promoted.id] = max(selected.get(promoted.id, 0), confidence)

    ordered = sorted(selected.items(), key=lambda item: (-item[1], item[0]))[
        : max_tags_per_entry(db)
    ]
    keep_ids = {tag_id for tag_id, _confidence in ordered}
    _remove_stale_auto_sources(db, entry.id, keep_ids)
    for tag_id, confidence in ordered:
        _add_auto_source(db, entry.id, tag_id, confidence, policy_version)

    record.source_hash = entry.source_hash
    record.policy_version = policy_version
    record.tag_ids = _current_auto_tag_ids(db, entry.id)
    record.status = "complete"
    record.last_error = None
    record.next_retry_at = None


def _mark_failed(db: Session, records: list[AutoTagRecord], error: Exception | str) -> None:
    message = str(error)[:4000]
    now = utcnow()
    for record in records:
        record.status = "failed"
        record.last_error = message
        if record.attempts >= AUTO_TAG_MAX_ATTEMPTS:
            record.next_retry_at = None
        else:
            delay_index = max(0, min(record.attempts - 1, len(AUTO_TAG_RETRY_MINUTES) - 1))
            record.next_retry_at = now + timedelta(minutes=AUTO_TAG_RETRY_MINUTES[delay_index])
    db.commit()


def _process_records_once(db: Session, records: list[AutoTagRecord]) -> list[AutoTagRecord]:
    entries: list[Entry] = []
    active_records: list[AutoTagRecord] = []
    for record in records:
        entry = db.get(Entry, record.entry_id)
        if entry is None:
            record.status = "failed"
            record.last_error = "Entry no longer exists"
            record.next_retry_at = None
            continue
        record.status = "running"
        record.attempts += 1
        record.last_error = None
        record.source_hash = entry.source_hash
        entries.append(entry)
        active_records.append(record)
    expected_policy_version = _policy_version(db)
    db.commit()
    if not entries:
        return records

    result, offered_tag_ids, offered_proposal_ids = _classify_batch(db, entries)
    # SessionLocal keeps objects after commits; expire them so concurrent
    # settings, model, taxonomy, and source changes cannot hide in the identity map.
    db.expire_all()
    valid, errors = _validated_entry_results(
        result,
        entries,
        offered_tag_ids,
        offered_proposal_ids,
        growth_mode=auto_tag_growth_mode(db),
    )
    policy_version = _policy_version(db)
    if policy_version != expected_policy_version:
        for record in active_records:
            record.status = "pending"
            record.attempts = max(0, record.attempts - 1)
            record.last_error = "Auto-tag policy or taxonomy changed while the batch was running"
            record.next_retry_at = None
        db.commit()
        return records
    # Start the write phase, then lock/re-read the entry rows. SQLite's write
    # lock closes the refresh-to-commit race; databases with row locking also
    # receive FOR UPDATE on the entries themselves.
    acquire_cleanup_snapshot(db)
    if _policy_version(db) != expected_policy_version:
        for record in active_records:
            record.status = "pending"
            record.attempts = max(0, record.attempts - 1)
            record.last_error = "Auto-tag policy or taxonomy changed while the batch was running"
            record.next_retry_at = None
        db.commit()
        return records
    locked_entries = {
        entry.id: entry
        for entry in db.scalars(
            select(Entry)
            .where(Entry.id.in_([entry.id for entry in entries]))
            .with_for_update()
        )
    }
    promotion_events: list[dict[str, Any]] = []
    for record, intended_entry in zip(active_records, entries, strict=True):
        entry = locked_entries.get(intended_entry.id)
        if entry is None:
            record.status = "failed"
            record.last_error = "Entry no longer exists"
            record.next_retry_at = None
            continue
        db.refresh(entry)
        if entry.source_hash != record.source_hash:
            record.source_hash = entry.source_hash
            record.status = "pending"
            record.attempts = 0
            record.last_error = "Source changed while auto tagging was running"
            record.next_retry_at = None
            continue
        if entry.id in errors:
            record.status = "failed"
            record.last_error = errors[entry.id]
            if record.attempts >= AUTO_TAG_MAX_ATTEMPTS:
                record.next_retry_at = None
            else:
                delay_index = max(
                    0,
                    min(record.attempts - 1, len(AUTO_TAG_RETRY_MINUTES) - 1),
                )
                record.next_retry_at = utcnow() + timedelta(
                    minutes=AUTO_TAG_RETRY_MINUTES[delay_index]
                )
            continue
        _apply_classification(db, record, entry, valid[entry.id], policy_version, promotion_events)
    db.commit()
    _log_promotions(promotion_events)
    return records


def tag_entries_batch(db: Session, records: list[AutoTagRecord]) -> list[AutoTagRecord]:
    if not records:
        return []
    retryable = [record for record in records if record.attempts < AUTO_TAG_MAX_ATTEMPTS]
    exhausted = [record for record in records if record.attempts >= AUTO_TAG_MAX_ATTEMPTS]
    for record in exhausted:
        record.status = "failed"
        record.next_retry_at = None
        if not record.last_error:
            record.last_error = "Automatic tagging reached the retry limit"
    if exhausted:
        db.commit()
    if not retryable:
        return records
    try:
        processed = _process_records_once(db, retryable)
        return processed + exhausted
    except AutoTagFormatError as exc:
        db.rollback()
        refreshed = [db.get(AutoTagRecord, record.id) for record in retryable]
        refreshed = [record for record in refreshed if record is not None]
        if len(refreshed) > 1:
            for record in refreshed:
                record.status = "pending"
                record.last_error = str(exc)[:4000]
                record.next_retry_at = None
            db.commit()
            midpoint = len(refreshed) // 2
            return (
                tag_entries_batch(db, refreshed[:midpoint])
                + tag_entries_batch(db, refreshed[midpoint:])
                + exhausted
            )
        _mark_failed(db, refreshed, exc)
        return refreshed + exhausted
    except (LLMConnectionError, ValueError) as exc:
        db.rollback()
        refreshed = [db.get(AutoTagRecord, record.id) for record in retryable]
        refreshed = [record for record in refreshed if record is not None]
        if isinstance(exc, LLMConnectionError) and not exc.retryable:
            # Mark a permanent connection/configuration failure as exhausted so
            # retry_failed jobs cannot repeatedly call a provider that rejected it.
            for record in refreshed:
                record.attempts = AUTO_TAG_MAX_ATTEMPTS
        _mark_failed(db, refreshed, exc)
        return refreshed + exhausted
    except Exception as exc:
        db.rollback()
        refreshed = [db.get(AutoTagRecord, record.id) for record in retryable]
        refreshed = [record for record in refreshed if record is not None]
        _mark_failed(db, refreshed, exc)
        return refreshed + exhausted


def tag_entry(db: Session, record: AutoTagRecord) -> AutoTagRecord:
    return tag_entries_batch(db, [record])[0]


def auto_tag_pending(
    db: Session,
    *,
    limit: int = AUTO_TAG_BATCH_SIZE,
    retry_failed: bool = False,
) -> list[AutoTagRecord]:
    if not is_auto_tag_enabled(db) or get_feature_connection(db, AUTO_TAG_FEATURE) is None:
        return []
    if auto_tag_preview_required(db):
        return []
    ensure_auto_tag_queue(db)
    db.commit()
    now = utcnow()
    stale_running = now - timedelta(minutes=STALE_RUNNING_MINUTES)
    conditions = [AutoTagRecord.status == "pending"]
    if retry_failed:
        conditions.append(
            and_(
                AutoTagRecord.status == "failed",
                AutoTagRecord.attempts < AUTO_TAG_MAX_ATTEMPTS,
            )
        )
    rows = list(
        db.scalars(
            select(AutoTagRecord)
            .where(
                or_(
                    *conditions,
                    and_(
                        AutoTagRecord.status == "running",
                        AutoTagRecord.updated_at <= stale_running,
                    ),
                ),
                or_(
                    AutoTagRecord.next_retry_at.is_(None),
                    AutoTagRecord.next_retry_at <= now,
                ),
            )
            .order_by(AutoTagRecord.updated_at, AutoTagRecord.id)
            .limit(max(1, min(limit, AUTO_TAG_BATCH_SIZE)))
        )
    )
    for row in rows:
        if row.status == "running":
            row.status = "pending"
            row.last_error = "Recovered an interrupted auto-tagging batch"
    db.commit()
    return tag_entries_batch(db, rows)


def auto_tag_due(db: Session, settings: Any = None) -> bool:
    if not is_auto_tag_enabled(db, settings):
        return False
    if get_feature_connection(db, AUTO_TAG_FEATURE) is None:
        return False
    if auto_tag_preview_required(db):
        return False
    now = utcnow()
    stale_running = now - timedelta(minutes=STALE_RUNNING_MINUTES)
    due = db.scalar(
        select(AutoTagRecord.id)
        .where(
            or_(
                AutoTagRecord.status == "pending",
                and_(
                    AutoTagRecord.status == "failed",
                    AutoTagRecord.attempts < AUTO_TAG_MAX_ATTEMPTS,
                    or_(
                        AutoTagRecord.next_retry_at.is_(None),
                        AutoTagRecord.next_retry_at <= now,
                    ),
                ),
                and_(
                    AutoTagRecord.status == "running",
                    AutoTagRecord.updated_at <= stale_running,
                ),
            )
        )
        .limit(1)
    )
    if due is not None:
        return True
    return db.scalar(
        select(Entry.id)
        .outerjoin(AutoTagRecord, AutoTagRecord.entry_id == Entry.id)
        .where(
            or_(
                AutoTagRecord.id.is_(None),
                AutoTagRecord.source_hash != Entry.source_hash,
            )
        )
        .limit(1)
    ) is not None


def auto_tag_status(db: Session) -> dict[str, Any]:
    connection = get_feature_connection(db, AUTO_TAG_FEATURE)
    counts = dict(
        db.execute(
            select(AutoTagRecord.status, func.count()).group_by(AutoTagRecord.status)
        ).all()
    )
    pending_count = int(counts.get("pending", 0))
    proposal_count = int(
        db.scalar(
            select(func.count(TagProposal.id)).where(TagProposal.status == "active")
        )
        or 0
    )
    promoted_count = int(
        db.scalar(
            select(func.count(TagProposal.id)).where(TagProposal.status == "promoted")
        )
        or 0
    )
    remaining_count = int(
        db.scalar(
            select(func.count(Entry.id))
            .outerjoin(AutoTagRecord, AutoTagRecord.entry_id == Entry.id)
            .where(
                or_(
                    AutoTagRecord.id.is_(None),
                    AutoTagRecord.status != "complete",
                    AutoTagRecord.source_hash != Entry.source_hash,
                )
            )
        )
        or 0
    )
    return {
        "enabled": is_auto_tag_enabled(db),
        "create_new": auto_tag_create_new(db),
        "growth_mode": auto_tag_growth_mode(db),
        "llm_connection_id": connection.id if connection else None,
        "llm_connection_name": connection.name if connection else None,
        "model": connection.model if connection else None,
        "configured": connection is not None,
        "max_tags_per_entry": max_tags_per_entry(db),
        "promotion_threshold": promotion_threshold(db),
        "support_window_days": support_window_days(db),
        "canonical_language": canonical_language(db),
        "min_confidence": AUTO_TAG_MIN_CONFIDENCE,
        "preview_required": auto_tag_preview_required(db),
        "proposal_count": proposal_count,
        "promoted_count": promoted_count,
        "estimated_calls": math.ceil(
            (
                int(db.scalar(select(func.count(Entry.id))) or 0)
                if auto_tag_preview_required(db)
                else remaining_count
            )
            / AUTO_TAG_BATCH_SIZE
        ),
        # Completed results remain valid as the policy and taxonomy evolve.
        # Retain these response fields for compatibility with existing clients.
        "outdated_count": 0,
        "needs_rebuild": False,
        "counts": {
            "pending": pending_count,
            "running": int(counts.get("running", 0)),
            "complete": int(counts.get("complete", 0)),
            "failed": int(counts.get("failed", 0)),
        },
    }


def _preview_target_sample_size(db: Session, sample_size: int = 50) -> int:
    available_work_count = int(
        db.scalar(select(func.count(func.distinct(Entry.work_id)))) or 0
    )
    return min(sample_size, available_work_count)


def _preview_sample_entries(db: Session, sample_size: int) -> list[Entry]:
    groups: dict[int, list[Entry]] = {}
    feed_ids = list(
        db.scalars(select(EntryFeed.feed_id).distinct().order_by(EntryFeed.feed_id))
    )
    for feed_id in feed_ids:
        groups[feed_id] = list(
            db.scalars(
                select(Entry)
                .join(EntryFeed, EntryFeed.entry_id == Entry.id)
                .where(EntryFeed.feed_id == feed_id)
                .order_by(
                    func.coalesce(Entry.published_at, Entry.created_at).desc(),
                    Entry.id.desc(),
                )
            )
        )

    # Entries without a feed remain eligible as a fallback group.
    groups[0] = list(
        db.scalars(
            select(Entry)
            .outerjoin(EntryFeed, EntryFeed.entry_id == Entry.id)
            .where(EntryFeed.id.is_(None))
            .order_by(
                func.coalesce(Entry.published_at, Entry.created_at).desc(),
                Entry.id.desc(),
            )
        )
    )
    selected: list[Entry] = []
    selected_work_ids: set[int] = set()
    group_ids = sorted(groups)
    while len(selected) < sample_size and any(groups.values()):
        for feed_id in group_ids:
            while groups[feed_id]:
                candidate = groups[feed_id].pop(0)
                if candidate.work_id in selected_work_ids:
                    continue
                selected.append(candidate)
                selected_work_ids.add(candidate.work_id)
                break
            if len(selected) >= sample_size:
                break
    return selected


def _preview_dict(preview: AutoTagPreview) -> dict[str, Any]:
    return {
        "id": preview.id,
        "status": preview.status,
        "sample_size": preview.sample_size,
        "entry_ids": list(preview.entry_ids or []),
        "results": list(preview.results or []),
        "metrics": dict(preview.metrics or {}),
        "last_error": preview.last_error,
        "created_at": preview.created_at,
        "updated_at": preview.updated_at,
    }


def create_preview(db: Session, sample_size: int = 50) -> AutoTagPreview:
    if not auto_tag_preview_enabled():
        raise ValueError("Auto-tag previews are currently disabled")
    if get_feature_connection(db, AUTO_TAG_FEATURE) is None:
        raise ValueError("Select an existing LLM connection before running a preview")
    if sample_size != 50:
        raise ValueError("Auto-tag previews must sample 50 articles")
    _require_cleanup_review(db)
    target_sample_size = _preview_target_sample_size(db, sample_size)
    if target_sample_size == 0:
        raise ValueError("No articles are available for preview")
    entries = _preview_sample_entries(db, target_sample_size)
    if len(entries) != target_sample_size:
        raise ValueError(
            "Could not build the complete distinct-work preview sample; try again"
        )
    preview = AutoTagPreview(
        status="pending",
        sample_size=target_sample_size,
        entry_ids=[entry.id for entry in entries],
        results=[],
        metrics={},
    )
    db.add(preview)
    db.flush()
    db.commit()
    db.refresh(preview)
    return preview


def get_preview(db: Session, preview_id: int) -> AutoTagPreview | None:
    return db.get(AutoTagPreview, preview_id)


def list_previews(db: Session, limit: int = 10) -> dict[str, Any]:
    items = list(
        db.scalars(
            select(AutoTagPreview)
            .order_by(AutoTagPreview.id.desc())
            .limit(max(1, min(limit, 50)))
        )
    )
    return {"items": [_preview_dict(item) for item in items]}


def _classify_preview_batch(
    db: Session,
    entries: list[Entry],
    expected_policy_version: str,
    *,
    attempt: int = 1,
) -> list[tuple[int, str, str, EntryClassification]]:
    source_hashes = {entry.id: entry.source_hash for entry in entries}
    titles = {entry.id: entry.title for entry in entries}
    try:
        result, tag_ids, proposal_ids = _classify_batch(db, entries)
        db.expire_all()
        current_hashes = dict(
            db.execute(
                select(Entry.id, Entry.source_hash).where(
                    Entry.id.in_(source_hashes)
                )
            ).all()
        )
        if any(
            current_hashes.get(entry_id) != source_hash
            for entry_id, source_hash in source_hashes.items()
        ):
            raise AutoTagFormatError("Article content changed during the preview")
        if _policy_version(db) != expected_policy_version:
            raise ValueError("Auto-tag policy or taxonomy changed during the preview")
        valid, errors = _validated_entry_results(
            result,
            entries,
            tag_ids,
            proposal_ids,
            growth_mode=auto_tag_growth_mode(db),
        )
        if errors:
            raise AutoTagFormatError("; ".join(sorted(set(errors.values()))))
        return [
            (entry.id, titles[entry.id], source_hashes[entry.id], valid[entry.id])
            for entry in entries
        ]
    except AutoTagFormatError:
        if attempt >= AUTO_TAG_MAX_ATTEMPTS:
            raise
        if len(entries) > 1:
            midpoint = len(entries) // 2
            return _classify_preview_batch(
                db,
                entries[:midpoint],
                expected_policy_version,
                attempt=attempt + 1,
            ) + _classify_preview_batch(
                db,
                entries[midpoint:],
                expected_policy_version,
                attempt=attempt + 1,
            )
        return _classify_preview_batch(
            db,
            entries,
            expected_policy_version,
            attempt=attempt + 1,
        )
    except LLMConnectionError as exc:
        if not exc.retryable or attempt >= AUTO_TAG_MAX_ATTEMPTS:
            raise
        db.rollback()
        sleep(AUTO_TAG_PREVIEW_RETRY_SECONDS[attempt - 1])
        return _classify_preview_batch(
            db,
            entries,
            expected_policy_version,
            attempt=attempt + 1,
        )


def run_auto_tag_preview(db: Session, preview_id: int) -> AutoTagPreview:
    if not auto_tag_preview_enabled():
        raise ValueError("Auto-tag previews are currently disabled")
    preview = db.get(AutoTagPreview, preview_id)
    if preview is None:
        raise ValueError("Auto-tag preview not found")
    if preview.status == "complete":
        return preview
    preview.status = "running"
    preview.last_error = None
    expected_policy_version = _policy_version(db)
    db.commit()
    entries = [db.get(Entry, entry_id) for entry_id in (preview.entry_ids or [])]
    entries = [entry for entry in entries if entry is not None]
    serialized: list[dict[str, Any]] = []
    try:
        target_sample_size = _preview_target_sample_size(db)
        if (
            preview.sample_size != target_sample_size
            or len(entries) != preview.sample_size
            or len(entries) != len(set(preview.entry_ids or []))
            or len({entry.work_id for entry in entries}) != preview.sample_size
        ):
            raise AutoTagFormatError(
                "The preview sample changed before classification; run a new preview"
            )
        for start in range(0, len(entries), AUTO_TAG_BATCH_SIZE):
            batch = entries[start : start + AUTO_TAG_BATCH_SIZE]
            for entry_id, title, source_hash, item in _classify_preview_batch(
                db,
                batch,
                expected_policy_version,
            ):
                topics: list[dict[str, Any]] = []
                for topic in item.topics:
                    name = topic.name
                    if topic.kind == "tag":
                        tag = db.get(Tag, topic.id)
                        name = tag.name if tag else None
                    elif topic.kind == "proposal":
                        proposal = db.get(TagProposal, topic.id)
                        name = proposal.name if proposal else None
                    topics.append(
                        {
                            "kind": topic.kind,
                            "id": topic.id,
                            "name": name,
                            "description": topic.description,
                            "aliases": topic.aliases,
                            "confidence": topic.confidence,
                        }
                    )
                serialized.append(
                    {
                        "entry_id": entry_id,
                        "source_hash": source_hash,
                        "title": title,
                        "topics": topics,
                    }
                )
        topic_count = sum(len(item["topics"]) for item in serialized)
        preview.results = serialized
        preview.metrics = {
            "classified_count": len(serialized),
            "topic_count": topic_count,
            "zero_topic_count": sum(not item["topics"] for item in serialized),
            "new_candidate_count": sum(
                topic["kind"] == "new"
                for item in serialized
                for topic in item["topics"]
            ),
            "estimated_full_calls": math.ceil(
                (db.scalar(select(func.count(Entry.id))) or 0) / AUTO_TAG_BATCH_SIZE
            ),
            "policy_version": expected_policy_version,
        }
        preview.status = "complete"
        preview.last_error = None
        db.commit()
        return preview
    except Exception as exc:
        db.rollback()
        preview = db.get(AutoTagPreview, preview_id)
        assert preview is not None
        preview.status = "failed"
        preview.last_error = str(exc)[:4000]
        db.commit()
        raise


def approve_preview(db: Session, preview_id: int, scope: str = "all") -> AutoTagPreview:
    if not auto_tag_preview_enabled():
        raise ValueError("Auto-tag previews are currently disabled")
    if scope != "all":
        raise ValueError("Only all-history approval is supported")
    preview = db.get(AutoTagPreview, preview_id)
    if preview is None:
        raise ValueError("Auto-tag preview not found")
    if preview.status != "complete":
        raise ValueError("The preview must complete successfully before approval")
    acquire_cleanup_snapshot(db)
    _require_cleanup_review(db)

    stored_results = list(preview.results or [])
    preview_policy = (preview.metrics or {}).get("policy_version")
    claimed = db.execute(
        update(AutoTagPreview)
        .where(
            AutoTagPreview.id == preview_id,
            AutoTagPreview.status == "complete",
        )
        .values(status="applying")
    ).rowcount
    if claimed != 1:
        raise ValueError("The preview is already being applied")
    try:
        expected_entry_ids = [int(entry_id) for entry_id in (preview.entry_ids or [])]
        result_entry_ids = [int(stored["entry_id"]) for stored in stored_results]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("The preview results are incomplete; run a new preview") from exc
    if (
        preview.sample_size != len(expected_entry_ids)
        or len(expected_entry_ids) != len(set(expected_entry_ids))
        or len(result_entry_ids) != len(set(result_entry_ids))
        or set(result_entry_ids) != set(expected_entry_ids)
    ):
        raise ValueError("The preview results are incomplete; run a new preview")
    locked_entries = {
        entry.id: entry
        for entry in db.scalars(
            select(Entry)
            .where(
                Entry.id.in_(
                    expected_entry_ids
                )
            )
            .with_for_update()
        )
    }
    if preview_policy is None or preview_policy != _policy_version(db):
        raise ValueError("The auto-tag policy or taxonomy changed; run a new preview")
    target_sample_size = _preview_target_sample_size(db)
    if (
        preview.sample_size != target_sample_size
        or len(locked_entries) != preview.sample_size
        or len({entry.work_id for entry in locked_entries.values()})
        != preview.sample_size
        or any(
            locked_entries[entry_id].source_hash != stored.get("source_hash")
            for stored in stored_results
            for entry_id in [int(stored["entry_id"])]
            if entry_id in locked_entries
        )
    ):
        raise ValueError("The preview sample changed; run a new preview")

    policy_version = _policy_version(db)
    approved_entry_ids: set[int] = set()
    promotion_events: list[dict[str, Any]] = []
    for stored in stored_results:
        entry = locked_entries.get(int(stored["entry_id"]))
        if entry is None or entry.source_hash != stored.get("source_hash"):
            raise ValueError("The preview sample changed; run a new preview")
        topics = [TopicChoice.model_validate(topic) for topic in stored.get("topics", [])]
        result = EntryClassification(entry_id=entry.id, topics=topics)
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry.id)
        )
        if record is None:
            record = AutoTagRecord(
                entry_id=entry.id,
                source_hash=entry.source_hash,
                status="pending",
                tag_ids=[],
                policy_version=None,
            )
            db.add(record)
            db.flush()
        _apply_classification(db, record, entry, result, policy_version, promotion_events)
        approved_entry_ids.add(entry.id)

    ensure_auto_tag_queue(db)
    for record in db.scalars(select(AutoTagRecord)):
        if record.entry_id in approved_entry_ids:
            continue
        record.status = "pending"
        record.attempts = 0
        record.last_error = None
        record.next_retry_at = None
    preview.status = "applied"
    _set_setting(db, "auto_tag_preview_required", "false")
    _set_setting(db, "auto_tag_enabled", "true")
    db.commit()
    _log_promotions(promotion_events)
    return preview


def cleanup_preview(db: Session) -> dict[str, Any]:
    inferred_by_tag: dict[int, set[int]] = defaultdict(set)
    existing_links = set(db.execute(select(EntryTag.entry_id, EntryTag.tag_id)).all())
    for record in db.scalars(select(AutoTagRecord)):
        for tag_id in record.tag_ids or []:
            normalized_tag_id = int(tag_id)
            if (record.entry_id, normalized_tag_id) in existing_links:
                inferred_by_tag[normalized_tag_id].add(record.entry_id)
    source_counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for tag_id, source, count in db.execute(
        select(EntryTag.tag_id, EntryTagSource.source, func.count(EntryTagSource.id))
        .join(EntryTagSource, EntryTagSource.entry_tag_id == EntryTag.id)
        .group_by(EntryTag.tag_id, EntryTagSource.source)
    ):
        source_counts[tag_id][source] = int(count)
    total_counts = dict(
        db.execute(
            select(EntryTag.tag_id, func.count(EntryTag.id)).group_by(EntryTag.tag_id)
        ).all()
    )
    feed_counts = dict(
        db.execute(select(FeedTag.tag_id, func.count(FeedTag.id)).group_by(FeedTag.tag_id)).all()
    )
    schedule_counts: dict[int, int] = defaultdict(int)
    for schedule in db.scalars(select(BriefSchedule).where(BriefSchedule.enabled.is_(True))):
        for tag_id in set(schedule.tag_ids or []):
            schedule_counts[int(tag_id)] += 1
    items: list[dict[str, Any]] = []
    inferred_total = 0
    for tag in db.scalars(select(Tag).order_by(Tag.name)):
        inferred = len(inferred_by_tag.get(tag.id, set()))
        if inferred == 0:
            continue
        inferred_total += inferred
        counts = source_counts.get(tag.id, {})
        items.append(
            {
                "tag_id": tag.id,
                "name": tag.name,
                "total_count": int(total_counts.get(tag.id, 0)),
                "inferred_auto_count": inferred,
                "legacy_count": int(counts.get("legacy", 0)),
                "manual_count": int(counts.get("manual", 0)),
                "auto_count": int(counts.get("auto", 0)),
                "feed_count": int(feed_counts.get(tag.id, 0)),
                "schedule_count": int(schedule_counts.get(tag.id, 0)),
                "deletable": (
                    int(total_counts.get(tag.id, 0)) == inferred
                    and not counts.get("manual", 0)
                    and not feed_counts.get(tag.id)
                    and not schedule_counts.get(tag.id)
                ),
            }
        )
    sources_by_association: dict[tuple[int, int], set[str]] = defaultdict(set)
    for entry_id, tag_id, source in db.execute(
        select(EntryTag.entry_id, EntryTag.tag_id, EntryTagSource.source)
        .join(EntryTagSource, EntryTagSource.entry_tag_id == EntryTag.id)
        .where(EntryTag.tag_id.in_(sorted(inferred_by_tag)))
    ):
        if entry_id in inferred_by_tag.get(tag_id, set()):
            sources_by_association[(int(entry_id), int(tag_id))].add(str(source))
    associations = [
        {
            "entry_id": entry_id,
            "tag_id": tag_id,
            "sources": sorted(sources_by_association.get((entry_id, tag_id), set())),
        }
        for tag_id in sorted(inferred_by_tag)
        for entry_id in sorted(inferred_by_tag[tag_id])
    ]
    snapshot = {
        "revision": AUTO_TAG_CLEANUP_SNAPSHOT_REVISION,
        "items": items,
        "associations": associations,
    }
    review_token = hashlib.sha256(
        json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "items": items,
        "inferred_auto_association_count": inferred_total,
        "review_token": review_token,
        "reviewed": _setting(db, AUTO_TAG_CLEANUP_REVIEW_KEY, "") == review_token,
    }


def _require_cleanup_review(db: Session) -> str:
    if not legacy_auto_tag_cleanup_enabled():
        return ""
    preview = cleanup_preview(db)
    if not preview["reviewed"]:
        raise ValueError(
            "Review and confirm the cleanup preview before running or approving "
            "the 50-entry auto-tag preview"
        )
    return str(preview["review_token"])


def _delete_link_if_unowned(db: Session, link: EntryTag) -> None:
    db.flush()
    if db.scalar(
        select(EntryTagSource.id).where(EntryTagSource.entry_tag_id == link.id).limit(1)
    ) is None:
        db.delete(link)
        db.flush()


def apply_cleanup(
    db: Session,
    *,
    remove_tag_ids: list[int],
    keep_tag_ids: list[int],
    review_token: str,
) -> dict[str, Any]:
    acquire_topic_namespace(db)
    current_preview = cleanup_preview(db)
    if review_token != current_preview["review_token"]:
        raise ValueError("The cleanup preview changed; review it again before applying")
    inferred: dict[int, set[int]] = defaultdict(set)
    records = list(db.scalars(select(AutoTagRecord)))
    records_by_entry = {record.entry_id: record for record in records}
    existing_links = set(db.execute(select(EntryTag.entry_id, EntryTag.tag_id)).all())
    for record in records:
        current_tag_ids = [
            int(tag_id)
            for tag_id in (record.tag_ids or [])
            if (record.entry_id, int(tag_id)) in existing_links
        ]
        if current_tag_ids != list(record.tag_ids or []):
            record.tag_ids = current_tag_ids
        for tag_id in current_tag_ids:
            inferred[tag_id].add(record.entry_id)

    eligible_ids = set(inferred)
    remove_ids = set(remove_tag_ids) & eligible_ids
    keep_ids = (set(keep_tag_ids) & eligible_ids) - remove_ids

    for tag_id in keep_ids:
        for entry_id in inferred.get(tag_id, set()):
            link = db.scalar(
                select(EntryTag).where(
                    EntryTag.entry_id == entry_id,
                    EntryTag.tag_id == tag_id,
                )
            )
            if link is None:
                record = records_by_entry.get(entry_id)
                if record is not None:
                    record.tag_ids = [
                        value for value in (record.tag_ids or []) if value != tag_id
                    ]
                continue
            legacy = db.scalar(
                select(EntryTagSource).where(
                    EntryTagSource.entry_tag_id == link.id,
                    EntryTagSource.source == "legacy",
                )
            )
            manual = db.scalar(
                select(EntryTagSource).where(
                    EntryTagSource.entry_tag_id == link.id,
                    EntryTagSource.source == "manual",
                )
            )
            if manual is None:
                db.add(EntryTagSource(entry_tag_id=link.id, source="manual"))
            if legacy is not None:
                db.delete(legacy)
            automatic = db.scalar(
                select(EntryTagSource).where(
                    EntryTagSource.entry_tag_id == link.id,
                    EntryTagSource.source == "auto",
                )
            )
            if automatic is not None:
                db.delete(automatic)
            suppression = db.scalar(
                select(AutoTagSuppression).where(
                    AutoTagSuppression.entry_id == entry_id,
                    AutoTagSuppression.tag_id == tag_id,
                )
            )
            if suppression is not None:
                db.delete(suppression)
            record = records_by_entry.get(entry_id)
            if record is not None:
                record.tag_ids = [
                    value for value in (record.tag_ids or []) if value != tag_id
                ]

    removed_associations = 0
    deleted_tag_ids: list[int] = []
    for tag_id in remove_ids:
        for entry_id in inferred.get(tag_id, set()):
            link = db.scalar(
                select(EntryTag).where(
                    EntryTag.entry_id == entry_id,
                    EntryTag.tag_id == tag_id,
                )
            )
            if link is None:
                continue
            if db.scalar(
                select(AutoTagSuppression.id).where(
                    AutoTagSuppression.entry_id == entry_id,
                    AutoTagSuppression.tag_id == tag_id,
                )
            ) is None:
                db.add(
                    AutoTagSuppression(
                        entry_id=entry_id,
                        tag_id=tag_id,
                        reason="owner_cleanup",
                    )
                )
            sources = list(
                db.scalars(
                    select(EntryTagSource).where(
                        EntryTagSource.entry_tag_id == link.id,
                        EntryTagSource.source.in_(("legacy", "auto")),
                    )
                )
            )
            for source in sources:
                db.delete(source)
            if sources:
                removed_associations += 1
            _delete_link_if_unowned(db, link)
        for record in records:
            if tag_id in (record.tag_ids or []):
                record.tag_ids = [value for value in record.tag_ids if value != tag_id]

        tag = db.get(Tag, tag_id)
        if tag is None:
            continue
        has_entry = db.scalar(select(EntryTag.id).where(EntryTag.tag_id == tag_id).limit(1))
        has_feed = db.scalar(select(FeedTag.id).where(FeedTag.tag_id == tag_id).limit(1))
        has_schedule = any(
            tag_id in (schedule.tag_ids or [])
            for schedule in db.scalars(
                select(BriefSchedule).where(BriefSchedule.enabled.is_(True))
            )
        )
        if has_entry is None and has_feed is None and not has_schedule:
            retire_tag_proposals(db, tag_id)
            db.delete(tag)
            deleted_tag_ids.append(tag_id)
    db.flush()
    reviewed_preview = cleanup_preview(db)
    _set_setting(
        db,
        AUTO_TAG_CLEANUP_REVIEW_KEY,
        str(reviewed_preview["review_token"]),
    )
    db.commit()
    return {
        "removed_tag_ids": deleted_tag_ids,
        "kept_tag_ids": sorted(keep_ids),
        "removed_count": removed_associations,
    }


def list_proposals(
    db: Session, offset: int = 0, limit: int = 100, *, status: str | None = None,
) -> dict[str, Any]:
    conditions = [TagProposal.status == status] if status is not None else []
    total = int(db.scalar(select(func.count(TagProposal.id)).where(*conditions)) or 0)
    cutoff = _support_cutoff(db)
    current_supports = (
        select(
            TagProposalSupport.proposal_id.label("proposal_id"),
            func.count(func.distinct(TagProposalSupport.work_id)).label(
                "support_count"
            ),
        )
        .join(Entry, Entry.id == TagProposalSupport.entry_id)
        .where(
            TagProposalSupport.source_hash == Entry.source_hash,
            func.coalesce(Entry.published_at, Entry.created_at) >= cutoff,
        )
        .group_by(TagProposalSupport.proposal_id)
        .subquery()
    )
    support_count = func.coalesce(current_supports.c.support_count, 0)
    page = list(
        db.execute(
            select(
                TagProposal,
                support_count.label("current_support_count"),
            )
            .outerjoin(
                current_supports,
                current_supports.c.proposal_id == TagProposal.id,
            )
            .where(*conditions)
            .order_by(
                TagProposal.status,
                support_count.desc(),
                TagProposal.id,
            )
            .offset(max(0, offset))
            .limit(max(1, min(limit, 200)))
        )
    )
    proposal_ids = [proposal.id for proposal, _count in page]
    aliases_by_proposal: dict[int, list[str]] = defaultdict(list)
    if proposal_ids:
        for proposal_id, alias in db.execute(
            select(TagProposalAlias.proposal_id, TagProposalAlias.alias)
            .where(TagProposalAlias.proposal_id.in_(proposal_ids))
            .order_by(TagProposalAlias.proposal_id, TagProposalAlias.alias)
        ):
            aliases_by_proposal[proposal_id].append(alias)

    items: list[dict[str, Any]] = []
    for proposal, current_support_count in page:
        items.append(
            {
                "id": proposal.id,
                "name": proposal.name,
                "normalized_name": proposal.normalized_name,
                "description": proposal.description or "",
                "status": proposal.status,
                "support_count": int(current_support_count),
                "promoted_tag_id": proposal.promoted_tag_id,
                "aliases": aliases_by_proposal[proposal.id],
                "created_at": proposal.created_at,
                "updated_at": proposal.updated_at,
            }
        )
    return {"items": items, "total": total}


def merge_tags(db: Session, source_tag_id: int, target_tag_id: int) -> Tag:
    acquire_topic_namespace(db)
    if source_tag_id == target_tag_id:
        raise ValueError("Source and target tags must differ")
    source = db.get(Tag, source_tag_id)
    target = db.get(Tag, target_tag_id)
    if source is None or target is None:
        raise ValueError("Tag not found")
    old_names = [source.name] + list(
        db.scalars(select(TagAlias.alias).where(TagAlias.tag_id == source.id))
    )

    for source_link in list(
        db.scalars(select(EntryTag).where(EntryTag.tag_id == source.id))
    ):
        target_link = _entry_tag(db, source_link.entry_id, target.id)
        if source_link.weight is not None:
            target_link.weight = max(target_link.weight or 0, source_link.weight)
        for source_row in list(
            db.scalars(
                select(EntryTagSource).where(
                    EntryTagSource.entry_tag_id == source_link.id
                )
            )
        ):
            target_row = db.scalar(
                select(EntryTagSource).where(
                    EntryTagSource.entry_tag_id == target_link.id,
                    EntryTagSource.source == source_row.source,
                )
            )
            if target_row is None:
                db.add(
                    EntryTagSource(
                        entry_tag_id=target_link.id,
                        source=source_row.source,
                        confidence=source_row.confidence,
                        policy_version=source_row.policy_version,
                    )
                )
            elif source_row.confidence is not None:
                target_row.confidence = max(
                    target_row.confidence or 0,
                    source_row.confidence,
                )
        db.delete(source_link)
        db.flush()

    for feed_link in list(
        db.scalars(select(FeedTag).where(FeedTag.tag_id == source.id))
    ):
        if db.scalar(
            select(FeedTag.id).where(
                FeedTag.feed_id == feed_link.feed_id,
                FeedTag.tag_id == target.id,
            )
        ) is None:
            db.add(FeedTag(feed_id=feed_link.feed_id, tag_id=target.id))
        db.delete(feed_link)

    suppressed_entry_ids: set[int] = set()
    for suppression in list(
        db.scalars(
            select(AutoTagSuppression).where(AutoTagSuppression.tag_id == source.id)
        )
    ):
        suppressed_entry_ids.add(suppression.entry_id)
        existing = db.scalar(
            select(AutoTagSuppression.id).where(
                AutoTagSuppression.entry_id == suppression.entry_id,
                AutoTagSuppression.tag_id == target.id,
            )
        )
        if existing is None:
            suppression.tag_id = target.id
        else:
            db.delete(suppression)
        target_link = db.scalar(
            select(EntryTag).where(
                EntryTag.entry_id == suppression.entry_id,
                EntryTag.tag_id == target.id,
            )
        )
        if target_link is not None:
            target_auto = db.scalar(
                select(EntryTagSource).where(
                    EntryTagSource.entry_tag_id == target_link.id,
                    EntryTagSource.source == "auto",
                )
            )
            if target_auto is not None:
                db.delete(target_auto)
                _delete_link_if_unowned(db, target_link)

    for suppression in list(
        db.scalars(
            select(AutoTagSuppression).where(
                AutoTagSuppression.tag_id == target.id
            )
        )
    ):
        suppressed_entry_ids.add(suppression.entry_id)
        target_link = db.scalar(
            select(EntryTag).where(
                EntryTag.entry_id == suppression.entry_id,
                EntryTag.tag_id == target.id,
            )
        )
        if target_link is None:
            continue
        target_auto = db.scalar(
            select(EntryTagSource).where(
                EntryTagSource.entry_tag_id == target_link.id,
                EntryTagSource.source == "auto",
            )
        )
        if target_auto is not None:
            db.delete(target_auto)
            _delete_link_if_unowned(db, target_link)

    for record in db.scalars(select(AutoTagRecord)):
        if source.id in (record.tag_ids or []):
            record.tag_ids = sorted(
                {target.id if value == source.id else value for value in record.tag_ids}
            )
        if record.entry_id in suppressed_entry_ids:
            record.tag_ids = _current_auto_tag_ids(db, record.entry_id)
    for schedule in db.scalars(select(BriefSchedule)):
        if source.id in (schedule.tag_ids or []):
            schedule.tag_ids = sorted(
                {target.id if value == source.id else value for value in schedule.tag_ids}
            )
    for proposal in db.scalars(
        select(TagProposal).where(TagProposal.promoted_tag_id == source.id)
    ):
        proposal.promoted_tag_id = target.id

    db.delete(source)
    db.flush()
    for old_name in old_names:
        _add_tag_alias(db, target, old_name)
    db.commit()
    db.refresh(target)
    return target
