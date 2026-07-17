"""Narrow compatibility normalization for Tuoi Tre RSS date elements."""

from __future__ import annotations

import re


_DATE_ELEMENT = re.compile(
    rb"(<(?P<tag>pubDate|lastBuildDate)>)([^<]*?)[ \t]+GMT\s*\+\s*7([ \t]*)(</(?P=tag)>)",
    flags=re.IGNORECASE,
)


def normalize_tuoitre_rss_dates(raw: bytes) -> bytes:
    """Return parser bytes with GMT+7 normalized only inside RSS date fields.

    The caller remains responsible for retaining ``raw`` unchanged. Malformed
    XML and GMT+7 text outside supported date fields are deliberately untouched.
    """

    return _DATE_ELEMENT.sub(rb"\1\3 +0700\4\5", raw)
