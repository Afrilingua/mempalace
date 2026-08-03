from mempalace.encoding_repair import (
    repair_collection,
    repair_mojibake,
)


def mojibake(text):
    return text.encode("utf-8").decode("cp1252")


def test_repairs_common_accented_text():
    assert repair_mojibake("cafÃ©") == "café"
    assert repair_mojibake("naÃ¯ve") == "naïve"


def test_repairs_dash_arrow_and_emoji():
    damaged = mojibake("Plan → result — ✅")
    assert repair_mojibake(damaged) == "Plan → result — ✅"


def test_preserves_clean_text():
    clean = "Already clean: café → ✅"
    assert repair_mojibake(clean) == clean


def test_repairs_mixed_clean_and_damaged_text():
    text = "Clean prefix, cafÃ©, clean suffix."
    assert repair_mojibake(text) == "Clean prefix, café, clean suffix."


def test_repairs_double_encoded_text():
    once = mojibake("café")
    twice = mojibake(once)

    assert repair_mojibake(twice) == "café"


def test_repair_is_idempotent():
    repaired = repair_mojibake("cafÃ© â†’ done")
    assert repair_mojibake(repaired) == repaired


class FakeCollection:
    def __init__(self, documents):
        self.ids = [f"drawer-{index}" for index in range(len(documents))]
        self.documents = list(documents)
        self.updates = []

    def get(self, *, limit, offset, include):
        del include
        end = offset + limit
        return {
            "ids": self.ids[offset:end],
            "documents": self.documents[offset:end],
        }

    def update(self, *, ids, documents):
        self.updates.append(
            {
                "ids": list(ids),
                "documents": list(documents),
            }
        )


def test_collection_dry_run_reports_without_writing():
    collection = FakeCollection(["cafÃ©", "plain", "arrow â†’"])

    report = repair_collection(
        collection,
        apply=False,
        page_size=2,
    )

    assert report == {
        "scanned": 3,
        "changed": 2,
        "updated": 0,
    }
    assert collection.updates == []


def test_collection_apply_updates_only_changed_documents():
    collection = FakeCollection(["cafÃ©", "plain", "arrow â†’"])

    report = repair_collection(
        collection,
        apply=True,
        page_size=2,
    )

    assert report == {
        "scanned": 3,
        "changed": 2,
        "updated": 2,
    }

    updated_ids = [drawer_id for batch in collection.updates for drawer_id in batch["ids"]]
    updated_documents = [
        document for batch in collection.updates for document in batch["documents"]
    ]

    assert updated_ids == ["drawer-0", "drawer-2"]
    assert updated_documents == ["café", "arrow →"]


def test_collection_rejects_invalid_page_size():
    collection = FakeCollection([])

    try:
        repair_collection(collection, page_size=0)
    except ValueError as exc:
        assert "page_size" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
