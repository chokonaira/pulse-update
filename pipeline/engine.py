"""Run the last30days engine headlessly and collect what it produced."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from config import Config, Topic

# Environment for every engine call. These mark the run as unattended and
# keep it from touching anything outside its own work folder.
_ENGINE_ENV = {
    "LAST30DAYS_SCHEDULED_RUN": "1",       # unattended: never use a logged-in session
    "LAST30DAYS_ALLOW_ENGINE_PLAN": "1",   # plan internally even if launched from an agent shell
    "LAST30DAYS_LIBRARY_CONTEXT": "off",   # no local research library in CI
    "LAST30DAYS_DISCOVERY_QUEUE": "off",
    "LAST30DAYS_AUTO_CAPTURE": "0",
    "PYTHONIOENCODING": "utf-8",
}


@dataclass
class TopicRun:
    topic: Topic
    ok: bool
    evidence: str = ""
    agent: dict | None = None
    seconds: float = 0.0
    error: str = ""
    log_tail: list[str] = field(default_factory=list)


@dataclass
class RisingRun:
    ok: bool
    data: dict | None = None
    seconds: float = 0.0
    error: str = ""


def _base_env(config_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(_ENGINE_ENV)
    env["LAST30DAYS_CONFIG_DIR"] = str(config_dir)
    # Nothing is saved to a research folder; we read the outputs we asked for.
    env.pop("LAST30DAYS_MEMORY_DIR", None)
    return env


def build_topic_command(engine: Path, topic: Topic, cfg: Config, evidence_path: Path, mock: bool) -> list[str]:
    cmd = [
        sys.executable, str(engine), topic.query,
        "--research-only",          # a topic named "setup" stays a topic
        "--emit=compact",
        "--output", str(evidence_path),
        "--no-browser-cookies",
    ]
    if cfg.depth == "quick":
        cmd.append("--quick")
    elif cfg.depth == "deep":
        cmd.append("--deep")
    if cfg.days != 30:
        cmd += ["--days", str(cfg.days)]
    if topic.subreddits:
        cmd += ["--subreddits", ",".join(topic.subreddits)]
    if topic.github_repos:
        cmd += ["--github-repo", ",".join(topic.github_repos)]
    if topic.x_handle:
        cmd += ["--x-handle", topic.x_handle]
    if topic.x_related:
        cmd += ["--x-related", ",".join(topic.x_related)]
    if topic.search:
        cmd += ["--search", ",".join(topic.search)]
    if mock:
        cmd.append("--mock")
    return cmd


def load_agent_export(engine: Path, report_cache: Path) -> dict | None:
    """Turn the engine's cached report into its versioned agent JSON.

    One engine run gives us two views: the compact evidence (best input for
    writing) and, through this conversion, structured results with URLs,
    dates and engagement counts (best input for the app's signals).
    """
    if not report_cache.exists():
        return None
    scripts_dir = str(engine.parent)
    added = scripts_dir not in sys.path
    if added:
        sys.path.insert(0, scripts_dir)
    try:
        from lib import schema  # type: ignore  # the engine's own module

        cache = json.loads(report_cache.read_text(encoding="utf-8"))
        reports = cache.get("reports") or []
        if not reports:
            return None
        first = reports[0]
        payload = first.get("report", first) if isinstance(first, dict) else first
        return schema.to_agent_export(schema.report_from_dict(payload))
    except Exception as exc:  # the cache format is internal; never fail the day over it
        print(f"  [warn] could not read structured results: {exc}", file=sys.stderr)
        return None
    finally:
        if added and scripts_dir in sys.path:
            sys.path.remove(scripts_dir)


def run_topic(engine: Path, topic: Topic, cfg: Config, workdir: Path, mock: bool = False) -> TopicRun:
    topic_dir = workdir / topic.id
    config_dir = topic_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = topic_dir / "evidence.md"
    cmd = build_topic_command(engine, topic, cfg, evidence_path, mock)

    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            env=_base_env(config_dir),
            cwd=topic_dir,
            capture_output=True,
            text=True,
            timeout=cfg.timeout_minutes * 60,
        )
    except subprocess.TimeoutExpired:
        return TopicRun(topic, ok=False, seconds=time.monotonic() - started,
                        error=f"timed out after {cfg.timeout_minutes} minutes")
    seconds = time.monotonic() - started
    (topic_dir / "engine.log").write_text(proc.stderr or "", encoding="utf-8")
    log_tail = [line for line in (proc.stderr or "").splitlines() if line.strip()][-12:]

    evidence = evidence_path.read_text(encoding="utf-8") if evidence_path.exists() else ""
    if proc.returncode != 0 or not evidence.strip():
        return TopicRun(topic, ok=False, seconds=seconds, log_tail=log_tail,
                        error=f"engine exited with status {proc.returncode}")

    agent = load_agent_export(engine, config_dir / "last-report.json")
    return TopicRun(topic, ok=True, evidence=evidence, agent=agent, seconds=seconds, log_tail=log_tail)


def run_rising(engine: Path, cfg: Config, workdir: Path, mock: bool = False) -> RisingRun:
    rising_dir = workdir / "_rising"
    config_dir = rising_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    out = rising_dir / "discover.json"
    cmd = [sys.executable, str(engine), "--discover"]
    if cfg.rising_domain.strip():
        cmd.append(cfg.rising_domain.strip())
    cmd += ["--emit=json", "--output", str(out), "--no-browser-cookies"]
    if mock:
        cmd.append("--mock")

    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, env=_base_env(config_dir), cwd=rising_dir,
                              capture_output=True, text=True, timeout=15 * 60)
    except subprocess.TimeoutExpired:
        return RisingRun(ok=False, seconds=time.monotonic() - started, error="timed out")
    seconds = time.monotonic() - started
    (rising_dir / "engine.log").write_text(proc.stderr or "", encoding="utf-8")
    if proc.returncode != 0 or not out.exists():
        return RisingRun(ok=False, seconds=seconds, error=f"engine exited with status {proc.returncode}")
    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return RisingRun(ok=False, seconds=seconds, error=f"unreadable output: {exc}")
    return RisingRun(ok=True, data=data, seconds=seconds)
