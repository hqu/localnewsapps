from __future__ import annotations

import csv
import re
from pathlib import Path


SRC = Path("outputs/camberville_events_2026_06_11/camberville_events_standardized_time.csv")
DST = Path("outputs/camberville_events_2026_06_11/camberville_events_standardized_time_price_split.csv")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def parse_price_fields(price_text: str) -> dict[str, str]:
    text = clean(price_text)
    lower = text.lower()
    fields = {
        "price_dollar": "",
        "price range": "",
        "RSVP": "",
        "minimum age": "",
    }
    if not text:
        return fields
    if lower.startswith("free"):
        fields["price_dollar"] = "0"
    if "rsvp" in lower or "register" in lower:
        fields["RSVP"] = "yes"
    age_match = re.search(r"(\d+)\s*-\s*plus", lower)
    if age_match:
        fields["minimum age"] = age_match.group(1)
    dollar_values = re.findall(r"\$(\d+(?:\.\d+)?)", text)
    if len(dollar_values) >= 2:
        fields["price range"] = f"{dollar_values[0]}-{dollar_values[1]}"
    elif len(dollar_values) == 1:
        fields["price_dollar"] = dollar_values[0]
    return fields


def main() -> None:
    rows = list(csv.DictReader(SRC.open(encoding="utf-8-sig")))
    fields = [
        "Date",
        "Day of the week",
        "Event title",
        "Venue",
        "Location",
        "start time",
        "end time",
        "startdatetime",
        "description",
        "price_dollar",
        "price range",
        "RSVP",
        "minimum age",
        "URL event",
        "URL venue",
        "URL address",
        "URL image",
    ]
    output_rows = []
    for row in rows:
        price_fields = parse_price_fields(row.get("price", ""))
        output_row = {field: row.get(field, "") for field in fields}
        output_row.update(price_fields)
        output_rows.append(output_row)

    with DST.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)

    print(DST.resolve())
    print(len(output_rows))


if __name__ == "__main__":
    main()
