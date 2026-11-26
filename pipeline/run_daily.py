"""Research every topic in pulse.toml and write today's brief for the app.

Usage:
  python pipeline/run_daily.py --engine path/to/last30days.py [--config pulse.toml] [--out site/data]

Set ANTHROPIC_API_KEY to have Claude write the brief. Any keys the engine
understands (SCRAPECREATORS_API_KEY, XAI_API_KEY, BRAVE_API_KEY, ...) are
passed through from the environment.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as config_mod  # noqa: E402
import engine as engine_mod  # noqa: E402
import evidence as ev  # noqa: E402
import output  # noqa: E402
import synthesize as syn  # noqa: E402


def _step_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", required=True, type=Path, help="path to skills/last30days/scripts/last30days.py")
    parser.add_argument("--config", default="pulse.toml", type=Path)
    parser.add_argument("--out", default="site/data", type=Path)
    parser.add_argument("--work", default=".work", type=Path, help="scratch folder for engine output")
    parser.add_argument("--only", default="", help="comma-separated topic ids to run")
    parser.add_argument("--no-rising", action="store_true")
    parser.add_argument("--date", default="", help="override today's date (YYYY-MM-DD)")
    parser.add_argument("--mock", action="store_true", help="use the engine's offline fixtures (for testing)")
    args = parser.parse_args(argv)

    cfg = config_mod.load(args.config)
    args.engine, args.work, args.out = args.engine.resolve(), args.work.resolve(), args.out.resolve()
    if not args.engine.exists():
        print(f"error: engine not found at {args.engine}", file=sys.stderr)
        return 2
    today = date.fromisoformat(args.date) if args.date else datetime.now(ZoneInfo(cfg.timezone)).date()
    topics = cfg.topics
    if args.only:
        wanted = {t.strip() for t in args.only.split(",") if t.strip()}
        topics = [t for t in topics if t.id in wanted]
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    args.work.mkdir(parents=True, exist_ok=True)

    print(f"Pulse brief for {today.isoformat()}: {len(topics)} topic(s), "
          f"writer: {'Claude (' + cfg.model + ')' if api_key else 'basic (no ANTHROPIC_API_KEY)'}")

    entries, modes, summary_rows = [], set(), []
    window_start = (today - timedelta(days=cfg.days - 1)).isoformat()
    for topic in topics:
        print(f"\n> {topic.name}: researching \"{topic.query}\"")
        run = engine_mod.run_topic(args.engine, topic, cfg, args.work, mock=args.mock)
        if not run.ok:
            print(f"  failed: {run.error}")
            for line in run.log_tail:
                print(f"    {line}")
            entries.append(output.topic_entry(topic, None, None, "none", today, cfg.days, error=run.error))
            summary_rows.append(f"| {topic.name} | failed: {run.error} | {run.seconds:.0f}s |")
            continue
        range_ = ev.date_range(run.evidence)
        if range_:
            window_start = range_[0]
        items = len((run.agent or {}).get("results") or [])
        print(f"  engine done in {run.seconds:.0f}s, {items} ranked results")

        brief, mode = None, "basic"
        if api_key:
            try:
                brief = syn.claude_brief(topic.name, topic.query, today.isoformat(), cfg.days,
                                         run.evidence, run.agent, cfg, api_key)
                mode = "claude"
                if not brief["stories"] and items:
                    print("  Claude found no on-topic stories")
            except syn.SynthesisError as exc:
                print(f"  Claude failed, using the basic brief: {exc}")
        if brief is None:
            brief = syn.basic_brief(topic.name, run.evidence, run.agent)
        modes.add(mode)
        entry = output.topic_entry(topic, run, brief, mode, today, cfg.days)
        entries.append(entry)
        print(f"  wrote {len(entry['stories'])} stories ({mode})")
        summary_rows.append(f"| {topic.name} | {len(entry['stories'])} stories, {items} results, {mode} | {run.seconds:.0f}s |")

    rising = None
    if cfg.rising_enabled and not args.no_rising:
        print(f"\n> Rising: sweeping {'domain ' + repr(cfg.rising_domain) if cfg.rising_domain else 'global trending'}")
        rising_run = engine_mod.run_rising(args.engine, cfg, args.work, mock=args.mock)
        if rising_run.ok:
            rising = output.rising_entry(rising_run.data, cfg.rising_domain)
            print(f"  {len(rising['items'])} rising topics in {rising_run.seconds:.0f}s")
            summary_rows.append(f"| Rising | {len(rising['items'])} topics | {rising_run.seconds:.0f}s |")
        else:
            print(f"  failed: {rising_run.error}")
            summary_rows.append(f"| Rising | failed: {rising_run.error} | {rising_run.seconds:.0f}s |")

    if not any(e["stories"] for e in entries) and not (rising and rising["items"]):
        print("\nNothing usable came back today, so yesterday's brief stays up.", file=sys.stderr)
        _step_summary(["## Pulse brief", "", "Nothing usable came back today.", "",
                       "| Topic | Result | Time |", "|---|---|---|", *summary_rows])
        return 1

    overview = None
    if api_key and "claude" in modes:
        try:
            overview = syn.claude_overview(entries, cfg, api_key)
        except syn.SynthesisError as exc:
            print(f"Overview failed, using the basic one: {exc}")
    if overview is None:
        overview = syn.basic_overview(entries)

    day = {
        "schema": output.DATA_SCHEMA,
        "date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "title": cfg.title,
        "timezone": cfg.timezone,
        "window": {"start": window_start, "end": today.isoformat(), "days": cfg.days},
        "overview": overview,
        "written_by": "claude" if modes == {"claude"} else ("basic" if modes <= {"basic"} else "mixed"),
        "model": cfg.model if "claude" in modes else None,
        "engine": cfg.engine_version,
        "topics": entries,
        "rising": rising,
    }
    output.write_day(args.out, day, cfg.keep_days)
    print(f"\nSaved {args.out / 'days' / (today.isoformat() + '.json')}")
    _step_summary(["## Pulse brief for " + today.isoformat(), "", f"**{overview['headline']}**", "",
                   "| Topic | Result | Time |", "|---|---|---|", *summary_rows])
    return 0


if __name__ == "__main__":
    sys.exit(main())
