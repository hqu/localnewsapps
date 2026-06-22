from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SRC = Path("outputs/camberville_events_2026_06_11/camberville_events_date_time_split.csv")
DST = Path("outputs/camberville_events_2026_06_11/camberville_events_standardized_time.csv")
LOCAL_TZ = ZoneInfo("America/New_York")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def time_to_24h(time_text: str) -> str:
    text = clean(time_text).lower()
    if not text:
        return ""
    if text == "noon":
        return "12:00"
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(a\.m\.|p\.m\.)$", text)
    if not match:
        return clean(time_text)
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = match.group(3)
    if meridiem == "a.m." and hour == 12:
        hour = 0
    elif meridiem == "p.m." and hour != 12:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def unix_startdatetime(date_text: str, start_time_24h: str) -> str:
    if not date_text or not re.match(r"^\d{2}:\d{2}$", start_time_24h):
        return ""
    dt = datetime.strptime(f"{date_text} {start_time_24h}", "%B %d, %Y %H:%M")
    return str(int(dt.replace(tzinfo=LOCAL_TZ).timestamp()))


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
        "price",
        "URL event",
        "URL venue",
        "URL address",
        "URL image",
    ]
    output_rows = []
    for row in rows:
        start_time = time_to_24h(row.get("start time", ""))
        end_time = time_to_24h(row.get("end time", ""))
        output_row = {field: row.get(field, "") for field in fields}
        output_row["start time"] = start_time
        output_row["end time"] = end_time
        output_row["startdatetime"] = unix_startdatetime(row.get("Date", ""), start_time)
        output_rows.append(output_row)

    with DST.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)

    print(DST.resolve())
    print(len(output_rows))


if __name__ == "__main__":
    main()
