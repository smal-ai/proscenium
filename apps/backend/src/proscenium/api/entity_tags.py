"""RFC 9110 entity-tag comparison for conditional reads."""

import re
from collections.abc import Sequence

_TAG = r'(?:W/)?"[\x21\x23-\x7e\x80-\xff]*"'
_ELEMENT = rf"[ \t]*(?:{_TAG}[ \t]*)?"
_LIST = re.compile(rf"{_ELEMENT}(?:,{_ELEMENT})*")
_TAGS = re.compile(_TAG)


def parse_entity_tags(value: str) -> tuple[str, ...]:
    # Empty list elements are allowed, but a wildcard is not an entity-tag.
    if not _LIST.fullmatch(value):
        return ()
    return tuple(tag[0] for tag in _TAGS.finditer(value))


def if_none_match(lines: Sequence[str], etag: str | None) -> bool:
    """Compare a condition to an existing representation, which may be untagged."""
    value = ",".join(lines)
    if value.strip(" \t") == "*":
        return True
    if etag is None:
        return False
    return any(
        tag.removeprefix("W/") == etag.removeprefix("W/") for tag in parse_entity_tags(value)
    )


def gzip_etag(etag: str) -> str:
    return etag[:-1] + '-gzip"' if _TAGS.fullmatch(etag) else etag
