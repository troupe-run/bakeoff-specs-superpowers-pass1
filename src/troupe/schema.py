"""The theatre's JSON Schema (`.troupe/troupe.schema.json`): which strings are URIs, and validation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, best_match

SCHEMA_FILE = "troupe.schema.json"


class SchemaProblem(Exception):
    """The schema cannot be read, or the configuration does not satisfy it."""


def load_schema(troupe_dir: Path) -> tuple[Any, str | None]:
    """The schema (None if there is none) and the text it was read from."""
    path = troupe_dir / SCHEMA_FILE
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as exc:
        raise SchemaProblem(f"{path}: cannot read: {exc}") from exc
    try:
        schema = json.loads(text)
    except ValueError as exc:
        raise SchemaProblem(f"{path}: invalid JSON: {exc}") from exc
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise SchemaProblem(f"{path}: not a valid JSON Schema: {exc.message}") from exc
    return schema, text


def prop_schema(schema: Any, key: str) -> Any:
    """The schema of mapping entry `key`: from `properties`, else `additionalProperties`."""
    if not isinstance(schema, dict):
        return None
    props = schema.get("properties")
    if isinstance(props, dict) and key in props:
        return props[key]
    patterns = schema.get("patternProperties")
    if isinstance(patterns, dict) and any(_search(p, key) for p in patterns):
        return None  # additionalProperties does not apply to it
    extra = schema.get("additionalProperties")
    return extra if isinstance(extra, dict) else None


def item_schema(schema: Any, index: int) -> Any:
    """The schema of list item `index`: from `items` (which applies after any `prefixItems`)."""
    if not isinstance(schema, dict):
        return None
    prefix = schema.get("prefixItems")
    if isinstance(prefix, list) and index < len(prefix):
        return None
    items = schema.get("items")
    return items if isinstance(items, dict) else None


def is_uri(schema: Any) -> bool:
    return isinstance(schema, dict) and schema.get("format") == "uri"


def validate(schema: Any, instance: Any, where: Path) -> None:
    if schema is None:
        return
    error = best_match(Draft202012Validator(schema).iter_errors(instance))
    if error is not None:
        at = ".".join(str(p) for p in error.absolute_path) or "(root)"
        raise SchemaProblem(f"configuration does not match {where}: at {at}: {error.message}")


def _search(pattern: str, key: str) -> bool:
    try:
        return re.search(pattern, key) is not None
    except re.error:
        return False
