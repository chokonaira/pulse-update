"""Assemble the day's JSON for the app and keep the archive index current."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import evidence as ev
import signals
from synthesize import clean_prose, url_key

DATA_SCHEMA = 1
CORE_SOURCES = ["reddit", "hackernews", "x", "youtube", "polymarket", "github", "grounding"]
FAILURE_STATES = {"partial", "rate-limited", "auth-failed", "payment-required", "unreachable",
                  "timeout", "schema-drift", "error"}
STATUS_WORDS = {
    "partial": "partly loaded", "rate-limited": "rate limited", "auth-failed": "login failed",
    "payment-required": "out of credits", "unreachable": "unreachable", "timeout": "timed out",
    "schema-drift": "format changed", "error": "failed", "no-results": "nothing found",
    "skipped-unconfigured": "not set up",
}


def _results_by_key(agent: dict | None) -> dict[str, dict]:
    return {url_key(r["url"]): r for r in (agent or {}).get("results", []) if r.get("url")}


def _source_from_url(url: str) -> str:
    host = re.sub(r"^https?://(www\.|old\.|m\.)?", "", url).split("/")[0].lower()
    for needle, source in (("reddit.com", "reddit"), ("ycombinator.com", "hackernews"),
                           ("x.com", "x"), ("twitter.com", "x"), ("youtube.com", "youtube"),
                           ("youtu.be", "youtube"), ("polymarket.com", "polymarket"),
                           ("github.com", "github"), ("tiktok.com", "tiktok"),
                           ("instagram.com", "instagram"), ("arxiv.org", "arxiv"),
                           ("bsky.app", "bluesky"), ("techmeme.com", "techmeme")):
        if host == needle or host.endswith("." + needle):
            return source
    return "grounding"


def _domain(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url).split("/")[0]


def source_entry(url: str, lookup: dict[str, dict], heat: dict[str, float]) -> dict:
    result = lookup.get(url_key(url))
    source = (result or {}).get("source") or _source_from_url(url)
    title = clean_prose((result or {}).get("title"), 140) or _domain(url)
    return {
        "url": url,
        "source": source,
        "label": signals.source_label(source) if source != "grounding" else _domain(url),
        "title": title,
        "count": signals.count_label(source, (result or {}).get("engagement") or {}),
        "date": ((result or {}).get("published_at") or "")[:10],
        "heat": heat.get((result or {}).get("url", ""), 0.0),
    }


def coverage(agent: dict | None, evidence_text: str) -> list[dict]:
    status = (agent or {}).get("source_status") or {}
    details = {row["label"].lower(): row["detail"] for row in ev.footer_sources(evidence_text)}
    rows = []
    for source, state in sorted(status.items(), key=lambda kv: (kv[0] not in CORE_SOURCES, kv[0])):
        label = signals.source_label(source)
        if state == "ok":
            detail = details.get(label.lower()) or details.get(source) or ""
            detail = clean_prose(detail.replace(" - ", ", "), 120)
            rows.append({"source": source, "label": label, "state": "ok", "detail": detail})
        elif state in FAILURE_STATES:
            rows.append({"source": source, "label": label, "state": "failed",
                         "detail": STATUS_WORDS.get(state, state)})
        elif source in CORE_SOURCES:
            rows.append({"source": source, "label": label,
                         "state": "empty" if state == "no-results" else "off",
                         "detail": STATUS_WORDS.get(state, state)})
    order = {"ok": 0, "failed": 1, "empty": 2, "off": 3}
    rows.sort(key=lambda r: (order[r["state"]], r["source"] not in CORE_SOURCES, r["label"].lower()))
    return rows


def reading_minutes(brief: dict) -> int:
    words = len((brief.get("summary") or "").split())
    for s in brief.get("stories", []):
        words += len(s["headline"].split()) + len(s["body"].split())
        if s.get("quote"):
            words += len(s["quote"]["text"].split())
    words += sum(len(p.split()) for p in brief.get("patterns", []))
    return max(1, round(words / 220))


def topic_entry(topic, run, brief: dict | None, mode: str, end: date, days: int, error: str = "") -> dict:
    agent = run.agent if run else None
    evidence_text = run.evidence if run else ""
    results = (agent or {}).get("results") or []
    heat = signals.result_heat(results)
    lookup = _results_by_key(agent)
    pulse, peak = signals.pulse(results, heat, end, days)

    stories = []
    for story in (brief or {}).get("stories", []):
        sources = [source_entry(u, lookup, heat) for u in story.get("sources", [])]
        quote = story.get("quote")
        quote_heat = heat.get((lookup.get(url_key(quote["url"])) or {}).get("url", ""), 0.0) if quote and quote.get("url") else 0.0
        story_heat = max([s["heat"] for s in sources] + [quote_heat] + [0.0])
        if story_heat == 0.0:
            story_heat = 0.25
        if len({s["source"] for s in sources}) >= 3:  # confirmed in three places: turn it up
            story_heat = min(1.0, story_heat + 0.15)
        stories.append({**story, "heat": round(story_heat, 3), "sources": sources})

    status = "ok" if stories else ("failed" if error else "thin")
    entry = {
        "id": topic.id,
        "name": topic.name,
        "query": topic.query,
        "status": status,
        "summary": (brief or {}).get("summary", ""),
        "stories": stories,
        "patterns": (brief or {}).get("patterns", []),
        "watch": (brief or {}).get("watch"),
        "pulse": pulse,
        "pulse_peak": peak,
        "coverage": coverage(agent, evidence_text),
        "counts": {
            "items": len(results),
            "sources": len({r.get("source") for r in results if r.get("source")}),
        },
        "written_by": mode,
        "reading_minutes": reading_minutes(brief or {}),
    }
    if error:
        entry["error"] = error
    return entry


def rising_entry(data: dict | None, domain: str) -> dict | None:
    if not data:
        return None
    items = []
    for row in (data.get("results") or [])[:10]:
        engagement = row.get("engagement") or {}
        parts = []
        for source in row.get("sources") or []:
            label = signals.count_label(source, engagement.get(source) or {})
            if label:
                parts.append({"source": source, "label": signals.source_label(source), "count": label})
        comment = (row.get("top_comment") or "").strip().strip('"“”').strip()
        why = row.get("why_spiking") or ""
        for source in row.get("sources") or []:  # internal source ids read badly
            why = re.sub(rf"\b{re.escape(source)}\b", signals.source_label(source), why)
        items.append({
            "rank": row.get("rank"),
            "topic": clean_prose(row.get("topic"), 90),
            "momentum": row.get("momentum") or "",
            "why": clean_prose(why, 260),
            "sources": [signals.source_label(s) for s in row.get("sources") or []],
            "counts": parts,
            "comment": comment[:280],
            "evidence": [u for u in (row.get("evidence_urls") or []) if u.startswith(("http://", "https://"))][:4],
            "corroboration": row.get("corroboration_count") or 0,
        })
    return {"domain": domain, "outcome": data.get("outcome", "ok"), "weak_signal": data.get("weak_signal"),
            "items": items}


def write_day(out_dir: Path, day: dict, keep_days: int) -> None:
    days_dir = out_dir / "days"
    days_dir.mkdir(parents=True, exist_ok=True)
    (days_dir / f"{day['date']}.json").write_text(json.dumps(day, ensure_ascii=False, indent=1), encoding="utf-8")

    cutoff = date.fromisoformat(day["date"]) - timedelta(days=keep_days)
    entries = []
    for path in sorted(days_dir.glob("*.json"), reverse=True):
        try:
            when = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if when < cutoff:
            path.unlink()
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        entries.append({
            "date": path.stem,
            "headline": (data.get("overview") or {}).get("headline", ""),
            "topics": [t["name"] for t in data.get("topics", [])],
            "stories": sum(len(t.get("stories", [])) for t in data.get("topics", [])),
        })
    index = {"schema": DATA_SCHEMA, "title": day.get("title", "Pulse"),
             "latest": entries[0]["date"] if entries else day["date"], "days": entries}
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
