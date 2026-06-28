"""
picker.py — Random winner selection logic.

Supports:
  - Optional keyword filtering (e.g. only pick comments that contain "@")
  - Deduplication by username (one entry per user)
  - Returns the full comment object of the winner
"""

import random
from typing import Optional


def pick_winner(
    comments: list[dict],
    filter_keyword: Optional[str] = None,
    deduplicate: bool = True,
) -> Optional[dict]:
    """
    Pick a random winner from a list of comment dicts.

    Args:
        comments: List of {"username": str, "text": str} dicts.
        filter_keyword: If provided, only consider comments that contain this
                        string (case-insensitive). Pass None or "" to skip.
        deduplicate: If True, only one comment per username is eligible
                     (the first one found).

    Returns:
        A single comment dict {"username": str, "text": str}, or None if
        no eligible entries exist.
    """
    pool: list[dict] = list(comments)  # copy

    # ── 1. Keyword filter ────────────────────────────────────────────────────
    if filter_keyword and filter_keyword.strip():
        kw = filter_keyword.strip().lower()
        pool = [c for c in pool if kw in c.get("text", "").lower()]

    # ── 2. Deduplicate by username (keep first occurrence) ───────────────────
    if deduplicate:
        seen: set[str] = set()
        deduped: list[dict] = []
        for c in pool:
            uname = c.get("username", "").lower()
            if uname not in seen:
                seen.add(uname)
                deduped.append(c)
        pool = deduped

    if not pool:
        return None

    return random.choice(pool)
