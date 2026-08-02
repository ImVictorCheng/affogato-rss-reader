from __future__ import annotations

import re
from datetime import datetime
from datetime import timezone
from time import perf_counter
from threading import Lock
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import String, and_, cast, delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .bootstrap import remove_initial_owner_password
from .call_logging import read_call_logs
from .db import get_db
from .briefs import (
    brief_item_count,
    brief_markdown,
    create_manual_brief,
    read_brief_rule,
    reset_brief_rule,
    run_due_schedules,
    save_brief_rule,
)
from .models import (
    AppSetting,
    Brief,
    BriefGenerationCheckpoint,
    BriefSchedule,
    Domain,
    Entry,
    EntryDomain,
    EntryFeed,
    EntryTag,
    Feed,
    FeedDomain,
    Folder,
    Job,
    Owner,
    ReadingState,
    Session as LoginSession,
    SyncRun,
    Tag,
    Translation,
    utcnow,
)
from .opml import export_opml_document, import_opml_document
from .schemas import (
    AIThemeRequest,
    AIThemeResponse,
    AppSettingsOut,
    AuthStatus,
    BriefCreate,
    BriefConfigurationOut,
    BriefConfigurationUpdate,
    BriefDetailOut,
    BriefGenerationProgressOut,
    BriefListOut,
    BriefOut,
    BriefRuleOut,
    BriefRuleUpdate,
    BriefScheduleCreate,
    BriefScheduleListOut,
    BriefScheduleOut,
    BriefScheduleUpdate,
    BriefUpdate,
    BulkState,
    CallLogListOut,
    DiscoverBody,
    DomainCreate,
    DomainListOut,
    DomainOut,
    DomainUpdate,
    EntriesPage,
    EntryDomainsPatch,
    EntryOut,
    FeedCreate,
    FeedDomainAssociation,
    FeedDiscoveryOut,
    FeedListOut,
    FeedOut,
    FeedReorder,
    FeedUpdate,
    FolderCreate,
    FolderListOut,
    FolderOut,
    FolderUpdate,
    JobListOut,
    LLMConnectionCreate,
    LLMConnectionOut,
    LLMConnectionTest,
    LLMConnectionTestOut,
    LLMConnectionUpdate,
    NetworkProxyOut,
    NetworkProxyTest,
    NetworkProxyTestOut,
    NetworkProxyUpdate,
    OpmlImportOut,
    OnboardingComplete,
    OnboardingProfile,
    OwnerActivationBody,
    PasswordBody,
    StatePatch,
    SourceSortSettings,
    SyncRunListOut,
    TagCreate,
    TagListOut,
    TagOut,
    TagWithCountOut,
    TranslationRetry,
    TranslationStatusOut,
    TranslationTest,
    TranslationTestOut,
    TranslationToggle,
    UpdateStatusOut,
)
from .personalization import generate_ai_theme
from .network_proxy import (
    http_route_for_llm_connection,
    network_proxy_summary,
    save_network_proxy_config,
    test_custom_proxy_targets,
)
from .updates import (
    UpdateError,
    UpdateInstallUnavailable,
    check_for_updates,
    request_update_install,
    update_status,
)
from .llm import (
    BRIEF_FEATURE,
    LLMConnectionError,
    LLMConnectionInUseError,
    bind_llm_connection,
    decrypt_llm_api_key,
    delete_llm_connection,
    get_llm_connection,
    get_feature_connection,
    list_llm_connections,
    llm_connection_summary,
    probe_llm_connection,
    save_llm_connection,
    unbind_llm_connection,
)
from .security import (
    COOKIE_NAME,
    clear_login_failures,
    create_login_session,
    current_owner,
    delete_login_session,
    enforce_login_rate_limit,
    hash_password,
    noauth_csrf_token,
    record_login_failure,
    require_csrf,
    token_hash,
    verify_password,
)
from .secrets import SecretKeyError
from .sync import discover_feeds, sync_feed
from .translation import (
    TranslationConfigurationError,
    TranslationError,
    build_selected_translation_provider,
    configure_translation,
    get_translation_record,
    is_translation_enabled,
    queue_retry,
    translation_status,
    translate_with_log,
    translation_target,
)

router = APIRouter()


def not_found(resource: str = "Resource") -> HTTPException:
    return HTTPException(status_code=404, detail=f"{resource} not found")


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def auth_payload(owner: Owner, csrf_token: str, settings: Settings) -> dict:
    return {
        "setup_required": False,
        "activation_required": False,
        "authenticated": True,
        "onboarding_required": not owner.onboarding_completed,
        "mode": settings.auth_mode,
        "warning": (
            "Authentication is disabled. Anyone who can reach this service can read and change its data."
            if settings.auth_mode == "none"
            else None
        ),
        "csrf_token": csrf_token,
        "owner": {"name": owner.username},
        "theme": owner.theme or None,
    }


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "database": "ok",
        "time": utcnow().isoformat() + "Z",
    }


@router.get("/auth/status", response_model=AuthStatus)
def auth_status(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    raw_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> dict:
    owner = db.scalar(select(Owner).limit(1))
    if settings.auth_mode == "none":
        if owner is None:
            owner = Owner(id=1, username="owner", password_hash=None)
            db.add(owner)
            db.commit()
        return auth_payload(owner, noauth_csrf_token(), settings)
    if owner is None:
        return {
            "setup_required": True,
            "activation_required": False,
            "authenticated": False,
            "onboarding_required": True,
            "mode": "owner",
            "warning": None,
            "csrf_token": None,
            "owner": None,
        }
    if not raw_token:
        return {
            "setup_required": False,
            "activation_required": owner.activation_required,
            "authenticated": False,
            "onboarding_required": not owner.onboarding_completed,
            "mode": "owner",
            "warning": None,
            "csrf_token": None,
            "owner": None,
            "theme": owner.theme or None,
        }
    login = db.scalar(select(LoginSession).where(LoginSession.token_hash == token_hash(raw_token)))
    if login is None or login.expires_at <= utcnow():
        return {
            "setup_required": False,
            "activation_required": owner.activation_required,
            "authenticated": False,
            "onboarding_required": not owner.onboarding_completed,
            "mode": "owner",
            "warning": None,
            "csrf_token": None,
            "owner": None,
            "theme": owner.theme or None,
        }
    return auth_payload(owner, login.csrf_token, settings)


@router.post("/auth/activate", response_model=AuthStatus)
def activate_owner(
    body: OwnerActivationBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if settings.auth_mode == "none":
        raise HTTPException(status_code=409, detail="Owner activation is disabled in no-auth mode")
    client_key = request.client.host if request.client else "unknown"
    enforce_login_rate_limit(client_key)
    owner = db.scalar(select(Owner).limit(1))
    if owner is None:
        raise HTTPException(status_code=409, detail="Initial setup is required")
    if not owner.activation_required:
        raise HTTPException(status_code=409, detail="Owner has already been activated")
    if not owner.password_hash or not verify_password(body.initial_password, owner.password_hash):
        record_login_failure(client_key)
        raise HTTPException(status_code=401, detail="Invalid initial password")
    if body.password == body.initial_password:
        raise HTTPException(
            status_code=422,
            detail="The permanent password must differ from the initial password",
        )
    try:
        password_hash = hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = db.execute(
        update(Owner)
        .where(
            Owner.id == owner.id,
            Owner.activation_required.is_(True),
            Owner.password_hash == owner.password_hash,
        )
        .values(password_hash=password_hash, activation_required=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Owner activation has already completed")
    db.execute(delete(LoginSession).where(LoginSession.owner_id == owner.id))
    db.commit()
    db.refresh(owner)
    clear_login_failures(client_key)
    remove_initial_owner_password(settings)
    login_session = create_login_session(db, owner, response, settings)
    return auth_payload(owner, login_session.csrf_token, settings)


@router.post("/auth/setup", status_code=201, response_model=AuthStatus)
def setup_owner(
    body: PasswordBody,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if settings.auth_mode == "none":
        raise HTTPException(status_code=409, detail="Owner setup is disabled in no-auth mode")
    if db.scalar(select(Owner.id).limit(1)) is not None:
        raise HTTPException(status_code=409, detail="Owner has already been configured")
    try:
        owner = Owner(id=1, username="owner", password_hash=hash_password(body.password))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.add(owner)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Owner has already been configured") from exc
    login = create_login_session(db, owner, response, settings)
    return auth_payload(owner, login.csrf_token, settings)


@router.post("/auth/login", response_model=AuthStatus)
def login(
    body: PasswordBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if settings.auth_mode == "none":
        raise HTTPException(status_code=409, detail="Login is disabled in no-auth mode")
    client_key = request.client.host if request.client else "unknown"
    enforce_login_rate_limit(client_key)
    owner = db.scalar(select(Owner).limit(1))
    if owner is None:
        raise HTTPException(status_code=409, detail="Initial setup is required")
    if owner.activation_required:
        raise HTTPException(status_code=409, detail="Owner activation is required")
    if not owner.password_hash or not verify_password(body.password, owner.password_hash):
        record_login_failure(client_key)
        raise HTTPException(status_code=401, detail="Invalid password")
    clear_login_failures(client_key)
    login_session = create_login_session(db, owner, response, settings)
    return auth_payload(owner, login_session.csrf_token, settings)


@router.post("/auth/logout", status_code=204)
def logout(
    response: Response,
    _login: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    raw_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> None:
    delete_login_session(db, raw_token, response)


@router.delete("/debug/owner", status_code=204)
def debug_delete_owner(
    response: Response,
    owner: Owner = Depends(current_owner),
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.debug:
        raise HTTPException(status_code=404, detail="Debug reset is not enabled")
    if settings.auth_mode != "owner":
        raise HTTPException(status_code=409, detail="Debug owner reset requires owner authentication mode")
    db.delete(owner)
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


def onboarding_payload(owner: Owner) -> dict:
    return {
        "completed": owner.onboarding_completed,
        "selected_domains": owner.selected_domains or [],
        "primary_domain": owner.primary_domain,
        "theme": owner.theme or None,
        "ai_personalized": owner.ai_personalized,
        "ai_provider": owner.ai_provider,
    }


@router.get("/onboarding", response_model=OnboardingProfile)
def get_onboarding_profile(owner: Owner = Depends(current_owner)) -> dict:
    return onboarding_payload(owner)


@router.post("/onboarding/ai-theme", response_model=AIThemeResponse)
async def create_ai_theme(
    body: AIThemeRequest,
    _owner: Owner = Depends(current_owner),
    _csrf: LoginSession = Depends(require_csrf),
) -> dict:
    try:
        theme = await generate_ai_theme(body)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"theme": theme}


@router.put("/onboarding", response_model=OnboardingProfile)
def complete_onboarding(
    body: OnboardingComplete,
    owner: Owner = Depends(current_owner),
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    existing = {
        item.name.casefold(): item
        for item in db.scalars(select(Domain).order_by(Domain.position, Domain.id))
    }
    for position, name in enumerate(body.selected_domains):
        if name.casefold() in existing:
            continue
        domain = Domain(
            name=name,
            description=f"Interest selected during initial setup: {name}",
            color=body.theme.accent if position == 0 else body.theme.secondary,
            position=position,
        )
        db.add(domain)
        existing[name.casefold()] = domain

    owner.onboarding_completed = True
    owner.selected_domains = body.selected_domains
    owner.primary_domain = body.primary_domain
    owner.theme = body.theme.model_dump()
    owner.ai_personalized = body.ai_personalized
    owner.ai_provider = body.ai_provider if body.ai_personalized else None
    db.add(owner)
    db.commit()
    db.refresh(owner)
    return onboarding_payload(owner)


def state_condition(owner_id: int, view: str):
    if view == "unread":
        return and_(
            or_(ReadingState.id.is_(None), ReadingState.read.is_(False)),
            or_(ReadingState.id.is_(None), ReadingState.archived.is_(False)),
        )
    if view == "starred":
        return ReadingState.starred.is_(True)
    if view == "later":
        return ReadingState.later.is_(True)
    if view == "archived":
        return ReadingState.archived.is_(True)
    return or_(ReadingState.id.is_(None), ReadingState.archived.is_(False))


def get_or_create_state(db: Session, owner_id: int, entry_id: int) -> ReadingState:
    row = db.scalar(
        select(ReadingState).where(ReadingState.owner_id == owner_id, ReadingState.entry_id == entry_id)
    )
    if row is None:
        row = ReadingState(owner_id=owner_id, entry_id=entry_id)
        db.add(row)
        db.flush()
    return row


def serialize_state(row: ReadingState | None) -> dict:
    return {
        "read": bool(row.read) if row else False,
        "starred": bool(row.starred) if row else False,
        "later": bool(row.later) if row else False,
        "archived": bool(row.archived) if row else False,
    }


def serialize_domain(db: Session, domain: Domain) -> dict:
    feed_count = int(
        db.scalar(
            select(func.count()).select_from(FeedDomain).where(FeedDomain.domain_id == domain.id)
        )
        or 0
    )
    direct_entries = select(EntryDomain.entry_id).where(EntryDomain.domain_id == domain.id)
    inherited_entries = (
        select(EntryFeed.entry_id)
        .join(FeedDomain, FeedDomain.feed_id == EntryFeed.feed_id)
        .where(FeedDomain.domain_id == domain.id)
    )
    entry_count = int(
        db.scalar(
            select(func.count(func.distinct(Entry.id))).where(
                or_(Entry.id.in_(direct_entries), Entry.id.in_(inherited_entries))
            )
        )
        or 0
    )
    return {
        "id": domain.id,
        "name": domain.name,
        "description": domain.description,
        "color": domain.color,
        "position": domain.position,
        "feed_count": feed_count,
        "entry_count": entry_count,
    }


def entry_domains(db: Session, entry_id: int) -> list[Domain]:
    direct_ids = select(EntryDomain.domain_id).where(EntryDomain.entry_id == entry_id)
    inherited_ids = (
        select(FeedDomain.domain_id)
        .join(EntryFeed, EntryFeed.feed_id == FeedDomain.feed_id)
        .where(EntryFeed.entry_id == entry_id)
    )
    return list(
        db.scalars(
            select(Domain)
            .where(or_(Domain.id.in_(direct_ids), Domain.id.in_(inherited_ids)))
            .order_by(Domain.position, Domain.name)
        )
    )


def serialize_entry(db: Session, entry: Entry, owner_id: int) -> dict:
    target = translation_target(db)
    translation = get_translation_record(db, entry.id, target)
    state = db.scalar(
        select(ReadingState).where(ReadingState.owner_id == owner_id, ReadingState.entry_id == entry.id)
    )
    feeds = list(
        db.scalars(
            select(Feed)
            .join(EntryFeed, EntryFeed.feed_id == Feed.id)
            .where(EntryFeed.entry_id == entry.id)
            .order_by(Feed.title)
        )
    )
    tags = list(
        db.scalars(
            select(Tag)
            .join(EntryTag, EntryTag.tag_id == Tag.id)
            .where(EntryTag.entry_id == entry.id)
            .order_by(Tag.name)
        )
    )
    translated = (
        translation
        if (
            translation
            and translation.status == "complete"
            and translation.source_hash == entry.source_hash
        )
        else None
    )
    domains = entry_domains(db, entry.id)
    return {
        "id": entry.id,
        "work_id": entry.work_id,
        "title": entry.title,
        "translated_title": translated.title if translated else None,
        "summary": entry.summary,
        "translated_summary": translated.summary if translated else None,
        "content": entry.content,
        "url": entry.url,
        "canonical_url": entry.work.canonical_url if entry.work else entry.url,
        "authors": entry.authors or [],
        "categories": entry.categories or [],
        "arxiv_id": entry.arxiv_id,
        "arxiv_version": entry.arxiv_version,
        "doi": entry.doi,
        "announce_type": entry.announce_type,
        "published_at": as_utc(entry.published_at),
        "updated_at": as_utc(entry.updated_at),
        "feed_titles": [feed.title for feed in feeds],
        "feed_ids": [feed.id for feed in feeds],
        "state": serialize_state(state),
        "tags": [{"id": tag.id, "name": tag.name, "color": tag.color} for tag in tags],
        "translation_status": translation.status if translation else None,
        "translation_error": translation.last_error if translation and translation.status == "failed" else None,
        "translation_language": translation.language if translation else target,
        "domains": [serialize_domain(db, domain) for domain in domains],
    }


@router.post("/entries/bulk-state", status_code=204)
def bulk_entry_state(
    body: BulkState,
    owner: Owner = Depends(current_owner),
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    valid_ids = set(db.scalars(select(Entry.id).where(Entry.id.in_(body.entry_ids))))
    if len(valid_ids) != len(set(body.entry_ids)):
        raise HTTPException(status_code=404, detail="One or more entries do not exist")
    values = body.state.model_dump(exclude_none=True)
    for entry_id in valid_ids:
        row = get_or_create_state(db, owner.id, entry_id)
        for key, value in values.items():
            setattr(row, key, value)
    db.commit()


def filtered_entries_query(
    db: Session,
    owner_id: int,
    view: Literal["all", "unread", "starred", "later", "archived"] = "all",
    feed_id: int | None = None,
    folder: str | None = None,
    tag_id: int | None = None,
    domain_ids: list[int] | None = None,
    domain_match: Literal["any", "all"] = "any",
    q: str | None = None,
):
    query = (
        select(Entry)
        .outerjoin(
            ReadingState,
            and_(ReadingState.entry_id == Entry.id, ReadingState.owner_id == owner_id),
        )
        .where(state_condition(owner_id, view))
    )
    if feed_id is not None or folder is not None:
        query = query.join(EntryFeed, EntryFeed.entry_id == Entry.id).join(Feed, Feed.id == EntryFeed.feed_id)
        if feed_id is not None:
            query = query.where(Feed.id == feed_id)
        if folder is not None:
            query = query.where(
                Feed.folder.is_(None) if folder == "__uncategorized__" else Feed.folder == folder
            )
    if tag_id is not None:
        query = query.join(EntryTag, EntryTag.entry_id == Entry.id).where(EntryTag.tag_id == tag_id)
    if domain_ids:
        conditions = []
        for domain_id in domain_ids:
            direct = select(EntryDomain.entry_id).where(EntryDomain.domain_id == domain_id)
            inherited = (
                select(EntryFeed.entry_id)
                .join(FeedDomain, FeedDomain.feed_id == EntryFeed.feed_id)
                .where(FeedDomain.domain_id == domain_id)
            )
            conditions.append(or_(Entry.id.in_(direct), Entry.id.in_(inherited)))
        query = query.where(
            and_(*conditions) if domain_match == "all" else or_(*conditions)
        )
    if q and q.strip():
        term = q.strip()
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.outerjoin(
            Translation,
            and_(
                Translation.entry_id == Entry.id,
                Translation.status == "complete",
                Translation.source_hash == Entry.source_hash,
            ),
        )
        like_condition = or_(
            Entry.title.ilike(pattern, escape="\\"),
            Entry.summary.ilike(pattern, escape="\\"),
            cast(Entry.authors, String).ilike(pattern, escape="\\"),
            cast(Entry.categories, String).ilike(pattern, escape="\\"),
            Entry.arxiv_id.ilike(pattern, escape="\\"),
            Entry.doi.ilike(pattern, escape="\\"),
            Translation.title.ilike(pattern, escape="\\"),
            Translation.summary.ilike(pattern, escape="\\"),
            Entry.id.in_(
                select(EntryTag.entry_id)
                .join(Tag, Tag.id == EntryTag.tag_id)
                .where(Tag.name.ilike(pattern, escape="\\"))
            ),
            Entry.id.in_(
                select(EntryDomain.entry_id)
                .join(Domain, Domain.id == EntryDomain.domain_id)
                .where(Domain.name.ilike(pattern, escape="\\"))
            ),
        )
        tokens = [token for token in re.split(r"\s+", term) if token]
        fts = " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        if db.bind and db.bind.dialect.name == "sqlite" and fts:
            like_condition = or_(
                like_condition,
                text("entries.id IN (SELECT rowid FROM entries_fts WHERE entries_fts MATCH :fts)").bindparams(fts=fts),
            )
        query = query.where(like_condition)
    return query


@router.post("/entries/mark-all-read")
def mark_all_entries_read(
    view: Literal["all", "unread", "starred", "later", "archived"] = "all",
    feed_id: int | None = None,
    folder: str | None = None,
    tag_id: int | None = None,
    domain_ids: Annotated[list[int] | None, Query()] = None,
    domain_match: Literal["any", "all"] = "any",
    q: str | None = Query(default=None, max_length=500),
    owner: Owner = Depends(current_owner),
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    query = filtered_entries_query(
        db, owner.id, view, feed_id, folder, tag_id, domain_ids, domain_match, q
    )
    updated = 0
    for entry in db.scalars(query.distinct()):
        row = get_or_create_state(db, owner.id, entry.id)
        if not row.read:
            row.read = True
            updated += 1
    db.commit()
    return {"updated": updated}


@router.get("/entries", response_model=EntriesPage)
def list_entries(
    view: Literal["all", "unread", "starred", "later", "archived"] = "all",
    feed_id: int | None = None,
    folder: str | None = None,
    tag_id: int | None = None,
    domain_ids: Annotated[list[int] | None, Query()] = None,
    domain_match: Literal["any", "all"] = "any",
    q: str | None = Query(default=None, max_length=500),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict:
    query = filtered_entries_query(
        db, owner.id, view, feed_id, folder, tag_id, domain_ids, domain_match, q
    )
    count_query = select(func.count()).select_from(query.distinct().subquery())
    total = int(db.scalar(count_query) or 0)
    entries = list(
        db.scalars(
            query.distinct()
            .order_by(func.coalesce(Entry.published_at, Entry.created_at).desc(), Entry.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    )
    return {
        "items": [serialize_entry(db, entry, owner.id) for entry in entries],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/entries/{entry_id}", response_model=EntryOut)
def get_entry(
    entry_id: int,
    owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict:
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise not_found("Entry")
    return serialize_entry(db, entry, owner.id)


@router.patch("/entries/{entry_id}/state", response_model=EntryOut)
def update_entry_state(
    entry_id: int,
    body: StatePatch,
    owner: Owner = Depends(current_owner),
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise not_found("Entry")
    row = get_or_create_state(db, owner.id, entry_id)
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(row, key, value)
    db.commit()
    return serialize_entry(db, entry, owner.id)


@router.post("/entries/{entry_id}/tags/{tag_id}", status_code=204)
def add_entry_tag(
    entry_id: int,
    tag_id: int,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    if db.get(Entry, entry_id) is None:
        raise not_found("Entry")
    if db.get(Tag, tag_id) is None:
        raise not_found("Tag")
    if db.scalar(select(EntryTag).where(EntryTag.entry_id == entry_id, EntryTag.tag_id == tag_id)) is None:
        db.add(EntryTag(entry_id=entry_id, tag_id=tag_id))
        db.commit()


@router.delete("/entries/{entry_id}/tags/{tag_id}", status_code=204)
def remove_entry_tag(
    entry_id: int,
    tag_id: int,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    db.execute(delete(EntryTag).where(EntryTag.entry_id == entry_id, EntryTag.tag_id == tag_id))
    db.commit()


@router.put("/entries/{entry_id}/domains", response_model=EntryOut)
def replace_entry_domains(
    entry_id: int,
    body: EntryDomainsPatch,
    owner: Owner = Depends(current_owner),
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise not_found("Entry")
    valid = set(db.scalars(select(Domain.id).where(Domain.id.in_(body.domain_ids))))
    if valid != set(body.domain_ids):
        raise HTTPException(status_code=404, detail="One or more domains do not exist")
    db.execute(delete(EntryDomain).where(EntryDomain.entry_id == entry_id))
    for domain_id in sorted(valid):
        db.add(EntryDomain(entry_id=entry_id, domain_id=domain_id))
    db.commit()
    return serialize_entry(db, entry, owner.id)


def feed_status(feed: Feed) -> str:
    if not feed.enabled:
        return "paused"
    if feed.error_count:
        return "error"
    return "healthy"


def serialize_feed(db: Session, feed: Feed, owner_id: int) -> dict:
    entry_count = int(
        db.scalar(select(func.count()).select_from(EntryFeed).where(EntryFeed.feed_id == feed.id)) or 0
    )
    unread_count = int(
        db.scalar(
            select(func.count())
            .select_from(EntryFeed)
            .outerjoin(
                ReadingState,
                and_(ReadingState.entry_id == EntryFeed.entry_id, ReadingState.owner_id == owner_id),
            )
            .where(
                EntryFeed.feed_id == feed.id,
                or_(ReadingState.id.is_(None), ReadingState.read.is_(False)),
                or_(ReadingState.id.is_(None), ReadingState.archived.is_(False)),
            )
        )
        or 0
    )
    domains = list(
        db.scalars(
            select(Domain)
            .join(FeedDomain, FeedDomain.domain_id == Domain.id)
            .where(FeedDomain.feed_id == feed.id)
            .order_by(Domain.position, Domain.name)
        )
    )
    return {
        "id": feed.id,
        "source_key": feed.source_key,
        "title": feed.title,
        "url": feed.url,
        "site_url": feed.site_url,
        "folder": feed.folder,
        "position": feed.position,
        "enabled": feed.enabled,
        "poll_interval_minutes": feed.poll_interval_minutes,
        "last_checked_at": as_utc(feed.last_checked_at),
        "last_fetched_at": as_utc(feed.last_success_at),
        "next_fetch_at": as_utc(feed.next_fetch_at),
        "error_count": feed.error_count,
        "last_error": feed.last_error,
        "status": feed_status(feed),
        "unread_count": unread_count,
        "entry_count": entry_count,
        "domains": [serialize_domain(db, domain) for domain in domains],
    }


def serialize_folder(db: Session, folder: Folder) -> dict:
    return {
        "id": folder.id,
        "name": folder.name,
        "position": folder.position,
        "sort_mode": folder.sort_mode,
        "sort_direction": folder.sort_direction,
        "feed_count": int(
            db.scalar(select(func.count()).select_from(Feed).where(Feed.folder == folder.name)) or 0
        ),
    }


def ensure_folder(db: Session, name: str | None) -> None:
    if not name or db.scalar(select(Folder.id).where(Folder.name == name)) is not None:
        return
    highest_position = db.scalar(select(func.max(Folder.position)))
    next_position = (int(highest_position) if highest_position is not None else -1) + 1
    db.add(Folder(name=name, position=next_position))


def next_feed_position(db: Session, folder: str | None) -> int:
    condition = Feed.folder.is_(None) if folder is None else Feed.folder == folder
    highest_position = db.scalar(select(func.max(Feed.position)).where(condition))
    return (int(highest_position) if highest_position is not None else -1) + 1


def replace_feed_domains(db: Session, feed: Feed, domain_ids: list[int]) -> None:
    valid = set(db.scalars(select(Domain.id).where(Domain.id.in_(domain_ids))))
    if valid != set(domain_ids):
        raise HTTPException(status_code=404, detail="One or more domains do not exist")
    db.execute(delete(FeedDomain).where(FeedDomain.feed_id == feed.id))
    for domain_id in sorted(valid):
        db.add(FeedDomain(feed_id=feed.id, domain_id=domain_id))


def source_sort_settings(db: Session) -> dict[str, str]:
    mode_row = db.get(AppSetting, "source_sort_mode")
    direction_row = db.get(AppSetting, "source_sort_direction")
    mode = mode_row.value if mode_row and mode_row.value in {"alpha", "updated", "manual"} else "alpha"
    direction = (
        direction_row.value
        if direction_row and direction_row.value in {"asc", "desc"}
        else "asc"
    )
    return {"sort_mode": mode, "sort_direction": direction}


@router.post("/feeds/discover", response_model=FeedDiscoveryOut)
def discover_feed(
    body: DiscoverBody,
    _owner: Owner = Depends(current_owner),
    _csrf: LoginSession = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        return {"items": discover_feeds(str(body.url), settings)}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Feed discovery failed: {exc}") from exc


@router.get(
    "/feeds/opml",
    response_class=Response,
    responses={
        200: {
            "description": "OPML 2.0 subscription export",
            "content": {"text/x-opml": {"schema": {"type": "string"}}},
        }
    },
)
def export_opml(
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> Response:
    return Response(
        export_opml_document(db),
        media_type="text/x-opml",
        headers={"Content-Disposition": 'attachment; filename="affogato-rss-reader-subscriptions.opml"'},
    )


@router.post("/feeds/opml", response_model=OpmlImportOut)
async def import_opml(
    file: UploadFile = File(...),
    owner: Owner = Depends(current_owner),
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    raw = await file.read(2_000_001)
    if len(raw) > 2_000_000:
        raise HTTPException(status_code=413, detail="OPML file is too large")
    try:
        return import_opml_document(db, raw, settings)
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=f"Invalid OPML feed metadata: {exc}") from exc


@router.get("/feeds/sort-settings", response_model=SourceSortSettings)
def get_source_sort_settings(
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    return source_sort_settings(db)


@router.put("/feeds/sort-settings", response_model=SourceSortSettings)
def update_source_sort_settings(
    body: SourceSortSettings,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    for key, value in (
        ("source_sort_mode", body.sort_mode),
        ("source_sort_direction", body.sort_direction),
    ):
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value))
        else:
            row.value = value
    db.commit()
    return source_sort_settings(db)


@router.get("/feeds", response_model=FeedListOut)
def list_feeds(
    owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict:
    feeds = list(db.scalars(select(Feed).order_by(Feed.folder, Feed.position, Feed.title)))
    return {"items": [serialize_feed(db, feed, owner.id) for feed in feeds], "total": len(feeds)}


@router.post("/feeds", status_code=201, response_model=FeedOut)
def create_feed(
    body: FeedCreate,
    owner: Owner = Depends(current_owner),
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    url = str(body.url)
    if db.scalar(select(Feed.id).where(Feed.url == url)) is not None:
        raise HTTPException(status_code=409, detail="Feed URL is already subscribed")
    folder_name = body.folder.strip() or None if body.folder else None
    ensure_folder(db, folder_name)
    feed = Feed(
        title=(body.title or "").strip() or url,
        url=url,
        site_url=str(body.site_url) if body.site_url else None,
        folder=folder_name,
        position=next_feed_position(db, folder_name),
        enabled=body.enabled,
        poll_interval_minutes=body.poll_interval_minutes,
    )
    db.add(feed)
    try:
        db.flush()
        replace_feed_domains(db, feed, body.domain_ids)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Feed URL is already subscribed") from exc
    db.refresh(feed)
    return serialize_feed(db, feed, owner.id)


@router.post("/feeds/{feed_id}/refresh", status_code=204)
def refresh_feed(
    feed_id: int,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    feed = db.get(Feed, feed_id)
    if feed is None:
        raise not_found("Feed")
    sync_feed(db, feed, settings)


@router.patch("/feeds/{feed_id}", response_model=FeedOut)
def update_feed(
    feed_id: int,
    body: FeedUpdate,
    owner: Owner = Depends(current_owner),
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    feed = db.get(Feed, feed_id)
    if feed is None:
        raise not_found("Feed")
    changes = body.model_dump(exclude_unset=True)
    domain_ids = changes.pop("domain_ids", None)
    original_folder = feed.folder
    new_url = str(changes["url"]) if "url" in changes else None
    if new_url is not None and new_url != feed.url:
        feed.etag = None
        feed.last_modified = None
        feed.next_fetch_at = None
        feed.error_count = 0
        feed.last_error = None
    for key, value in changes.items():
        if key in {"url", "site_url"} and value is not None:
            value = str(value)
        if key == "title":
            value = value.strip()
        if key == "folder" and value is not None:
            value = value.strip() or None
        setattr(feed, key, value)
    if feed.folder != original_folder:
        with db.no_autoflush:
            feed.position = next_feed_position(db, feed.folder)
    try:
        ensure_folder(db, feed.folder)
        if domain_ids is not None:
            replace_feed_domains(db, feed, domain_ids)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Feed URL is already subscribed") from exc
    return serialize_feed(db, feed, owner.id)


@router.post("/feeds/associate-domains")
def associate_feed_domains(
    body: FeedDomainAssociation,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    feed_ids = set(body.feed_ids)
    domain_ids = set(body.domain_ids)
    valid_feed_ids = set(db.scalars(select(Feed.id).where(Feed.id.in_(feed_ids))))
    if valid_feed_ids != feed_ids:
        raise HTTPException(status_code=404, detail="One or more feeds do not exist")
    valid_domain_ids = set(db.scalars(select(Domain.id).where(Domain.id.in_(domain_ids))))
    if valid_domain_ids != domain_ids:
        raise HTTPException(status_code=404, detail="One or more domains do not exist")

    existing = {
        (feed_id, domain_id)
        for feed_id, domain_id in db.execute(
            select(FeedDomain.feed_id, FeedDomain.domain_id).where(
                FeedDomain.feed_id.in_(feed_ids),
                FeedDomain.domain_id.in_(domain_ids),
            )
        )
    }
    missing = [
        (feed_id, domain_id)
        for feed_id in sorted(feed_ids)
        for domain_id in sorted(domain_ids)
        if (feed_id, domain_id) not in existing
    ]
    for feed_id, domain_id in missing:
        db.add(FeedDomain(feed_id=feed_id, domain_id=domain_id))
    db.commit()
    return {"feeds_updated": len(feed_ids), "associations_added": len(missing)}


@router.put("/feeds/reorder", status_code=204)
def reorder_feeds(
    body: FeedReorder,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    condition = Feed.folder.is_(None) if body.folder is None else Feed.folder == body.folder
    current_ids = list(db.scalars(select(Feed.id).where(condition)))
    if len(current_ids) != len(body.feed_ids) or set(current_ids) != set(body.feed_ids):
        raise HTTPException(
            status_code=422,
            detail="feed_ids must contain every feed in the selected folder exactly once",
        )
    feeds_by_id = {
        feed.id: feed
        for feed in db.scalars(select(Feed).where(Feed.id.in_(body.feed_ids)))
    }
    for position, feed_id in enumerate(body.feed_ids):
        feeds_by_id[feed_id].position = position
    db.commit()


@router.delete("/feeds/{feed_id}", status_code=204)
def delete_feed(
    feed_id: int,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    feed = db.get(Feed, feed_id)
    if feed is None:
        raise not_found("Feed")
    db.delete(feed)
    db.commit()


@router.get("/folders", response_model=FolderListOut)
def list_folders(
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict:
    rows = list(db.scalars(select(Folder).order_by(Folder.position, Folder.name)))
    return {"items": [serialize_folder(db, row) for row in rows]}


@router.post("/folders", status_code=201, response_model=FolderOut)
def create_folder(
    body: FolderCreate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    folder = Folder(name=body.name, position=body.position)
    db.add(folder)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Folder name already exists") from exc
    db.refresh(folder)
    return serialize_folder(db, folder)


@router.patch("/folders/{folder_id}", response_model=FolderOut)
def update_folder(
    folder_id: int,
    body: FolderUpdate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    folder = db.get(Folder, folder_id)
    if folder is None:
        raise not_found("Folder")
    changes = body.model_dump(exclude_unset=True)
    original_name = folder.name
    for key, value in changes.items():
        setattr(folder, key, value)
    try:
        if folder.name != original_name:
            db.execute(update(Feed).where(Feed.folder == original_name).values(folder=folder.name))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Folder name already exists") from exc
    return serialize_folder(db, folder)


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(
    folder_id: int,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    folder = db.get(Folder, folder_id)
    if folder is None:
        raise not_found("Folder")
    db.execute(update(Feed).where(Feed.folder == folder.name).values(folder=None))
    db.delete(folder)
    db.commit()


@router.get("/domains", response_model=DomainListOut)
def list_domains(
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict:
    rows = list(db.scalars(select(Domain).order_by(Domain.position, Domain.name)))
    return {"items": [serialize_domain(db, row) for row in rows]}


@router.post("/domains", status_code=201, response_model=DomainOut)
def create_domain(
    body: DomainCreate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    name = body.name.strip()
    if db.scalar(select(Domain.id).where(func.lower(Domain.name) == name.lower())):
        raise HTTPException(status_code=409, detail="Domain already exists")
    domain = Domain(
        name=name,
        description=body.description.strip(),
        color=body.color,
        position=body.position,
    )
    db.add(domain)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Domain already exists") from exc
    db.refresh(domain)
    return serialize_domain(db, domain)


@router.patch("/domains/{domain_id}", response_model=DomainOut)
def update_domain(
    domain_id: int,
    body: DomainUpdate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    domain = db.get(Domain, domain_id)
    if domain is None:
        raise not_found("Domain")
    for key, value in body.model_dump(exclude_unset=True).items():
        if key in {"name", "description"} and value is not None:
            value = value.strip()
        setattr(domain, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Domain already exists") from exc
    return serialize_domain(db, domain)


@router.delete("/domains/{domain_id}", status_code=204)
def delete_domain(
    domain_id: int,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    domain = db.get(Domain, domain_id)
    if domain is None:
        raise not_found("Domain")
    db.delete(domain)
    db.commit()


@router.get("/tags", response_model=TagListOut)
def list_tags(
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.execute(
        select(Tag, func.count(EntryTag.id))
        .outerjoin(EntryTag, EntryTag.tag_id == Tag.id)
        .group_by(Tag.id)
        .order_by(Tag.name)
    ).all()
    return {
        "items": [
            {"id": tag.id, "name": tag.name, "color": tag.color, "entry_count": int(count)}
            for tag, count in rows
        ]
    }


@router.post("/tags", status_code=201, response_model=TagWithCountOut)
def create_tag(
    body: TagCreate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Tag name cannot be blank")
    if db.scalar(select(Tag.id).where(func.lower(Tag.name) == name.lower())) is not None:
        raise HTTPException(status_code=409, detail="Tag already exists")
    tag = Tag(name=name, color=body.color)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return {"id": tag.id, "name": tag.name, "color": tag.color, "entry_count": 0}


@router.patch("/tags/{tag_id}", response_model=TagOut)
def update_tag(
    tag_id: int,
    body: TagCreate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise not_found("Tag")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Tag name cannot be blank")
    tag.name = name
    tag.color = body.color
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tag already exists") from exc
    return {"id": tag.id, "name": tag.name, "color": tag.color}


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(
    tag_id: int,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise not_found("Tag")
    db.delete(tag)
    db.commit()


@router.get("/translations/status", response_model=TranslationStatusOut)
def get_translation_status(
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict:
    data = translation_status(db)
    counts = data.pop("counts")
    return {
        **data,
        "provider_healthy": data.pop("healthy"),
        "pending_count": counts["pending"],
        "running_count": counts["running"],
        "failed_count": counts["failed"],
        "completed_count": counts["complete"],
    }


@router.patch("/translations/status", response_model=TranslationStatusOut)
def toggle_translation(
    body: TranslationToggle,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        configure_translation(
            db,
            enabled=body.enabled,
            target=body.target_language,
            provider=body.provider,
            fallback_mode=body.fallback_mode,
            llm_connection_id=body.llm_connection_id,
            deepl_endpoint=str(body.deepl_endpoint) if body.deepl_endpoint else None,
            deepl_api_key=body.deepl_api_key,
            clear_deepl_api_key=body.clear_deepl_api_key,
            google_cloud_api_key=body.google_cloud_api_key,
            clear_google_cloud_api_key=body.clear_google_cloud_api_key,
            settings=settings,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_translation_status(_owner=db.get(Owner, 1), db=db)  # type: ignore[arg-type]


@router.post("/translations/test", response_model=TranslationTestOut)
def test_translation(
    body: TranslationTest,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        provider = build_selected_translation_provider(
            db,
            settings,
            provider=body.provider,
            llm_connection_id=body.llm_connection_id,
            deepl_endpoint=str(body.deepl_endpoint) if body.deepl_endpoint else None,
            deepl_api_key=body.deepl_api_key,
            google_cloud_api_key=body.google_cloud_api_key,
        )
        assert provider is not None
        started_at = perf_counter()
        translated = translate_with_log(
            provider,
            body.sample_text,
            body.target_language,
            operation="translation_test",
        )
    except TranslationConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TranslationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "provider": provider.name,
        "translated_text": translated,
        "elapsed_ms": max(0, round((perf_counter() - started_at) * 1000)),
    }


@router.get("/llm/connections", response_model=list[LLMConnectionOut])
def get_llm_connections(
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> list[dict]:
    return list_llm_connections(db)


@router.post("/llm/connections", response_model=LLMConnectionOut, status_code=201)
def create_llm_connection(
    body: LLMConnectionCreate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        connection = save_llm_connection(
            db,
            name=body.name,
            base_url=str(body.base_url),
            model=body.model,
            api_key=body.api_key,
            settings=settings,
        )
        db.commit()
        db.refresh(connection)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="LLM connection name already exists") from exc
    return llm_connection_summary(connection)


@router.post("/llm/connections/test", response_model=LLMConnectionTestOut)
def test_llm_connection(
    body: LLMConnectionTest,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    connection = (
        get_llm_connection(db, body.connection_id) if body.connection_id else None
    )
    if body.connection_id and connection is None:
        raise not_found("LLM connection")
    base_url = str(body.base_url) if body.base_url else (
        connection.base_url if connection else None
    )
    model = body.model or (connection.model if connection else None)
    try:
        api_key = body.api_key or (
            decrypt_llm_api_key(connection, settings) if connection else None
        )
    except SecretKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not base_url or not model or not api_key:
        raise HTTPException(
            status_code=400,
            detail="Base URL, model, and API key are required",
        )
    try:
        started_at = perf_counter()
        response_text = probe_llm_connection(
            base_url=base_url,
            model=model,
            api_key=api_key,
            settings=settings,
            route=http_route_for_llm_connection(db, connection, settings),
            connection_id=connection.id if connection else None,
            connection_name=connection.name if connection else None,
        )
    except LLMConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "model": model,
        "response_text": response_text,
        "elapsed_ms": max(0, round((perf_counter() - started_at) * 1000)),
    }


@router.patch("/llm/connections/{connection_id}", response_model=LLMConnectionOut)
def update_llm_connection(
    connection_id: int,
    body: LLMConnectionUpdate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    current = get_llm_connection(db, connection_id)
    if current is None:
        raise not_found("LLM connection")
    try:
        connection = save_llm_connection(
            db,
            connection_id=connection_id,
            name=body.name or current.name,
            base_url=str(body.base_url) if body.base_url else current.base_url,
            model=body.model or current.model,
            api_key=body.api_key,
            clear_api_key=body.clear_api_key,
            settings=settings,
        )
        db.commit()
        db.refresh(connection)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="LLM connection name already exists") from exc
    return llm_connection_summary(connection)


@router.delete("/llm/connections/{connection_id}", status_code=204)
def remove_llm_connection(
    connection_id: int,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    connection = get_llm_connection(db, connection_id)
    if connection is None:
        raise not_found("LLM connection")
    try:
        delete_llm_connection(db, connection)
        db.commit()
    except LLMConnectionInUseError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/network-proxy", response_model=NetworkProxyOut)
def get_network_proxy(
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict:
    return network_proxy_summary(db)


@router.patch("/network-proxy", response_model=NetworkProxyOut)
def update_network_proxy(
    body: NetworkProxyUpdate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        save_network_proxy_config(
            db,
            enabled=body.enabled,
            url=body.url,
            username=body.username,
            password=body.password,
            clear_password=body.clear_password,
            global_mode=body.global_mode,
            feed_modes=body.feed_modes,
            llm_connection_modes=body.llm_connection_modes,
            translation_service_modes=body.translation_service_modes,
            settings=settings,
        )
        db.commit()
    except (ValueError, SecretKeyError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return network_proxy_summary(db)


@router.post("/network-proxy/test", response_model=NetworkProxyTestOut)
def test_network_proxy(
    body: NetworkProxyTest,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        return test_custom_proxy_targets(
            db,
            url=body.url,
            username=body.username,
            password=body.password,
            use_saved_password=body.use_saved_password,
            settings=settings,
        )
    except (ValueError, SecretKeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/translations/retry", status_code=204)
def retry_translations(
    body: TranslationRetry,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    queue_retry(db, body.entry_ids)


@router.get("/settings", response_model=AppSettingsOut)
def app_settings(
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return {
        "app_name": settings.app_name,
        "version": settings.version,
        "auth_mode": settings.auth_mode,
        "debug": settings.debug,
        "timezone": settings.timezone,
        "translation_enabled": is_translation_enabled(db, settings),
        "translation_target": translation_target(db, settings),
        "available_locales": ["en", "zh-CN"],
    }


@router.get("/updates/status", response_model=UpdateStatusOut)
def get_update_status(
    _owner: Owner = Depends(current_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    return update_status(settings)


@router.post("/updates/check", response_model=UpdateStatusOut)
def check_updates_now(
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return check_for_updates(db, settings)


@router.post("/updates/install", response_model=UpdateStatusOut)
def install_downloaded_update(
    _csrf: LoginSession = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        return request_update_install(settings)
    except UpdateInstallUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (UpdateError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/jobs", response_model=JobListOut)
def list_jobs(
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    jobs = list(db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)))
    return {
        "items": [
            {
                "id": job.id,
                "kind": job.kind,
                "status": job.status,
                "created_at": as_utc(job.created_at),
                "started_at": as_utc(job.started_at),
                "finished_at": as_utc(job.finished_at),
                "message": job.error,
                "payload": job.payload,
                "result": job.result,
            }
            for job in jobs
        ]
    }


@router.get("/call-logs", response_model=CallLogListOut)
def list_call_logs(
    _owner: Owner = Depends(current_owner),
    settings: Settings = Depends(get_settings),
    limit: int = Query(default=200, ge=1, le=1000),
    category: Literal["llm", "translation"] | None = None,
    status: Literal["success", "error"] | None = None,
) -> dict:
    return {
        "items": read_call_logs(
            limit=limit,
            category=category,
            status=status,
            settings=settings,
        ),
        "file_path": str(settings.effective_call_log_file),
        "host_path_hint": "logs/llm-translation.jsonl",
    }


@router.get("/jobs/sync-runs", response_model=SyncRunListOut)
def list_sync_runs(
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    rows = db.execute(
        select(SyncRun, Feed.title)
        .outerjoin(Feed, Feed.id == SyncRun.feed_id)
        .order_by(SyncRun.started_at.desc())
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": f"sync-{run.id}",
                "kind": "feed_sync",
                "status": run.status,
                "started_at": as_utc(run.started_at),
                "finished_at": as_utc(run.finished_at),
                "feed_title": feed_title,
                "inserted_count": run.created_count,
                "updated_count": run.updated_count,
                "error_count": 1 if run.error else 0,
                "message": run.error,
                "http_status": run.http_status,
            }
            for run, feed_title in rows
        ]
    }


def serialize_brief(db: Session, brief: Brief) -> dict:
    return {
        "id": brief.id,
        "schedule_id": brief.schedule_id,
        "period": brief.period,
        "title": brief.title,
        "period_start": as_utc(brief.start_at),
        "period_end": as_utc(brief.end_at),
        "start_at": as_utc(brief.start_at),
        "end_at": as_utc(brief.end_at),
        "notes": brief.notes,
        "stats": brief.stats,
        "filters": brief.filters,
        "item_count": brief_item_count(db, brief.id),
        "created_at": as_utc(brief.created_at),
        "updated_at": as_utc(brief.updated_at),
        "status": "ready",
    }


def serialize_brief_schedule(schedule: BriefSchedule) -> dict:
    return {
        "id": schedule.id,
        "name": schedule.name,
        "period": schedule.period,
        "timezone": schedule.timezone,
        "cutoff_time": schedule.cutoff_time,
        "weekday": schedule.weekday,
        "month_day": schedule.month_day,
        "year_month": schedule.year_month,
        "domain_ids": schedule.domain_ids,
        "feed_ids": schedule.feed_ids,
        "tag_ids": schedule.tag_ids,
        "domain_match": schedule.domain_match,
        "enabled": schedule.enabled,
        "last_run_at": as_utc(schedule.last_run_at),
        "created_at": as_utc(schedule.created_at),
        "updated_at": as_utc(schedule.updated_at),
    }


def serialize_brief_configuration(db: Session) -> dict:
    connection = get_feature_connection(db, BRIEF_FEATURE)
    return {
        "llm_connection_id": connection.id if connection else None,
        "llm_connection_name": connection.name if connection else None,
        "model": connection.model if connection else None,
        "configured": bool(connection and connection.api_key_encrypted),
    }


@router.get("/briefs/configuration", response_model=BriefConfigurationOut)
def get_brief_configuration(
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict:
    return serialize_brief_configuration(db)


@router.patch("/briefs/configuration", response_model=BriefConfigurationOut)
def update_brief_configuration(
    body: BriefConfigurationUpdate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if body.llm_connection_id is None:
        unbind_llm_connection(db, feature_key=BRIEF_FEATURE)
    else:
        connection = get_llm_connection(db, body.llm_connection_id)
        if connection is None:
            raise not_found("LLM connection")
        if not connection.api_key_encrypted:
            raise HTTPException(
                status_code=400,
                detail="The selected LLM connection does not have an API key",
            )
        bind_llm_connection(
            db,
            feature_key=BRIEF_FEATURE,
            connection=connection,
        )
    db.commit()
    return serialize_brief_configuration(db)


@router.get("/briefs/rule", response_model=BriefRuleOut)
def get_brief_rule(
    _owner: Owner = Depends(current_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    content, is_custom = read_brief_rule(settings)
    return {"content": content, "is_custom": is_custom}


@router.patch("/briefs/rule", response_model=BriefRuleOut)
def update_brief_rule(
    body: BriefRuleUpdate,
    _csrf: LoginSession = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        content = save_brief_rule(body.content, settings)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"content": content, "is_custom": True}


@router.delete("/briefs/rule", response_model=BriefRuleOut)
def restore_default_brief_rule(
    _csrf: LoginSession = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        content = reset_brief_rule(settings)
    except OSError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"content": content, "is_custom": False}


@router.get("/briefs", response_model=BriefListOut)
def list_briefs(
    period: Literal["daily", "weekly", "monthly", "yearly"] | None = None,
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict:
    query = select(Brief)
    if period:
        query = query.where(Brief.period == period)
    rows = list(db.scalars(query.order_by(Brief.end_at.desc(), Brief.id.desc())))
    return {"items": [serialize_brief(db, row) for row in rows]}


def _brief_generation_job(db: Session, idempotency_key: str) -> Job | None:
    return db.scalar(
        select(Job)
        .where(
            Job.kind == "brief_generation",
            Job.payload["idempotency_key"].as_string() == idempotency_key,
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )


def _serialize_brief_generation_progress(job: Job) -> dict:
    result = job.result or {}
    progress = result.get("progress") or {}
    request_payload = (job.payload or {}).get("request")
    return {
        "idempotency_key": str(job.payload.get("idempotency_key", "")),
        "status": job.status,
        "stage": str(progress.get("stage", "preparing")),
        "completed": int(progress.get("completed", 0)),
        "total": int(progress.get("total", 1)),
        "brief_id": result.get("brief_id"),
        "message": job.error if job.status == "failed" else progress.get("message"),
        "can_retry": job.status == "failed" and isinstance(request_payload, dict),
        "attempt": max(1, int(result.get("attempt", 1))),
    }


@router.get(
    "/briefs/generation-progress/latest",
    response_model=BriefGenerationProgressOut | None,
)
def get_latest_brief_generation_progress(
    period: Literal["daily", "weekly", "monthly", "yearly"],
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict | None:
    job = db.scalar(
        select(Job)
        .where(
            Job.kind == "brief_generation",
            Job.payload["period"].as_string() == period,
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    if job is None or job.status == "completed":
        return None
    return _serialize_brief_generation_progress(job)


@router.get(
    "/briefs/generation-progress/{idempotency_key}",
    response_model=BriefGenerationProgressOut,
)
def get_brief_generation_progress(
    idempotency_key: str,
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict:
    job = _brief_generation_job(db, idempotency_key)
    if job is None:
        raise not_found("Brief generation")
    return _serialize_brief_generation_progress(job)


def _execute_brief_generation(
    db: Session,
    *,
    job: Job,
    body: BriefCreate,
    settings: Settings,
) -> dict:
    progress_lock = Lock()
    progress_bind = db.get_bind()

    def report_progress(
        stage: str,
        completed: int,
        total: int,
        message: str | None,
    ) -> None:
        with progress_lock:
            with Session(bind=progress_bind) as progress_db:
                current = progress_db.get(Job, job.id)
                if current is None:
                    return
                current.result = {
                    **(current.result or {}),
                    "progress": {
                        "stage": stage,
                        "completed": completed,
                        "total": total,
                        "message": message,
                    },
                }
                progress_db.commit()

    def load_checkpoint(stage: str, prompt_hash: str) -> str | None:
        return db.scalar(
            select(BriefGenerationCheckpoint.content).where(
                BriefGenerationCheckpoint.job_id == job.id,
                BriefGenerationCheckpoint.stage == stage,
                BriefGenerationCheckpoint.prompt_hash == prompt_hash,
            )
        )

    def save_checkpoint(stage: str, prompt_hash: str, content: str) -> None:
        checkpoint = db.scalar(
            select(BriefGenerationCheckpoint).where(
                BriefGenerationCheckpoint.job_id == job.id,
                BriefGenerationCheckpoint.stage == stage,
                BriefGenerationCheckpoint.prompt_hash == prompt_hash,
            )
        )
        if checkpoint is None:
            checkpoint = BriefGenerationCheckpoint(
                job_id=job.id,
                stage=stage,
                prompt_hash=prompt_hash,
                content=content,
            )
            db.add(checkpoint)
        else:
            checkpoint.content = content
        db.commit()

    def fail_job(message: str) -> None:
        db.rollback()
        current = db.get(Job, job.id)
        if current is None:
            return
        current.status = "failed"
        current.error = message[:4000]
        current.finished_at = utcnow()
        db.commit()

    try:
        brief = create_manual_brief(
            db,
            body.period,
            at=body.at,
            start_at=body.start_at,
            end_at=body.end_at,
            filters={
                "domain_ids": body.domain_ids,
                "feed_ids": body.feed_ids,
                "tag_ids": body.tag_ids,
                "domain_match": body.domain_match,
            },
            idempotency_key=body.idempotency_key,
            settings=settings,
            progress_callback=report_progress,
            checkpoint_loader=load_checkpoint,
            checkpoint_saver=save_checkpoint,
        )
    except ValueError as exc:
        fail_job(str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMConnectionError as exc:
        fail_job(str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        fail_job(str(exc))
        raise
    if body.title and brief.title != body.title:
        brief.title = body.title.strip()
        db.commit()
    current = db.get(Job, job.id)
    if current is not None:
        current.status = "completed"
        current.result = {
            **(current.result or {}),
            "progress": {
                "stage": "finalizing",
                "completed": 1,
                "total": 1,
            },
            "brief_id": brief.id,
        }
        current.error = None
        current.finished_at = utcnow()
        db.execute(
            delete(BriefGenerationCheckpoint).where(
                BriefGenerationCheckpoint.job_id == job.id
            )
        )
        db.commit()
    return serialize_brief(db, brief)


@router.post("/briefs", status_code=201, response_model=BriefOut)
def generate_brief(
    body: BriefCreate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    existing_job = _brief_generation_job(db, body.idempotency_key)
    if existing_job is not None:
        brief_id = (existing_job.result or {}).get("brief_id")
        if existing_job.status == "completed" and brief_id:
            brief = db.get(Brief, brief_id)
            if brief is not None:
                return serialize_brief(db, brief)
        if existing_job.status == "failed":
            raise HTTPException(
                status_code=409,
                detail="This brief generation failed; use checkpoint retry.",
            )
        raise HTTPException(
            status_code=409,
            detail="This brief generation is already running.",
        )
    job = Job(
        kind="brief_generation",
        status="running",
        payload={
            "idempotency_key": body.idempotency_key,
            "period": body.period,
            "request": body.model_dump(mode="json"),
        },
        result={
            "attempt": 1,
            "progress": {
                "stage": "preparing",
                "completed": 0,
                "total": 1,
                "message": None,
            },
        },
        started_at=utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _execute_brief_generation(db, job=job, body=body, settings=settings)


@router.post(
    "/briefs/generation-progress/{idempotency_key}/retry",
    response_model=BriefOut,
)
def retry_brief_generation(
    idempotency_key: str,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    job = _brief_generation_job(db, idempotency_key)
    if job is None:
        raise not_found("Brief generation")
    if job.status == "running":
        raise HTTPException(status_code=409, detail="Brief generation is still running.")
    if job.status == "completed":
        brief_id = (job.result or {}).get("brief_id")
        brief = db.get(Brief, brief_id) if brief_id else None
        if brief is None:
            raise not_found("Brief")
        return serialize_brief(db, brief)
    request_payload = (job.payload or {}).get("request")
    if not isinstance(request_payload, dict):
        raise HTTPException(
            status_code=409,
            detail=(
                "This task was created before resumable checkpoints were enabled "
                "and cannot continue from its previous batches."
            ),
        )
    try:
        body = BriefCreate.model_validate(request_payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="The saved brief request is no longer valid.",
        ) from exc
    result = dict(job.result or {})
    result["attempt"] = max(1, int(result.get("attempt", 1))) + 1
    result["progress"] = {
        **(result.get("progress") or {}),
        "message": "Resuming from saved checkpoints",
    }
    job.result = result
    job.status = "running"
    job.error = None
    job.started_at = utcnow()
    job.finished_at = None
    db.commit()
    return _execute_brief_generation(db, job=job, body=body, settings=settings)


@router.get("/briefs/{brief_id}", response_model=BriefDetailOut)
def get_brief(
    brief_id: int,
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict:
    brief = db.get(Brief, brief_id)
    if brief is None:
        raise not_found("Brief")
    payload = serialize_brief(db, brief)
    payload["markdown"] = brief_markdown(db, brief)
    return payload


@router.patch("/briefs/{brief_id}", response_model=BriefOut)
def update_brief(
    brief_id: int,
    body: BriefUpdate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    brief = db.get(Brief, brief_id)
    if brief is None:
        raise not_found("Brief")
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(brief, key, value)
    db.commit()
    return serialize_brief(db, brief)


@router.delete("/briefs/{brief_id}", status_code=204)
def delete_brief(
    brief_id: int,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    brief = db.get(Brief, brief_id)
    if brief is None:
        raise not_found("Brief")
    db.delete(brief)
    db.commit()


@router.get(
    "/briefs/{brief_id}/export",
    response_class=Response,
    responses={
        200: {
            "description": "Brief exported as Markdown",
            "content": {"text/markdown": {"schema": {"type": "string"}}},
        }
    },
)
def export_brief(
    brief_id: int,
    format: Literal["markdown", "md"] = "markdown",
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> Response:
    brief = db.get(Brief, brief_id)
    if brief is None:
        raise not_found("Brief")
    markdown = brief_markdown(db, brief)
    filename = f"brief-{brief.period}-{brief.end_at.date().isoformat()}.md"
    return PlainTextResponse(
        markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/brief-schedules", response_model=BriefScheduleListOut)
def list_brief_schedules(
    _owner: Owner = Depends(current_owner),
    db: Session = Depends(get_db),
) -> dict:
    rows = list(db.scalars(select(BriefSchedule).order_by(BriefSchedule.name)))
    return {"items": [serialize_brief_schedule(row) for row in rows]}


@router.post("/brief-schedules", status_code=201, response_model=BriefScheduleOut)
def create_brief_schedule(
    body: BriefScheduleCreate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        ZoneInfo(body.timezone)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid timezone") from exc
    schedule = BriefSchedule(**body.model_dump())
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return serialize_brief_schedule(schedule)


@router.patch("/brief-schedules/{schedule_id}", response_model=BriefScheduleOut)
def update_brief_schedule(
    schedule_id: int,
    body: BriefScheduleUpdate,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    schedule = db.get(BriefSchedule, schedule_id)
    if schedule is None:
        raise not_found("Brief schedule")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("timezone"):
        try:
            ZoneInfo(changes["timezone"])
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Invalid timezone") from exc
    for key, value in changes.items():
        setattr(schedule, key, value)
    db.commit()
    return serialize_brief_schedule(schedule)


@router.delete("/brief-schedules/{schedule_id}", status_code=204)
def delete_brief_schedule(
    schedule_id: int,
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    schedule = db.get(BriefSchedule, schedule_id)
    if schedule is None:
        raise not_found("Brief schedule")
    db.delete(schedule)
    db.commit()


@router.post("/brief-schedules/run-due", response_model=BriefListOut)
def run_brief_schedules(
    _csrf: LoginSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    rows = run_due_schedules(db)
    return {"items": [serialize_brief(db, row) for row in rows]}
