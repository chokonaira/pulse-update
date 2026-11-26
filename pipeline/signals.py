"""Engagement signals for the app: per-item heat, the 30-day pulse, labels.

Engagement numbers mean different things on different platforms (a YouTube
view is not a Reddit upvote), so heat is a percentile *within each source*.
A thread in the top tenth of today's Reddit results and a video in the top
tenth of today's YouTube results get the same heat.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

SOURCE_LABELS = {
    "reddit": "Reddit", "hackernews": "Hacker News", "x": "X", "youtube": "YouTube",
    "youtube_comments": "YouTube", "tiktok": "TikTok", "instagram": "Instagram",
    "polymarket": "Polymarket", "github": "GitHub", "grounding": "Web", "web": "Web",
    "digg": "Digg", "arxiv": "arXiv", "techmeme": "Techmeme", "bluesky": "Bluesky",
    "threads": "Threads", "linkedin": "LinkedIn", "devto": "dev.to", "stocktwits": "StockTwits",
    "perplexity": "Perplexity", "jobs": "Jobs", "patents": "Patents", "dripstack": "DripStack",
    "trustpilot": "Trustpilot", "amazon": "Amazon", "meta_ads": "Meta Ads", "pinterest": "Pinterest",
    "truthsocial": "Truth Social", "telegram": "Telegram", "xiaohongshu": "Xiaohongshu",
    "yahoo_finance": "Yahoo Finance", "corpus": "Your files",
}

# Which counter is the headline number for a source, and what to call it.
PRIMARY_COUNTER = {
    "reddit": [("score", "upvotes")],
    "hackernews": [("points", "points")],
    "x": [("likes", "likes")],
    "youtube": [("views", "views")],
    "tiktok": [("views", "views"), ("plays", "plays"), ("likes", "likes")],
    "instagram": [("views", "views"), ("plays", "plays"), ("likes", "likes")],
    "github": [("stars", "stars"), ("reactions", "reactions"), ("comments", "comments")],
    "bluesky": [("likes", "likes")],
    "threads": [("likes", "likes")],
    "digg": [("postCount", "posts")],
    "devto": [("reactions", "reactions"), ("positive_reactions_count", "reactions")],
    "linkedin": [("reactions", "reactions"), ("likes", "likes")],
    "polymarket": [],  # its counter is dollar volume, which we never show
}
_SKIP_FIELDS = {"rank", "rating", "score", "trustscore", "followers", "subscribers", "volume"}


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source.replace("_", " ").title())


def _is_counter(field: str) -> bool:
    f = field.lower()
    return not (f in _SKIP_FIELDS or f.endswith(("_rank", "_score", "_ratio", "_rate", "_followers")))


def headline_count(source: str, engagement: dict) -> tuple[float, str]:
    """(value, unit) of the number worth showing for one result."""
    engagement = engagement or {}
    for field, unit in PRIMARY_COUNTER.get(source, []):
        value = engagement.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value), unit
    if source == "polymarket":
        return 0.0, ""
    best = (0.0, "")
    for field, value in engagement.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool) and _is_counter(field):
            if value > best[0]:
                best = (float(value), field.replace("_", " ").replace("num ", "").lower())
    return best


def compact_number(value: float) -> str:
    if value >= 1_000_000:
        text = f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{text}M"
    if value >= 10_000:
        return f"{round(value / 1000):.0f}k"
    if value >= 1_000:
        text = f"{value / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{text}k"
    return f"{int(value)}"


def count_label(source: str, engagement: dict) -> str:
    value, unit = headline_count(source, engagement)
    if value <= 0 or not unit:
        return ""
    if value == 1 and unit.endswith("s"):
        unit = unit[:-1]
    return f"{compact_number(value)} {unit}"


def result_heat(results: list[dict]) -> dict[str, float]:
    """Map result URL to heat in [0, 1], as a within-source percentile.

    A source with a single result gets 0.5: there is nothing to rank it against.
    """
    by_source: dict[str, list[tuple[str, float]]] = {}
    for r in results:
        url = r.get("url") or ""
        if not url:
            continue
        value, _ = headline_count(r.get("source", ""), r.get("engagement") or {})
        by_source.setdefault(r.get("source", ""), []).append((url, math.log1p(value)))
    heat: dict[str, float] = {}
    for rows in by_source.values():
        values = sorted(v for _, v in rows)
        n = len(values)
        for url, v in rows:
            if n == 1 or values[0] == values[-1]:
                pct = 0.5 if v > 0 else 0.25
            else:
                below = sum(1 for x in values if x < v)
                equal = sum(1 for x in values if x == v)
                pct = (below + 0.5 * equal) / n
            heat[url] = round(max(heat.get(url, 0.0), pct), 3)
    return heat


def _parse_day(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def pulse(results: list[dict], heat: dict[str, float], end: date, days: int) -> tuple[list[float], str | None]:
    """Daily activity over the window, normalized to a max of 1.

    Each result adds 0.3 plus its heat on the day it was published, so the
    line rises with both volume and how hard people engaged.
    """
    start = end - timedelta(days=days - 1)
    bins = [0.0] * days
    for r in results:
        day = _parse_day(r.get("published_at", ""))
        if day is None or day < start or day > end:
            continue
        bins[(day - start).days] += 0.3 + heat.get(r.get("url") or "", 0.25)
    raw_peak = max(bins) if bins else 0.0
    if raw_peak <= 0:
        return [0.0] * days, None
    peak_day = start + timedelta(days=bins.index(raw_peak))
    # Light smoothing so a single post reads as a bump, not a needle.
    smooth = [
        0.25 * (bins[i - 1] if i > 0 else bins[i]) + 0.5 * bins[i] + 0.25 * (bins[i + 1] if i < days - 1 else bins[i])
        for i in range(days)
    ]
    peak = max(smooth)
    return [round(b / peak, 3) for b in smooth], peak_day.isoformat()
