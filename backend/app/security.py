from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import timedelta
from threading import Lock

from fastapi import Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .models import Owner, Session as LoginSession, utcnow

COOKIE_NAME = "affogato_rss_reader_session"
_NOAUTH_CSRF_TOKEN = secrets.token_urlsafe(32)
_login_attempt_lock = Lock()
_login_attempts: dict[str, list[float]] = {}
_LOGIN_WINDOW_SECONDS = 5 * 60
_LOGIN_MAX_FAILURES = 8


def enforce_login_rate_limit(client_key: str) -> None:
    now = time.monotonic()
    with _login_attempt_lock:
        recent = [
            timestamp
            for timestamp in _login_attempts.get(client_key, [])
            if now - timestamp < _LOGIN_WINDOW_SECONDS
        ]
        _login_attempts[client_key] = recent
        if len(recent) >= _LOGIN_MAX_FAILURES:
            retry_after = max(1, int(_LOGIN_WINDOW_SECONDS - (now - recent[0])))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts; try again later",
                headers={"Retry-After": str(retry_after)},
            )


def record_login_failure(client_key: str) -> None:
    with _login_attempt_lock:
        _login_attempts.setdefault(client_key, []).append(time.monotonic())


def clear_login_failures(client_key: str) -> None:
    with _login_attempt_lock:
        _login_attempts.pop(client_key, None)


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(derived).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, expected_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(expected_text)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_login_session(db: Session, owner: Owner, response: Response, settings: Settings) -> LoginSession:
    raw_token = secrets.token_urlsafe(48)
    login = LoginSession(
        owner_id=owner.id,
        token_hash=token_hash(raw_token),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=utcnow() + timedelta(days=settings.session_days),
    )
    db.add(login)
    db.commit()
    db.refresh(login)
    response.set_cookie(
        COOKIE_NAME,
        raw_token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return login


def delete_login_session(db: Session, raw_token: str | None, response: Response) -> None:
    if raw_token:
        db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(raw_token)))
        db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


def current_session(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    raw_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> LoginSession:
    if settings.auth_mode == "none":
        owner = db.get(Owner, 1)
        if owner is None:
            owner = Owner(id=1, username="owner", password_hash=None)
            db.add(owner)
            db.commit()
        return LoginSession(
            owner_id=owner.id,
            token_hash="no-auth",
            csrf_token=_NOAUTH_CSRF_TOKEN,
            expires_at=utcnow() + timedelta(days=3650),
        )
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    login = db.scalar(select(LoginSession).where(LoginSession.token_hash == token_hash(raw_token)))
    if login is None or login.expires_at <= utcnow():
        if login is not None:
            db.delete(login)
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    login.last_seen_at = utcnow()
    db.commit()
    return login


def current_owner(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    raw_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> Owner:
    if settings.auth_mode == "none":
        owner = db.get(Owner, 1)
        if owner is None:
            owner = Owner(id=1, username="owner", password_hash=None)
            db.add(owner)
            db.commit()
        return owner
    login = current_session(db=db, settings=settings, raw_token=raw_token)
    owner = db.get(Owner, login.owner_id)
    if owner is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Owner not found")
    return owner


def require_csrf(
    request: Request,
    login: LoginSession = Depends(current_session),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> LoginSession:
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Untrusted request origin")
        if not csrf_token or not hmac.compare_digest(csrf_token, login.csrf_token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
    return login


def noauth_csrf_token() -> str:
    return _NOAUTH_CSRF_TOKEN


def purge_expired_sessions(db: Session) -> None:
    db.execute(delete(LoginSession).where(LoginSession.expires_at <= utcnow()))
    db.commit()
