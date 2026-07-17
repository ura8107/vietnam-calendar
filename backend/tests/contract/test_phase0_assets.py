from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import feedparser
import jsonschema

from vietnam_calendar.infrastructure.feeds import normalize_tuoitre_rss_dates


PROJECT_ROOT = Path(__file__).parents[3]
IMPORTANCE_V1_SHA256 = "0dc949488ce98698f77e5b8f2ba6c99652aa64517c5645db7efd775a65da86c6"

# Independent review snapshot: intentionally not generated from JSONL or the
# requirements parser, so accidental label drift in any of the 57 rows fails.
EXPECTED_LABELS = {
    **{key: ("target", "high", False) for key in (
        "001", "003", "008", "011", "013", "015", "018", "020", "021", "023",
        "029", "033", "035", "037", "041", "044", "045", "047", "049", "051",
        "054", "056", "057",
    )},
    **{key: ("target", "middle", False) for key in (
        "002", "006", "007", "009", "012", "017", "026", "036", "043", "046",
    )},
    "010": ("target", "middle_high", False),
    **{key: ("target", "low", False) for key in (
        "014", "016", "019", "022", "024", "025", "027", "038", "039", "040",
        "042", "048", "050", "052", "055",
    )},
    "004": ("out_of_scope", None, False),
    "005": ("out_of_scope", None, False),
    **{key: ("target", "high", True) for key in ("028", "030", "031", "032", "034", "053")},
}


def test_importance_eval_has_every_id_exactly_once() -> None:
    dataset_bytes = (PROJECT_ROOT / "evals/importance-v1.jsonl").read_bytes()
    assert hashlib.sha256(dataset_bytes).hexdigest() == IMPORTANCE_V1_SHA256
    rows = [
        json.loads(line)
        for line in dataset_bytes.decode().splitlines()
        if line.strip()
    ]
    expected_ids = [f"IMP-{number:03d}" for number in range(1, 58)]
    schema = json.loads((PROJECT_ROOT / "evals/schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    for row in rows:
        validator.validate(row)
    assert [row["id"] for row in rows] == expected_ids
    assert all(
        set(row)
        == {
            "id",
            "scenario",
            "expected_relevance",
            "expected_importance",
            "must_include",
            "reason",
            "tags",
        }
        for row in rows
    )
    assert all(row["expected_importance"] is None for row in rows if row["expected_relevance"] == "out_of_scope")
    assert all(row["expected_importance"] is not None for row in rows if row["expected_relevance"] != "out_of_scope")
    assert {row["id"] for row in rows if row["must_include"]} == {
        "IMP-028",
        "IMP-030",
        "IMP-031",
        "IMP-032",
        "IMP-034",
        "IMP-053",
    }
    assert len(EXPECTED_LABELS) == 57
    actual_labels = {
        row["id"].removeprefix("IMP-"): (
            row["expected_relevance"], row["expected_importance"], row["must_include"]
        )
        for row in rows
    }
    assert actual_labels == EXPECTED_LABELS


def test_tuoitre_fixture_records_mapping_edge_cases_without_bulk_content() -> None:
    fixture = PROJECT_ROOT / "backend/tests/fixtures/feeds/tuoitre-home.xml"
    root = ElementTree.parse(fixture).getroot()
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("language") == "vi-vn"
    assert channel.findtext("ttl") == "20"
    item = channel.find("item")
    assert item is not None
    assert item.find("guid") is None
    assert item.findtext("pubDate").endswith("GMT+7")
    assert item.find("enclosure").attrib["url"].startswith("https://")


def test_tuoitre_fixture_parses_through_feedparser_bytes() -> None:
    fixture = PROJECT_ROOT / "backend/tests/fixtures/feeds/tuoitre-home.xml"
    raw = fixture.read_bytes()
    # Tuoi Tre emits the non-RFC token GMT+7. feedparser 6.0.12 preserves the
    # date but cannot parse it, so the feed adapter must normalize that token
    # before handing bytes to feedparser (and retain the original raw bytes).
    parser_bytes = normalize_tuoitre_rss_dates(raw)
    parsed = feedparser.parse(parser_bytes)
    assert parsed.bozo == 0
    assert parsed.feed.language == "vi-vn"
    assert parsed.feed.ttl == "20"
    entry = parsed.entries[0]
    assert "guid" not in entry and "id" not in entry
    assert entry.enclosures[0].href == "https://cdn.tuoitre.vn/sample-image.jpg"
    actual = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    assert actual == datetime(2025, 7, 16, 2, 30, tzinfo=timezone.utc)


def test_tuoitre_date_normalizer_is_narrow_and_preserves_raw_input() -> None:
    raw = b"<rss><pubDate>Wed, 16 Jul 2025 09:30:00  gMt + 7 </pubDate><title>GMT+7</title></rss>"
    normalized = normalize_tuoitre_rss_dates(raw)
    assert raw.endswith(b"</rss>")
    assert b"09:30:00 +0700 </pubDate>" in normalized
    assert b"<title>GMT+7</title>" in normalized


def test_tuoitre_date_normalizer_leaves_malformed_or_unscoped_text_alone() -> None:
    malformed = b"<pubDate>Wed GMT+7"
    description = b"<description>Meeting at GMT+7</description>"
    assert normalize_tuoitre_rss_dates(malformed) == malformed
    assert normalize_tuoitre_rss_dates(description) == description
    pub_to_build = b"<pubDate>Wed, 16 Jul 2025 09:30:00 GMT+7</lastBuildDate>"
    build_to_pub = b"<lastBuildDate>Wed, 16 Jul 2025 09:30:00 GMT+7</pubDate>"
    assert normalize_tuoitre_rss_dates(pub_to_build) == pub_to_build
    assert normalize_tuoitre_rss_dates(build_to_pub) == build_to_pub
