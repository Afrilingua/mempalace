"""Repair legacy UTF-8 text that was decoded as Windows-1252."""

from __future__ import annotations


def _byte_for_character(character: str) -> int | None:
    try:
        encoded = character.encode("cp1252")
    except UnicodeEncodeError:
        codepoint = ord(character)
        if codepoint <= 255:
            return codepoint
        return None

    if len(encoded) != 1:
        return None

    return encoded[0]


def _decode_candidate(
    text: str,
    start: int,
) -> tuple[str, int] | None:
    for width in (4, 3, 2):
        segment = text[start : start + width]
        if len(segment) != width:
            continue

        raw_values = []
        for character in segment:
            value = _byte_for_character(character)
            if value is None:
                break
            raw_values.append(value)
        else:
            raw = bytes(raw_values)
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue

            if len(decoded) == 1 and ord(decoded) >= 128:
                return decoded, width

    return None


def repair_mojibake_once(text: str) -> str:
    """Repair one layer of UTF-8-as-Windows-1252 mojibake."""
    output: list[str] = []
    index = 0

    while index < len(text):
        candidate = _decode_candidate(text, index)
        if candidate is None:
            output.append(text[index])
            index += 1
            continue

        decoded, width = candidate
        output.append(decoded)
        index += width

    return "".join(output)


def repair_mojibake(
    text: str,
    *,
    max_passes: int = 3,
) -> str:
    """Repair repeated mojibake layers until stable."""
    current = text

    for _ in range(max_passes):
        repaired = repair_mojibake_once(current)
        if repaired == current:
            break
        current = repaired

    return current


def _result_field(result, name: str):
    if isinstance(result, dict):
        return result.get(name)
    return getattr(result, name, None)


def repair_collection(
    collection,
    *,
    apply: bool = False,
    page_size: int = 500,
) -> dict[str, int]:
    """Scan a collection and optionally update damaged documents."""
    if page_size < 1:
        raise ValueError("page_size must be at least 1")

    scanned = 0
    changed = 0
    updated = 0
    offset = 0

    while True:
        page = collection.get(
            limit=page_size,
            offset=offset,
            include=["documents"],
        )
        ids = list(_result_field(page, "ids") or [])
        documents = list(_result_field(page, "documents") or [])

        if not ids:
            break

        update_ids = []
        update_documents = []

        for drawer_id, document in zip(ids, documents):
            scanned += 1

            if not isinstance(document, str):
                continue

            repaired = repair_mojibake(document)
            if repaired == document:
                continue

            changed += 1
            update_ids.append(drawer_id)
            update_documents.append(repaired)

        if apply and update_ids:
            collection.update(
                ids=update_ids,
                documents=update_documents,
            )
            updated += len(update_ids)

        offset += len(ids)

        if len(ids) < page_size:
            break

    return {
        "scanned": scanned,
        "changed": changed,
        "updated": updated,
    }
