"""Command-line entry point: ``python3 -m services.discover ...``.

Prints a human-readable ranked list, and (with ``--submit``) POSTs each top
candidate to the existing ``POST /jobs`` endpoint of the API service.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib import error as url_error
from urllib import request as url_request

from services.discover.runner import VideoCandidate, discover


DEFAULT_API_URL = "http://localhost:8080"


def _submit_job(api_url: str, source_url: str, timeout_s: float = 10.0) -> str | None:
    """POST a single candidate URL to `/jobs`. Returns the new job_id or None.

    Uses stdlib `urllib` so the CLI has no extra deps. Failures are logged to
    stderr and surfaced as `None` so a bad submit doesn't kill the whole batch.
    """
    body = json.dumps({"source_url": source_url}).encode("utf-8")
    req = url_request.Request(
        f"{api_url.rstrip('/')}/jobs",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with url_request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (url_error.URLError, url_error.HTTPError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"  [submit-failed] {source_url}: {exc}", file=sys.stderr)
        return None
    job_id = payload.get("id")
    return str(job_id) if job_id else None


def _format_candidate(idx: int, c: VideoCandidate) -> str:
    reasons = ", ".join(c.reasons) if c.reasons else "-"
    return (
        f"{idx}. score={c.score:.2f}  views={c.views:>10,d}  "
        f"dur={int(c.duration_s):>5d}s  {c.upload_date or '????????'}  "
        f"[{c.channel}]\n   {c.title}\n   {c.url}\n   why: {reasons}"
    )


def _to_dict(c: VideoCandidate) -> dict[str, Any]:
    return c.to_dict()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python3 -m services.discover",
        description="Crawl YouTube for trending videos on a topic.",
    )
    p.add_argument(
        "--topic",
        default=None,
        help='Search topic, e.g. "tarpon fishing". Required unless --preset is given.',
    )
    p.add_argument(
        "--preset",
        default=None,
        choices=sorted(__import__("services.discover.runner", fromlist=["NICHE_PRESETS"]).NICHE_PRESETS.keys()),
        help="Use a curated niche query set (fishing / survival / trap / camping). "
             "Overrides --topic's query expansion.",
    )
    p.add_argument("--top", type=int, default=5, help="How many top candidates to return")
    p.add_argument("--min-views", type=int, default=50_000)
    p.add_argument(
        "--max-age-days",
        type=int,
        default=180,
        help="Reject videos older than this many days (use -1 to disable)",
    )
    p.add_argument("--per-query", type=int, default=20)
    p.add_argument("--max-results", type=int, default=30)
    p.add_argument(
        "--no-english-only",
        action="store_true",
        help="Allow non-English titles (Vietnamese, CJK, ...)",
    )
    p.add_argument(
        "--submit",
        action="store_true",
        help=f"POST each top candidate to {DEFAULT_API_URL}/jobs",
    )
    p.add_argument("--api-url", default=DEFAULT_API_URL)
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a human table",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from services.discover.runner import expand_with_preset

    if not args.topic and not args.preset:
        print("error: --topic or --preset is required", file=sys.stderr)
        return 2

    preset_queries: list[str] | None = None
    if args.preset:
        preset_queries = expand_with_preset(args.preset)
        if not preset_queries:
            print(f"error: unknown preset {args.preset!r}", file=sys.stderr)
            return 2
    # Default topic for ranking / display when only a preset was given.
    effective_topic = args.topic or args.preset

    max_age = None if args.max_age_days is not None and args.max_age_days < 0 else args.max_age_days
    candidates = discover(
        effective_topic,
        top=args.top,
        min_views=args.min_views,
        max_age_days=max_age,
        per_query=args.per_query,
        max_results=args.max_results,
        require_english=not args.no_english_only,
        queries=preset_queries,
    )

    if args.json:
        out = {"topic": args.topic, "candidates": [_to_dict(c) for c in candidates]}
        # We may also collect submitted job IDs below; build the dict eagerly
        # and mutate it before printing.
        if args.submit:
            out["submitted_job_ids"] = []
            for c in candidates:
                jid = _submit_job(args.api_url, c.url)
                if jid:
                    out["submitted_job_ids"].append(jid)
        print(json.dumps(out, indent=2))
        return 0

    if not candidates:
        print(f"No candidates found for topic: {args.topic!r}")
        return 1

    print(f"Top {len(candidates)} candidates for {args.topic!r}:\n")
    for i, c in enumerate(candidates, 1):
        print(_format_candidate(i, c))
        print()

    if args.submit:
        print(f"Submitting {len(candidates)} job(s) to {args.api_url}/jobs ...")
        submitted: list[str] = []
        for c in candidates:
            jid = _submit_job(args.api_url, c.url)
            if jid:
                submitted.append(jid)
                print(f"  ok  {c.video_id} -> job_id={jid}")
            else:
                print(f"  err {c.video_id} (see stderr)")
        print(f"\nSubmitted {len(submitted)}/{len(candidates)} jobs.")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin wrapper
    raise SystemExit(main())
