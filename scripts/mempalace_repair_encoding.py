#!/usr/bin/env python3
"""Repair legacy Windows mojibake in a MemPalace collection."""

from __future__ import annotations

import argparse

from mempalace.config import MempalaceConfig
from mempalace.encoding_repair import repair_collection
from mempalace.palace import get_collection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Detect and repair UTF-8 text that legacy Windows paths "
            "stored as Windows-1252 mojibake."
        )
    )
    parser.add_argument(
        "--palace",
        help="Palace path; defaults to the configured palace.",
    )
    parser.add_argument(
        "--collection",
        help="Collection name; defaults to the configured collection.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=500,
        help="Rows scanned per page (default: 500).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write repaired documents. Without this flag, run read-only.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = MempalaceConfig()

    palace_path = args.palace or config.palace_path
    collection_name = args.collection or getattr(config, "collection_name", "mempalace_drawers")

    collection = get_collection(
        palace_path,
        collection_name=collection_name,
        create=False,
    )

    report = repair_collection(
        collection,
        apply=args.apply,
        page_size=args.page_size,
    )

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"Mode: {mode}")
    print(f"Rows scanned: {report['scanned']}")
    print(f"Documents needing repair: {report['changed']}")
    print(f"Documents updated: {report['updated']}")

    if not args.apply and report["changed"]:
        print()
        print("Run again with --apply to write the repairs.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
