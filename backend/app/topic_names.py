from __future__ import annotations

import unicodedata


TOPIC_NORMALIZED_NAME_MAX_LENGTH = 240


def normalize_topic_name(value: str) -> str:
    """Return the stable NFKC/casefold comparison key used by topic namespaces."""

    text = unicodedata.normalize("NFKC", value).casefold().strip()
    text = "".join(character if character.isalnum() else " " for character in text)
    return " ".join(text.split())


def validate_normalized_topic_name(
    value: str,
    *,
    field_name: str = "Topic name",
    allow_empty: bool = False,
) -> str:
    """Validate a value before storing its normalized form in VARCHAR(240)."""

    normalized = normalize_topic_name(value)
    if not normalized:
        if allow_empty:
            return normalized
        raise ValueError(f"{field_name} cannot be blank")
    if len(normalized) > TOPIC_NORMALIZED_NAME_MAX_LENGTH:
        raise ValueError(
            f"{field_name} is too long after Unicode normalization "
            f"(maximum {TOPIC_NORMALIZED_NAME_MAX_LENGTH} characters)"
        )
    return normalized
