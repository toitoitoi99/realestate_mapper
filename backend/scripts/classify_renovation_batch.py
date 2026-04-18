"""
Batch-API driver for the renovation classifier.

Rewrite of classify_renovation.py that uses Anthropic's Message Batches
endpoint: submit up to 10,000 vision calls at once, Anthropic runs them
asynchronously (completes within 24 h, usually much sooner), results are
50% cheaper than live calls, and regular per-minute rate limits don't
apply. Perfect for one-shot catalog backfills.

Images are passed as URL sources so we don't download + base64-encode
them client-side — Anthropic fetches the idealista CDN directly.

Three subcommands:

  submit — find every listing missing renovation_class, build a batch
           (default chunked at 2,000 per batch so payloads stay sane),
           call messages.batches.create, persist the batch ids + per-
           request custom_id → (table, listing_id) mapping to
           backend/data/renovation_batches.json.

  status — for each tracked batch, print its processing state and
           request counts (succeeded / errored / expired / ...).

  collect — for each batch that has completed, stream the results,
           parse each response JSON, and call save_result. Marks the
           batch as collected in the state file so re-running is a
           no-op until new batches are submitted.

Run:
  python3 scripts/classify_renovation_batch.py submit
  python3 scripts/classify_renovation_batch.py submit --limit 100  # smoke
  python3 scripts/classify_renovation_batch.py status
  python3 scripts/classify_renovation_batch.py collect

Requires ANTHROPIC_API_KEY in the environment.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anthropic  # noqa: E402
import database as db  # noqa: E402
from scorers.renovation_classifier import (  # noqa: E402
    MODEL,
    SYSTEM_PROMPT,
    _select_images,
    parse_classifier_raw_text,
    save_result,
)


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "renovation_batches.json"
MAX_REQUESTS_PER_BATCH = 2000  # Anthropic caps at 10,000; keep batches smaller
                                # so a hiccup costs less and status is granular.
CONFIDENT_STAGES = {"needs_reno", "full_remodel", "turnkey", "approved_project"}


# ---------- State ---------------------------------------------------------


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"batches": []}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning(f"state file {STATE_FILE} unreadable — starting fresh")
        return {"batches": []}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------- Submit --------------------------------------------------------


def _parse_images(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        urls = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [u for u in urls if isinstance(u, str) and u.startswith("http")]


def _select_rows(conn, tbl: str, skip_if_staged: bool, limit: Optional[int]):
    where = ["renovation_class IS NULL", "images IS NOT NULL", "LENGTH(images) > 10"]
    params: list = []
    if skip_if_staged:
        placeholders = ",".join("?" for _ in CONFIDENT_STAGES)
        where.append(
            f"(building_stage IS NULL OR building_stage NOT IN ({placeholders}))"
        )
        params.extend(CONFIDENT_STAGES)
    q = (f"SELECT id, images, description FROM {tbl} "
         f"WHERE {' AND '.join(where)}")
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, params).fetchall()


def _build_request(tbl: str, row) -> Optional[dict]:
    """Build one batch request from a DB row. Returns None if unusable."""
    image_urls = _parse_images(row["images"])
    if not image_urls:
        return None
    selected = _select_images(image_urls)

    content: list = []
    for url in selected:
        content.append({
            "type": "image",
            "source": {"type": "url", "url": url},
        })
    user_text = "Classify this property based on the photos above. Return only JSON."
    description = row["description"]
    if description:
        snippet = description[:600].replace("\n", " ")
        user_text += f"\n\nFor context, the listing description says: \"{snippet}\""
    content.append({"type": "text", "text": user_text})

    return {
        "custom_id": f"{tbl}_{row['id']}",
        "params": {
            "model": MODEL,
            "max_tokens": 600,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": content}],
        },
    }


def cmd_submit(args):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY not set")
        sys.exit(1)
    conn = db.get_connection()
    tables = [args.listing_type] if args.listing_type else ["sales", "rentals"]

    # Build every request up front so we can split across multiple batches.
    all_requests: list = []
    for tbl in tables:
        rows = _select_rows(conn, tbl, args.skip_if_staged, args.limit)
        logger.info(f"[{tbl}] {len(rows)} candidates")
        for r in rows:
            req = _build_request(tbl, r)
            if req is not None:
                all_requests.append(req)

    conn.close()
    if not all_requests:
        logger.info("no candidates; nothing to submit")
        return

    logger.info(f"submitting {len(all_requests)} requests "
                f"across {(len(all_requests) + MAX_REQUESTS_PER_BATCH - 1) // MAX_REQUESTS_PER_BATCH} batch(es)")

    client = anthropic.Anthropic()
    state = _load_state()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for i in range(0, len(all_requests), MAX_REQUESTS_PER_BATCH):
        chunk = all_requests[i:i + MAX_REQUESTS_PER_BATCH]
        batch = client.messages.batches.create(requests=chunk)
        logger.info(f"batch {batch.id}: created with {len(chunk)} requests "
                    f"(processing_status={batch.processing_status})")
        state["batches"].append({
            "batch_id": batch.id,
            "created_at": now,
            "request_count": len(chunk),
            "collected": False,
            # custom_id mapping is implicit in the custom_id format ("{tbl}:{id}")
            # — we store the list to make status/collect robust to that format.
            "custom_ids": [r["custom_id"] for r in chunk],
        })

    _save_state(state)
    logger.info(f"state written to {STATE_FILE}")


# ---------- Status --------------------------------------------------------


def cmd_status(_args):
    client = anthropic.Anthropic()
    state = _load_state()
    if not state["batches"]:
        print("no batches tracked")
        return
    for b in state["batches"]:
        try:
            live = client.messages.batches.retrieve(b["batch_id"])
        except Exception as e:
            print(f"{b['batch_id']}: retrieve failed: {e}")
            continue
        rc = live.request_counts
        print(
            f"{b['batch_id']}  status={live.processing_status:>11}  "
            f"collected={b['collected']}  "
            f"requests={b['request_count']}  "
            f"succeeded={rc.succeeded}  errored={rc.errored}  "
            f"expired={rc.expired}  processing={rc.processing}  "
            f"canceled={rc.canceled}"
        )


# ---------- Collect -------------------------------------------------------


def cmd_collect(args):
    client = anthropic.Anthropic()
    state = _load_state()
    if not state["batches"]:
        logger.info("no batches tracked")
        return

    conn = db.get_connection()
    total_saved = 0
    total_errors: dict = {}
    class_counts: dict = {}

    for b in state["batches"]:
        if b.get("collected") and not args.recollect:
            continue
        try:
            live = client.messages.batches.retrieve(b["batch_id"])
        except Exception as e:
            logger.warning(f"{b['batch_id']}: retrieve failed: {e}")
            continue
        if live.processing_status != "ended":
            logger.info(f"{b['batch_id']}: still {live.processing_status} — skipping")
            continue

        logger.info(f"{b['batch_id']}: collecting results")
        saved = 0
        for item in client.messages.batches.results(b["batch_id"]):
            custom_id = item.custom_id
            try:
                tbl, lid_str = custom_id.rsplit("_", 1)
                lid = int(lid_str)
            except (ValueError, AttributeError):
                total_errors["bad_custom_id"] = total_errors.get("bad_custom_id", 0) + 1
                continue
            if tbl not in {"sales", "rentals"}:
                total_errors["bad_table"] = total_errors.get("bad_table", 0) + 1
                continue

            result_type = item.result.type
            if result_type != "succeeded":
                total_errors[result_type] = total_errors.get(result_type, 0) + 1
                continue

            msg = item.result.message
            raw = "".join(
                block.text for block in msg.content
                if getattr(block, "type", None) == "text"
            )
            parsed = parse_classifier_raw_text(raw)
            if parsed.error:
                kind = parsed.error.split(":", 1)[0].strip()
                total_errors[kind] = total_errors.get(kind, 0) + 1
                continue

            class_counts[parsed.renovation_class] = (
                class_counts.get(parsed.renovation_class, 0) + 1
            )
            save_result(conn, lid, parsed, table=tbl)
            saved += 1
            total_saved += 1

        logger.info(f"{b['batch_id']}: saved {saved} classifications")
        b["collected"] = True
        b["collected_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    _save_state(state)
    conn.close()
    logger.info(f"collect done: saved={total_saved} "
                f"classes={class_counts} errors={total_errors}")


# ---------- Cleanup -------------------------------------------------------


def cmd_forget(args):
    """Drop collected batches from the state file."""
    state = _load_state()
    before = len(state["batches"])
    state["batches"] = [b for b in state["batches"] if not b.get("collected")]
    _save_state(state)
    logger.info(f"kept {len(state['batches'])} of {before} batches")


# ---------- CLI -----------------------------------------------------------


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sub = sub.add_parser("submit", help="Build + create a batch")
    p_sub.add_argument("--listing-type", choices=["sales", "rentals"], default=None)
    p_sub.add_argument("--limit", type=int, default=None)
    p_sub.add_argument("--skip-if-staged", action="store_true",
                       help="Skip listings whose building_stage is already "
                            "confidently set from text.")
    p_sub.set_defaults(func=cmd_submit)

    p_stat = sub.add_parser("status", help="Show state of tracked batches")
    p_stat.set_defaults(func=cmd_status)

    p_col = sub.add_parser("collect", help="Persist results of completed batches")
    p_col.add_argument("--recollect", action="store_true",
                       help="Re-collect batches already marked collected")
    p_col.set_defaults(func=cmd_collect)

    p_for = sub.add_parser("forget", help="Drop collected batches from state file")
    p_for.set_defaults(func=cmd_forget)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
