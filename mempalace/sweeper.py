#!/usr/bin/env python3
"""
sweeper.py — Tandem miner that guarantees no conversation is silently
dropped.

Works alongside miner.py / convo_miner.py via timestamp coordination:

    For each session in the transcript dir:
      cursor = max(timestamp of drawers with matching session_id, "")
      For each user/assistant message in the jsonl with timestamp > cursor:
        write one small drawer (message_uuid as deterministic ID)

Properties:
  - Idempotent: rerunning on a fully-mined palace is a no-op.
  - Resume-safe: crash mid-sweep → next run picks up from max-timestamp.
  - Coordinates with primary miners for free: whichever got further
    advances the cursor; the other starts from there next time.
  - No size caps: each drawer holds one exchange, ~1-5 KB.

Usage:
    from mempalace.sweeper import sweep
    result = sweep("/path/to/session.jsonl", "/path/to/palace")
    # result: {"drawers_added": N, "drawers_skipped": M, "cursor": ts}
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .palace import get_collection

logger = logging.getLogger(__name__)


# ── JSONL parsing ────────────────────────────────────────────────────


def _flatten_content(content) -> str:
    """Normalize Claude Code's message content to a plain string.

    User messages are strings already; assistant messages are a list of
    content blocks like [{"type": "text", "text": "..."}, {"type":
    "tool_use", ...}]. All blocks are preserved verbatim — the design
    principle is "verbatim always", so tool inputs and results are
    serialized in full, never truncated.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                parts.append(
                    f"[tool_use: {block.get('name', '?')} "
                    f"input={json.dumps(block.get('input', {}), default=str)}]"
                )
            elif btype == "tool_result":
                parts.append(f"[tool_result: {json.dumps(block.get('content', ''), default=str)}]")
            else:
                parts.append(f"[{btype}: {json.dumps(block, default=str)}]")
        return "\n".join(p for p in parts if p)
    return str(content)


def parse_claude_jsonl(path: str) -> Iterator[dict]:
    """Yield user/assistant records from a Claude Code .jsonl file.

    Each yield is:
        {
          "session_id": str,
          "uuid":       str,   # per-message UUID
          "timestamp":  str,   # ISO 8601
          "role":       "user" | "assistant",
          "content":    str,   # flattened text
        }

    Non-message records (progress, file-history-snapshot, system,
    queue-operation, last-prompt) are filtered out. Malformed lines are
    skipped silently — data quality is the transcript writer's problem,
    not ours.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            rtype = record.get("type")
            if rtype not in ("user", "assistant"):
                continue
            msg = record.get("message") or {}
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            timestamp = record.get("timestamp")
            if not timestamp:
                continue
            uuid = record.get("uuid")
            if not uuid:
                continue
            session_id = record.get("sessionId") or record.get("session_id")
            if not session_id:
                continue
            content = _flatten_content(msg.get("content", ""))
            if not content.strip():
                continue
            yield {
                "session_id": session_id,
                "uuid": uuid,
                "timestamp": timestamp,
                "role": role,
                "content": content,
            }


# ── Cursor resolution ────────────────────────────────────────────────


def get_palace_cursor(collection, session_id: str) -> Optional[str]:
    """Return the max timestamp of drawers for this session_id, or None.

    ISO-8601 strings compare lexically in the right order, so we don't
    need to parse them. Query scans metadatas for the session via the
    backend's where-filter, then reduces.

    Backend errors are logged at WARNING and surface as a `None` cursor —
    which makes the caller treat the session as empty and ingest every
    message. That's intentional: a no-cursor sweep is recovered from on
    the next run by deterministic drawer IDs, so a degraded cursor never
    causes silent data loss.
    """
    try:
        data = collection.get(
            where={"session_id": session_id},
            include=["metadatas"],
        )
    except Exception as exc:
        logger.warning(
            "sweeper: cursor lookup failed for session_id=%s (%s); "
            "treating as empty — drawers will be re-upserted idempotently.",
            session_id,
            exc,
        )
        return None
    metas = data.get("metadatas") or []
    timestamps = [m.get("timestamp") for m in metas if m and m.get("timestamp")]
    if not timestamps:
        return None
    return max(timestamps)


# ── Sweep ────────────────────────────────────────────────────────────


def _drawer_id_for_message(session_id: str, message_uuid: str) -> str:
    """Deterministic drawer ID so upserts at the same message are no-ops.

    Uses the full session_id (not a prefix) to avoid any cross-session
    collision risk if a transcript source ever uses non-UUID session
    identifiers or shares prefixes across sessions.
    """
    return f"sweep_{session_id}_{message_uuid}"


def sweep(jsonl_path: str, palace_path: str, source_label: Optional[str] = None) -> dict:
    """Ingest every user/assistant message not already represented.

    For each message in the jsonl:
      - If timestamp <= cursor for that session, skip (already saved by
        us or by primary miner).
      - Else, upsert a drawer with deterministic ID so reruns dedupe.

    Returns a summary dict: {drawers_added, drawers_skipped, cursor_by_session}.
    """
    collection = get_collection(palace_path, create=True)
    cursors: dict = {}

    drawers_added = 0
    drawers_skipped = 0

    batch_ids = []
    batch_docs = []
    batch_metas = []
    BATCH_SIZE = 64

    def _flush():
        nonlocal drawers_added
        if not batch_ids:
            return
        collection.upsert(
            ids=batch_ids,
            documents=batch_docs,
            metadatas=batch_metas,
        )
        drawers_added += len(batch_ids)
        batch_ids.clear()
        batch_docs.clear()
        batch_metas.clear()

    for rec in parse_claude_jsonl(jsonl_path):
        sid = rec["session_id"]
        if sid not in cursors:
            cursors[sid] = get_palace_cursor(collection, sid)

        cursor = cursors[sid]
        if cursor is not None and rec["timestamp"] <= cursor:
            drawers_skipped += 1
            continue

        drawer_id = _drawer_id_for_message(sid, rec["uuid"])
        document = f"{rec['role'].upper()}: {rec['content']}"
        metadata = {
            "session_id": sid,
            "timestamp": rec["timestamp"],
            "message_uuid": rec["uuid"],
            "role": rec["role"],
            "source_file": source_label or jsonl_path,
            "filed_at": datetime.now().isoformat(),
            "ingest_mode": "sweep",
        }

        batch_ids.append(drawer_id)
        batch_docs.append(document)
        batch_metas.append(metadata)

        if len(batch_ids) >= BATCH_SIZE:
            _flush()

    _flush()

    return {
        "drawers_added": drawers_added,
        "drawers_skipped": drawers_skipped,
        "cursor_by_session": cursors,
    }


def sweep_directory(dir_path: str, palace_path: str) -> dict:
    """Sweep every .jsonl file in a directory (recursive).

    Returns aggregated summary across all files.
    """
    dir_p = Path(dir_path).expanduser().resolve()
    files = sorted(dir_p.rglob("*.jsonl"))

    total_added = 0
    total_skipped = 0
    per_file = []

    failures: list[dict] = []
    for f in files:
        try:
            result = sweep(str(f), palace_path, source_label=str(f))
        except Exception as exc:
            logger.error("sweeper: sweep failed on %s: %s", f, exc)
            print(f"  \u26a0 sweep failed on {f}: {exc}", file=sys.stderr)
            failures.append({"file": str(f), "error": str(exc)})
            continue
        total_added += result["drawers_added"]
        total_skipped += result["drawers_skipped"]
        per_file.append(
            {
                "file": str(f),
                "added": result["drawers_added"],
                "skipped": result["drawers_skipped"],
            }
        )

    return {
        "files_processed": len(per_file),
        "drawers_added": total_added,
        "drawers_skipped": total_skipped,
        "per_file": per_file,
        "failures": failures,
    }
