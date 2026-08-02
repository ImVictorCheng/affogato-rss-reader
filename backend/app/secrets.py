from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .config import Settings, get_settings

ENCRYPTED_PREFIX = "fernet:v1:"


class SecretKeyError(RuntimeError):
    pass


def is_encrypted_secret(value: str | None) -> bool:
    return bool(value and value.startswith(ENCRYPTED_PREFIX))


def secret_hint(value: str) -> str:
    stripped = value.strip()
    return f"****{stripped[-4:]}" if len(stripped) >= 4 else "****"


class SecretCipher:
    """Encrypt small application secrets with a key kept outside the database."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        active_path = self.settings.effective_secret_key_file
        active_key = self._load_or_create(active_path)
        previous_paths = [
            Path(item.strip())
            for item in self.settings.secret_key_previous_files.split(",")
            if item.strip()
        ]
        previous_keys = [self._load(path) for path in previous_paths]
        try:
            self._fernet = MultiFernet(
                [Fernet(active_key), *(Fernet(key) for key in previous_keys)]
            )
        except (TypeError, ValueError) as exc:
            raise SecretKeyError("The configured secret key is invalid") from exc

    @staticmethod
    def _load(path: Path) -> bytes:
        try:
            value = path.read_bytes().strip()
        except OSError as exc:
            raise SecretKeyError(f"Unable to read secret key file: {path}") from exc
        if not value:
            raise SecretKeyError(f"Secret key file is empty: {path}")
        return value

    def _load_or_create(self, path: Path) -> bytes:
        if path.exists():
            return self._load(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(key + b"\n")
            try:
                path.chmod(0o600)
            except OSError:
                # Windows ACLs do not map directly to POSIX modes.
                pass
            return key
        except FileExistsError:
            return self._load(path)
        except OSError as exc:
            raise SecretKeyError(f"Unable to create secret key file: {path}") from exc

    def encrypt(self, value: str, *, context: str) -> str:
        payload = json.dumps(
            {"context": context, "value": value},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return ENCRYPTED_PREFIX + self._fernet.encrypt(payload).decode("ascii")

    def decrypt(self, token: str, *, context: str) -> str:
        if not is_encrypted_secret(token):
            raise SecretKeyError("Refusing to decrypt an unencrypted secret")
        try:
            payload = self._fernet.decrypt(
                token[len(ENCRYPTED_PREFIX) :].encode("ascii")
            )
            decoded = json.loads(payload)
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecretKeyError(
                "Unable to decrypt a stored secret; restore the matching master key"
            ) from exc
        if decoded.get("context") != context or not isinstance(decoded.get("value"), str):
            raise SecretKeyError("Stored secret context does not match")
        return decoded["value"]

    def rotate(self, token: str, *, context: str) -> str:
        return self.encrypt(self.decrypt(token, context=context), context=context)
