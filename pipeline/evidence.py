"""Read the engine's compact evidence markdown.

The compact output is what the skill hands its host model to write a report
from. We use it for the same purpose (Claude's input) and pull a few pieces
out of it directly: community quotes, the source footer and every URL, so
anything Claude cites can be checked against what was actually retrieved.
"""

from __future__ import annotations

import re

EVIDENCE_START = "<!-- EVIDENCE FOR SYNTHESIS"
EVIDENCE_END = "<!-- END EVIDENCE FOR SYNTHESIS -->"
SEP = " \u2014 "  # the engine separates fields in quote lines with a spaced em dash

_URL_RE = re.compile(r"https?://[^\s<>\"'\])]+")
_DATE_RANGE_RE = re.compile(r"Date range:\s*(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})")
_COUNT_RE = re.compile(r"^(?P<who>.*?)\s+·\s+(?P<count>[\d.,]+\s*[kKmM]?)\s+(?P<unit>[A-Za-z]+)\s*$")
_LABEL_RE = re.compile(r"^(top comment|#\d+ comment|post|said|reply|comment|top reply)$", re.I)
_LEADING_SYMBOLS_RE = re.compile(r"^[^\w@#/(]+", re.UNICODE)


def clean_url(url: str) -> str:
    return url.rstrip(".,;:!?)]}>'\"")


def urls(text: str) -> set[str]:
    return {clean_url(match) for match in _URL_RE.findall(text or "")}


def date_range(evidence: str) -> tuple[str, str] | None:
    match = _DATE_RANGE_RE.search(evidence or "")
    return (match.group(1), match.group(2)) if match else None


def _sections(region: str) -> list[tuple[str, str]]:
    """Split a markdown region into (heading, body) pairs on '## ' headings."""
    parts: list[tuple[str, str]] = []
    heading, buf = "", []
    for line in region.splitlines():
        if line.startswith("## "):
            if heading or any(l.strip() for l in buf):
                parts.append((heading, "\n".join(buf).strip()))
            heading, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if heading or any(l.strip() for l in buf):
        parts.append((heading, "\n".join(buf).strip()))
    return parts


def evidence_region(evidence: str) -> str:
    start = evidence.find(EVIDENCE_START)
    end = evidence.find(EVIDENCE_END)
    if start == -1:
        return evidence
    start = evidence.find("\n", start) + 1
    return evidence[start:end if end != -1 else len(evidence)]


def footer_block(evidence: str) -> str:
    match = re.search(r"```text\n(.*?)```", evidence or "", re.S)
    return match.group(1).strip() if match else ""


def section(evidence: str, name: str) -> str:
    for heading, body in _sections(evidence_region(evidence)):
        if heading.lower() == name.lower():
            return body
    return ""


def synthesis_input(evidence: str, max_chars: int) -> str:
    """The evidence Claude reads, trimmed to a budget.

    Quotes and stats are kept whole; ranked clusters are cut at a cluster
    boundary when the budget runs out, lowest-ranked first.
    """
    header = []
    range_ = date_range(evidence)
    if range_:
        header.append(f"Research window: {range_[0]} to {range_[1]}")
    region = evidence_region(evidence)
    sections = _sections(region)
    priority = {"top community comments": 0, "best takes": 1, "stats": 2,
                "ranked evidence clusters": 3, "source coverage": 5}
    ordered = sorted(sections, key=lambda pair: priority.get(pair[0].lower(), 4))

    budget = max_chars - sum(len(h) + 1 for h in header) - 200
    chosen: dict[str, str] = {}
    for heading, body in ordered:
        block = f"## {heading}\n{body}\n" if heading else f"{body}\n"
        if len(block) <= budget:
            chosen[heading] = block
            budget -= len(block)
        elif heading.lower() == "ranked evidence clusters" and budget > 500:
            clusters = re.split(r"(?m)^(?=### )", body)
            kept = []
            for chunk in clusters:
                if len(chunk) + 40 > budget:
                    break
                kept.append(chunk)
                budget -= len(chunk)
            chosen[heading] = f"## {heading}\n{''.join(kept).rstrip()}\n(lower-ranked clusters omitted)\n"
    # Keep the engine's own section order for readability.
    body = "\n".join(chosen[h] for h, _ in sections if h in chosen)
    footer = footer_block(evidence)
    text = "\n".join(header) + "\n\n" + body
    if footer and len(text) + len(footer) + 40 < max_chars:
        text += "\n## Source footer\n" + footer + "\n"
    return text.strip() + "\n"


def parse_quote_line(line: str) -> dict | None:
    """Parse one '## Top Community Comments' line into its parts.

    Shape: - "body" attribution SEP url SEP cluster N SEP label [(about ...)] [SEP funny]
    Parsed right to left because the quoted body may contain anything.
    """
    line = line.strip()
    if not line.startswith('- "'):
        return None
    rest = line[2:]
    funny = False
    if rest.endswith(SEP + "funny"):
        rest, funny = rest[: -len(SEP + "funny")], True
    rest = re.sub(r"\s+\(about [^)]*\)$", "", rest)
    label = ""
    head, sep, tail = rest.rpartition(SEP)
    if sep and _LABEL_RE.match(tail.strip()):
        rest, label = head, tail.strip()
    cluster = None
    head, sep, tail = rest.rpartition(SEP)
    if sep and re.match(r"^cluster \d+$", tail.strip()):
        rest, cluster = head, int(tail.strip().split()[1])
    elif sep and tail.strip().startswith('re: "'):
        rest = head
    url = ""
    head, sep, tail = rest.rpartition(SEP)
    if sep and tail.strip().startswith(("http://", "https://")):
        rest, url = head, clean_url(tail.strip())
    split_at = rest.rfind('" ')
    if not rest.startswith('"') or split_at <= 0:
        return None
    text = rest[1:split_at].strip()
    attribution = rest[split_at + 2:].strip()
    who, count = attribution, ""
    match = _COUNT_RE.match(attribution)
    if match:
        who = match.group("who")
        count = f"{match.group('count').strip()} {match.group('unit')}"
    who = _LEADING_SYMBOLS_RE.sub("", who).strip()
    if not text:
        return None
    return {"text": text, "who": who, "count": count, "url": url,
            "cluster": cluster, "label": label, "funny": funny}


def community_comments(evidence: str) -> list[dict]:
    quotes = []
    for name in ("Top Community Comments", "Best Takes"):
        for line in section(evidence, name).splitlines():
            parsed = parse_quote_line(line)
            if parsed and all(parsed["text"] != q["text"] for q in quotes):
                quotes.append(parsed)
    return quotes


def footer_sources(evidence: str) -> list[dict]:
    """Per-source lines from the engine footer: label plus a short detail."""
    rows = []
    for raw in footer_block(evidence).splitlines():
        line = raw.strip()
        if not line.startswith(("├", "└")):  # tree branch characters
            continue
        line = re.sub(r"^[├└]─\s*", "", line)
        line = _LEADING_SYMBOLS_RE.sub("", line).strip()
        if ":" not in line:
            continue
        label, detail = line.split(":", 1)
        detail = re.sub(r"\s*│\s*", ", ", detail.strip())
        rows.append({"label": label.strip(), "detail": detail})
    return rows


def normalize_for_match(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[‘’“”\"'`]", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_in_evidence(quote: str, evidence: str) -> bool:
    """True when a quote's words appear in the evidence (punctuation ignored).

    The evidence may wrap or truncate long comments with '...', so a quote
    passes when a long enough prefix of it is found verbatim.
    """
    needle = normalize_for_match(quote)
    if len(needle) < 8:
        return False
    haystack = normalize_for_match(evidence)
    if needle in haystack:
        return True
    words = needle.split()
    if len(words) >= 8:
        prefix = " ".join(words[: max(8, int(len(words) * 0.6))])
        return prefix in haystack
    return False
