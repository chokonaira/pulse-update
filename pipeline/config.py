"""Load and validate pulse.toml."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEPTHS = {"quick", "default", "deep"}
EFFORTS = {"low", "medium", "high"}


class ConfigError(ValueError):
    pass


@dataclass
class Topic:
    id: str
    name: str
    query: str
    subreddits: list[str] = field(default_factory=list)
    github_repos: list[str] = field(default_factory=list)
    x_handle: str = ""
    x_related: list[str] = field(default_factory=list)
    search: list[str] = field(default_factory=list)


@dataclass
class Config:
    title: str = "Pulse"
    timezone: str = "Europe/Berlin"
    keep_days: int = 365
    model: str = "claude-sonnet-5-5"
    effort: str = "medium"
    max_evidence_chars: int = 60000
    engine_version: str = "v4.6.0"
    depth: str = "default"
    days: int = 30
    timeout_minutes: int = 12
    topics: list[Topic] = field(default_factory=list)
    rising_enabled: bool = True
    rising_domain: str = "AI"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "topic"


def _str_list(value, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where} must be a list of strings")
    return [v.strip().lstrip("@").removeprefix("r/") for v in value if v.strip()]


def load(path: str | Path) -> Config:
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    site = raw.get("site", {})
    synthesis = raw.get("synthesis", {})
    engine = raw.get("engine", {})
    rising = raw.get("rising", {})

    cfg = Config(
        title=str(site.get("title", "Pulse")),
        timezone=str(site.get("timezone", "Europe/Berlin")),
        keep_days=int(site.get("keep_days", 365)),
        model=str(synthesis.get("model", "claude-sonnet-5-5")),
        effort=str(synthesis.get("effort", "medium")),
        max_evidence_chars=int(synthesis.get("max_evidence_chars", 60000)),
        engine_version=str(engine.get("version", "v4.6.0")),
        depth=str(engine.get("depth", "default")),
        days=int(engine.get("days", 30)),
        timeout_minutes=int(engine.get("timeout_minutes", 12)),
        rising_enabled=bool(rising.get("enabled", True)),
        rising_domain=str(rising.get("domain", "AI")),
    )
    if cfg.depth not in DEPTHS:
        raise ConfigError(f"engine.depth must be one of {sorted(DEPTHS)}")
    if cfg.effort not in EFFORTS:
        raise ConfigError(f"synthesis.effort must be one of {sorted(EFFORTS)}")
    if not 1 <= cfg.days <= 365:
        raise ConfigError("engine.days must be between 1 and 365")

    seen: set[str] = set()
    for index, entry in enumerate(raw.get("topic", []), start=1):
        where = f"topic #{index}"
        name = str(entry.get("name", "")).strip()
        query = str(entry.get("query", name)).strip()
        if not name or not query:
            raise ConfigError(f"{where} needs a name (and optionally a query)")
        topic_id = slugify(entry.get("id") or name)
        if topic_id in seen:
            raise ConfigError(f"{where}: two topics share the id '{topic_id}'")
        seen.add(topic_id)
        cfg.topics.append(
            Topic(
                id=topic_id,
                name=name,
                query=query,
                subreddits=_str_list(entry.get("subreddits"), f"{where} subreddits"),
                github_repos=_str_list(entry.get("github_repos"), f"{where} github_repos"),
                x_handle=str(entry.get("x_handle", "")).strip().lstrip("@"),
                x_related=_str_list(entry.get("x_related"), f"{where} x_related"),
                search=_str_list(entry.get("search"), f"{where} search"),
            )
        )
    if not cfg.topics and not cfg.rising_enabled:
        raise ConfigError("pulse.toml has no [[topic]] blocks and rising is off: nothing to do")
    return cfg
