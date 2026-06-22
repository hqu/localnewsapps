from __future__ import annotations

import csv
import argparse
import json
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


DEFAULT_SOURCE_URL = "https://www.cambridgeday.com/2026/06/11/events-this-week-in-camberville-wax-museum-fresh-pond-day-soccer-watch-parties/"
LOCAL_TZ = ZoneInfo("America/New_York")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; local research scraper; +https://www.cambridgeday.com/)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

PRICE_RE = re.compile(
    r"^(?P<price>(?:Free|Museum admission rates apply|\$\d[^.]*?)(?:\s+(?:but|and|to|or|,|[0-9A-Za-z+.-]+))*[^.]*?)\.\s*(?P<description>.*)$",
    re.IGNORECASE,
)


@dataclass
class EventRow:
    Date: str = ""
    Day_of_the_week: str = ""
    Event_title: str = ""
    Venue: str = ""
    Location: str = ""
    start_time: str = ""
    end_time: str = ""
    description: str = ""
    price: str = ""
    URL_event: str = ""
    URL_venue: str = ""
    URL_address: str = ""
    URL_image: str = ""


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip(" ,")


def safe_name(index: int, title: str, url: str) -> str:
    host = urlparse(url).netloc.replace("www.", "")
    stem = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "event"
    return f"{index:02d}_{stem}_{host}.html"


def output_dir_for_url(source_url: str) -> Path:
    parsed = urlparse(source_url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) >= 4 and all(part.isdigit() for part in parts[:3]):
        year, month, day = parts[:3]
        slug = parts[3]
        return Path("outputs") / f"camberville_events_{year}_{month}_{day}_{slug[:45]}"
    slug = re.sub(r"[^a-z0-9]+", "-", parsed.path.lower()).strip("-")[:60] or "events"
    return Path("outputs") / f"camberville_events_{slug}"


def normalize_event_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.endswith("d.docs.live.net") and "ambridgema.gov/" in parsed.path:
        corrected_path = parsed.path.split("ambridgema.gov/", 1)[1]
        return f"https://www.cambridgema.gov/{corrected_path}"
    return url


def year_for_url(source_url: str) -> str:
    parts = [part for part in urlparse(source_url).path.strip("/").split("/") if part]
    if parts and re.fullmatch(r"\d{4}", parts[0]):
        return parts[0]
    return "2026"


def fetch(session: requests.Session, url: str) -> str:
    response = session.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    response.raise_for_status()
    return response.text


def fetch_event_page(session: requests.Session, url: str, title: str) -> tuple[str, str]:
    try:
        return fetch(session, url), ""
    except requests.RequestException as exc:
        message = clean(str(exc))
        html = (
            "<!doctype html><html><head>"
            f"<title>Download failed: {title}</title>"
            "</head><body>"
            f"<h1>{title}</h1>"
            f"<p>Original URL: <a href=\"{url}\">{url}</a></p>"
            f"<p>Download failed: {message}</p>"
            "</body></html>"
        )
        return html, message


def save_html(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def meaningful_children(tag: Tag) -> list[Tag | NavigableString]:
    return [
        child
        for child in tag.children
        if not (isinstance(child, NavigableString) and not clean(str(child)))
    ]


def first_link_is_event_title(p: Tag) -> Tag | None:
    children = meaningful_children(p)
    if not children or not isinstance(children[0], Tag) or children[0].name != "a":
        return None
    link = children[0]
    href = link.get("href", "")
    if not href or any(domain in href for domain in ("maps.google", "google.com/maps")):
        return None
    text_after_link = clean(p.get_text(" ", strip=True).replace(link.get_text(" ", strip=True), "", 1))
    if PRICE_RE.match(text_after_link) or text_after_link.lower().startswith(("free", "$", "museum admission")):
        return link
    return None


def split_price_description(text: str) -> tuple[str, str]:
    text = clean(text)
    if "." in text and text.lower().startswith(("free", "$", "museum admission rates apply")):
        price, description = text.split(".", 1)
        return clean(price), clean(description)
    match = PRICE_RE.match(text)
    if match:
        return clean(match.group("price")), clean(match.group("description"))
    return "", text


def split_time_range(time_text: str) -> tuple[str, str]:
    text = clean(time_text)
    if not text:
        return "", ""
    text = re.sub(r"^Starting at\s+", "", text, flags=re.IGNORECASE)
    if " to " not in text:
        and_match = re.match(r"^(\d{1,2}(?::\d{2})?)\s+and\s+\d{1,2}(?::\d{2})?\s+(a\.m\.|p\.m\.)$", text, re.IGNORECASE)
        if and_match:
            return f"{and_match.group(1)} {and_match.group(2)}", ""
        return text, ""
    start, end = [clean(part) for part in text.split(" to ", 1)]
    meridiem_match = re.search(r"(a\.m\.|p\.m\.)", end, re.IGNORECASE)
    if (
        meridiem_match
        and not re.search(r"(a\.m\.|p\.m\.)", start, re.IGNORECASE)
        and start.lower() not in {"noon", "midnight"}
    ):
        start = f"{start} {meridiem_match.group(1)}"
    return start, end


def time_to_24h(time_text: str) -> str:
    text = clean(time_text).lower()
    if not text:
        return ""
    text = re.sub(r"^starting at\s+", "", text, flags=re.IGNORECASE)
    if text == "noon":
        return "12:00"
    if text == "midnight":
        return "00:00"
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


def leading_time(p: Tag) -> str:
    strong = p.find("strong")
    if strong:
        text = clean(strong.get_text(" ", strip=True))
        if re.search(r"(?:a\.m\.|p\.m\.|Noon)", text, re.IGNORECASE):
            return text
    text = clean(p.get_text(" ", strip=True))
    match = re.match(
        r"^((?:Starting at\s+)?(?:Noon|Midnight|\d{1,2}(?::\d{2})?(?:\s+(?:and|to)\s+\d{1,2}(?::\d{2})?)?\s+(?:a\.m\.|p\.m\.)))",
        text,
        re.IGNORECASE,
    )
    if match:
        return clean(match.group(1))
    return ""


def venue_text_without_time(p: Tag) -> str:
    text = clean(p.get_text(" ", strip=True))
    time_text = leading_time(p)
    if time_text and text.startswith(time_text):
        return clean(text[len(time_text) :])
    return text


def parse_venue_line(p: Tag | None) -> tuple[str, str, str, str]:
    if p is None:
        return "", "", "", ""
    links = p.find_all("a", href=True)
    address_pattern = re.compile(
        r"^\d+|(?:\b(?:st|street|ave|avenue|road|rd|square|sq|parkway|pkwy|drive|dr|place|pl)\b\.?)",
        re.IGNORECASE,
    )
    address_links = [
        a
        for a in links
        if address_pattern.search(clean(a.get_text(" ", strip=True)))
        or "maps.google" in a["href"]
        or "/maps/" in a["href"]
    ]
    venue_link = next((a for a in links if a not in address_links), None)
    address_link = address_links[0] if address_links else None

    text = venue_text_without_time(p)
    venue = clean(venue_link.get_text(" ", strip=True)) if venue_link else ""
    url_venue = venue_link["href"] if venue_link else ""
    url_address = address_link["href"] if address_link else ""

    if address_link:
        address = clean(address_link.get_text(" ", strip=True))
        location = clean("/".join(clean(a.get_text(" ", strip=True)) for a in address_links))
        if not venue:
            before_address = clean(text.split(address, 1)[0])
            if before_address.lower().endswith(" at"):
                before_address = before_address[:-3]
            venue = before_address.strip(" ,")
    else:
        location = text
        venue = text.split(",", 1)[0].strip()

    return venue, location, url_venue, url_address


def extract_jsonld_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for script in soup.find_all("script", type=lambda value: value and "ld+json" in value):
        raw = script.string or script.get_text()
        if not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        stack = parsed if isinstance(parsed, list) else [parsed]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                objects.append(item)
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(item, list):
                stack.extend(item)
    return objects


def event_like_jsonld(soup: BeautifulSoup) -> dict[str, Any] | None:
    for obj in extract_jsonld_objects(soup):
        typ = obj.get("@type")
        types = typ if isinstance(typ, list) else [typ]
        if any(str(t).lower() == "event" for t in types):
            return obj
    return None


def image_from_page(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    event_obj = event_like_jsonld(soup)
    image = event_obj.get("image") if event_obj else None
    if isinstance(image, list):
        image = image[0] if image else ""
    if isinstance(image, dict):
        image = image.get("url") or image.get("@id")
    if image:
        return urljoin(base_url, str(image))

    for selector, attr in [
        ('meta[property="og:image"]', "content"),
        ('meta[name="twitter:image"]', "content"),
        ('link[rel="image_src"]', "href"),
    ]:
        tag = soup.select_one(selector)
        if tag and tag.get(attr):
            return urljoin(base_url, tag[attr])
    img = soup.find("img", src=True)
    return urljoin(base_url, img["src"]) if img else ""


def apply_landing_page_metadata(row: EventRow, html: str, base_url: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    event_obj = event_like_jsonld(soup)
    if event_obj:
        description = event_obj.get("description")
        if description and len(clean(str(description))) > len(row.description):
            row.description = clean(BeautifulSoup(str(description), "html.parser").get_text(" ", strip=True))
        offers = event_obj.get("offers")
        if not row.price and isinstance(offers, dict):
            price = offers.get("price")
            currency = offers.get("priceCurrency", "")
            row.price = clean(f"{currency} {price}") if price else row.price
    row.URL_image = image_from_page(html, base_url)


def parse_source_article(html: str, source_url: str) -> list[EventRow]:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("article .entry-content") or soup.select_one("article") or soup.body
    rows: list[EventRow] = []
    current_date = ""
    pending_time = ""
    pending_venue: Tag | None = None
    pending_title: tuple[str, str] | None = None

    def append_row(title: str, url_event: str, price_description_text: str) -> None:
        price, description = split_price_description(price_description_text)
        venue, location, url_venue, url_address = parse_venue_line(pending_venue)
        start_time, end_time = split_time_range(pending_time)
        date_only = clean(current_date.split(",", 1)[1]) if "," in current_date else current_date
        start_time_24h = time_to_24h(start_time)
        end_time_24h = time_to_24h(end_time)
        rows.append(
            EventRow(
                Date=date_only,
                Day_of_the_week=clean(current_date.split(",", 1)[0]) if "," in current_date else "",
                Event_title=title,
                Venue=venue,
                Location=location,
                start_time=start_time_24h,
                end_time=end_time_24h,
                description=description,
                price=price,
                URL_event=url_event,
                URL_venue=url_venue,
                URL_address=url_address,
            )
        )

    for node in content.find_all(["h2", "p"], recursive=True):
        if node.name == "h2":
            date_text = clean(node.get_text(" ", strip=True))
            if date_text.lower().startswith("support"):
                break
            if re.match(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),", date_text):
                current_date = f"{date_text}, {year_for_url(source_url)}"
            pending_time = ""
            pending_venue = None
            pending_title = None
            continue

        p_text = clean(node.get_text(" ", strip=True))
        if not p_text:
            continue
        if pending_title and p_text.lower().startswith(("free", "$", "museum admission")):
            append_row(pending_title[0], pending_title[1], p_text)
            pending_title = None
            pending_venue = None
            continue

        title_link = first_link_is_event_title(node)
        if title_link and current_date:
            title = clean(title_link.get_text(" ", strip=True))
            url_event = normalize_event_url(urljoin(source_url, title_link["href"]))
            after_title = clean(p_text.replace(title, "", 1))
            append_row(title, url_event, after_title)
            pending_venue = None
            continue

        links = node.find_all("a", href=True)
        if links and current_date and pending_time:
            first_link = links[0]
            href = first_link.get("href", "")
            link_text = clean(first_link.get_text(" ", strip=True))
            without_link = clean(p_text.replace(link_text, "", 1))
            if (
                href
                and link_text
                and not without_link
                and not any(domain in href for domain in ("maps.google", "google.com/maps"))
            ):
                pending_title = (link_text, normalize_event_url(urljoin(source_url, href)))
                continue

        if current_date and leading_time(node):
            pending_time = leading_time(node)
            pending_venue = node
            pending_title = None
        elif current_date and pending_time:
            pending_venue = node

    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Cambridge Day event roundup links into CSV.")
    parser.add_argument("source_url", nargs="?", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_url = args.source_url
    out_dir = args.out_dir or output_dir_for_url(source_url)
    html_dir = out_dir / "downloaded_html"
    csv_path = out_dir / "camberville_events.csv"

    session = requests.Session()
    out_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)

    source_html = fetch(session, source_url)
    save_html(html_dir / "00_source_cambridge_day.html", source_html)

    rows = parse_source_article(source_html, source_url)
    failures: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        html, error = fetch_event_page(session, row.URL_event, row.Event_title)
        if error:
            failures.append({"title": row.Event_title, "url": row.URL_event, "error": error})
        filename = safe_name(index, row.Event_title, row.URL_event)
        save_html(html_dir / filename, html)
        if not error:
            apply_landing_page_metadata(row, html, row.URL_event)
        time.sleep(0.35)

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
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            data = asdict(row)
            price_fields = parse_price_fields(data["price"])
            writer.writerow(
                {
                    "Date": data["Date"],
                    "Day of the week": data["Day_of_the_week"],
                    "Event title": data["Event_title"],
                    "Venue": data["Venue"],
                    "Location": data["Location"],
                    "start time": data["start_time"],
                    "end time": data["end_time"],
                    "startdatetime": unix_startdatetime(data["Date"], data["start_time"]),
                    "description": data["description"],
                    "price_dollar": price_fields["price_dollar"],
                    "price range": price_fields["price range"],
                    "RSVP": price_fields["RSVP"],
                    "minimum age": price_fields["minimum age"],
                    "URL event": data["URL_event"],
                    "URL venue": data["URL_venue"],
                    "URL address": data["URL_address"],
                    "URL image": data["URL_image"],
                }
            )

    manifest = {
        "source_url": source_url,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "events": len(rows),
        "download_failures": failures,
        "csv": str(csv_path),
        "html_dir": str(html_dir),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
