"""Локальная проверка детектора временной аренды."""

from __future__ import annotations

import sys

from services.lease_filter import is_temporary_lease
from services.parsers.base import ListingData

_CASES: list[tuple[str, ListingData, bool]] = [
    (
        "unbefristet Kleinanzeigen",
        ListingData(
            id="1",
            title="2-Zimmer-Wohnung in Lahr — unbefristet zu vermieten",
            price=750,
            size_sqm=62.0,
            rooms=2.0,
            location="77933 Lahr",
            url="https://example.test/1",
            image_url=None,
            source_platform="kleinanzeigen",
            description="Die Wohnung wird unbefristet vermietet. Warmmiete 750 €.",
        ),
        False,
    ),
    (
        "Zwischenmiete im Titel",
        ListingData(
            id="2",
            title="Zwischenmiete 3 Monate — 2 Zimmer Freiburg",
            price=650,
            size_sqm=55.0,
            rooms=2.0,
            location="Freiburg",
            url="https://example.test/2",
            image_url=None,
            source_platform="kleinanzeigen",
            description="Helle Wohnung, ab sofort.",
        ),
        True,
    ),
    (
        "befristet bis Immowelt",
        ListingData(
            id="3",
            title="Moderne Wohnung in Offenburg",
            price=820,
            size_sqm=70.0,
            rooms=2.5,
            location="77652 Offenburg",
            url="https://example.test/3",
            image_url=None,
            source_platform="immowelt",
            description="Die Miete ist befristet bis 31.08.2027.",
        ),
        True,
    ),
    (
        "auf Zeit / Untermiete",
        ListingData(
            id="4",
            title="Wohnung auf Zeit in Emmendingen",
            price=600,
            size_sqm=None,
            rooms=2.0,
            location="Emmendingen",
            url="https://example.test/4",
            image_url=None,
            source_platform="immowelt",
            description="Untermiete für 6 Monate möglich.",
        ),
        True,
    ),
    (
        "RU limited term text",
        ListingData(
            id="5",
            title="Квартира в Lahr",
            price=700,
            size_sqm=58.0,
            rooms=2.0,
            location="Lahr",
            url="https://example.test/5",
            image_url=None,
            source_platform="kleinanzeigen",
            description="Сдаётся на ограниченный срок, минимальный срок 1 месяц.",
        ),
        True,
    ),
    (
        "WG rent_type=1",
        ListingData(
            id="6",
            title="2-Zimmer in Lahr",
            price=700,
            size_sqm=60.0,
            rooms=2.0,
            location="Lahr",
            url="https://example.test/6",
            image_url=None,
            source_platform="wggesucht",
            description="",
            raw_data={
                "api": {
                    "rent_type": "1",
                    "available_from_date": "01.06.2026",
                    "available_to_date": "00.00.0000",
                }
            },
        ),
        True,
    ),
    (
        "WG available_to_date",
        ListingData(
            id="7",
            title="Wohnung in Kehl",
            price=680,
            size_sqm=55.0,
            rooms=2.0,
            location="Kehl",
            url="https://example.test/7",
            image_url=None,
            source_platform="wggesucht",
            description="",
            raw_data={
                "api": {
                    "rent_type": "0",
                    "available_from_date": "01.03.2026",
                    "available_to_date": "30.09.2026",
                }
            },
        ),
        True,
    ),
    (
        "WG unbefristet API",
        ListingData(
            id="8",
            title="Dauerhafte Vermietung",
            price=720,
            size_sqm=63.0,
            rooms=2.0,
            location="Lahr",
            url="https://example.test/8",
            image_url=None,
            source_platform="wggesucht",
            description="Langfristige Miete.",
            raw_data={
                "api": {
                    "rent_type": "0",
                    "available_from_date": "01.04.2026",
                    "available_to_date": "00.00.0000",
                }
            },
        ),
        False,
    ),
    (
        "nur für 4 Monate",
        ListingData(
            id="9",
            title="Möblierte Wohnung",
            price=900,
            size_sqm=48.0,
            rooms=1.0,
            location="Freiburg",
            url="https://example.test/9",
            image_url=None,
            source_platform="kleinanzeigen",
            description="Nur für 4 Monate, ideal für Praktikum.",
        ),
        True,
    ),
    (
        "ab bis Datum",
        ListingData(
            id="10",
            title="Kurzzeitmiete",
            price=500,
            size_sqm=40.0,
            rooms=1.0,
            location="Offenburg",
            url="https://example.test/10",
            image_url=None,
            source_platform="wggesucht",
            description="Vermietung ab 01.05.2026 bis 31.08.2026.",
        ),
        True,
    ),
]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    failed = 0
    for label, listing, expected in _CASES:
        is_temp, reason = is_temporary_lease(listing)
        ok = is_temp == expected
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {label}: temp={is_temp} reason={reason!r}")
        if not ok:
            failed += 1

    if failed:
        print(f"\n{failed} test(s) failed")
        sys.exit(1)
    print(f"\nAll {len(_CASES)} lease-filter checks passed")


if __name__ == "__main__":
    main()
