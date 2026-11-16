import base64
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, urljoin, urlparse
import requests
from bs4 import BeautifulSoup, NavigableString

BASE_URL = "https://eja.tv/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://eja.tv/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

FILTER_ONLY_KEYS = {"country", "language", "category", "search", "offset", "limit", "page", "sort", "order"}
STATIC_SLUGS = {"about", "help", "privacy", "terms", "contact", "faq", "login", "register", "dmca"}

# Base noise words that should never be treated as a TV channel's name
BASE_NOISE_WORDS = {
    "eja", "eja.tv", "home", "tv", "live", "live tv", "channel", "channels",
    "stream", "streams", "watch", "play", "player", "video", "hd", "sd", "fhd",
    "next", "previous", "prev", "more", "all", "search", "filter", "menu",
    "sign languages", "sign language", "unknown", "untitled", "index", "playlist", "master"
}

ALIASES = {
    "country": {
        "gb": "uk", "united kingdom": "uk", "uk": "uk",
        "india": "in", "united states": "us", "usa": "us",
        "canada": "ca", "australia": "au"
    },
    "language": {
        "english": "eng", "hindi": "hin", "telugu": "tel", "tamil": "tam",
        "spanish": "spa", "french": "fra", "german": "deu", "arabic": "ara",
        "russian": "rus", "portuguese": "por", "italian": "ita", "japanese": "jpn",
        "korean": "kor", "chinese": "zho", "bengali": "ben", "marathi": "mar",
        "gujarati": "guj", "punjabi": "pan", "malayalam": "mal", "kannada": "kan",
        "urdu": "urd", "bhojpuri": "bho"
    },
    "category": {
        "movies": "Movie", "movie": "Movie"
    }
}


def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def safe_filename(name):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_").lower()
    return cleaned or "general"


def clean_text(text):
    text = re.sub(r"\s+", " ", (text or "")).replace(",", " ").strip()
    # Strip site branding prefixes/suffixes like "eja.tv - " or " - eja.tv"
    text = re.sub(r"(?i)^eja\.tv\s*[-|:–—]+\s*|\s*[-|:–—]+\s*eja\.tv$", "", text).strip()
    return text


def is_valid_channel_name(name, noise_words):
    if not name:
        return False
    low = name.strip().lower()
    if len(low) < 2 or len(low) > 80:
        return False
    if low in noise_words:
        return False
    if low.startswith(("http://", "https://", "//", "www.")):
        return False
    if low.isdigit():
        return False
    if re.match(r"^(channel|stream|live)\s*\d*$", low):
        return False
    return True


def humanize_stream_url(stream_url):
    """Generates a readable channel title from an .m3u8 URL path if HTML lacks a text title."""
    parsed = urlparse(stream_url)
    parts = [p for p in parsed.path.split("/") if p]
    generic_parts = {
        "hls", "live", "stream", "streams", "index.m3u8", "playlist.m3u8",
        "master.m3u8", "chunklist.m3u8", "manifest.m3u8", "tv", "iptv", "out", "v1", "v2"
    }
    for part in reversed(parts):
        stem = re.sub(r"\.m3u8$", "", part, flags=re.IGNORECASE)
        if stem.lower() not in generic_parts and not stem.isdigit() and len(stem) >= 2:
            readable = re.sub(r"[_-]+", " ", stem).strip()
            return readable.upper() if len(readable) <= 4 else readable.title()

    host = parsed.netloc.split(":")[0]
    host_parts = [h for h in host.split(".") if h not in ("www", "live", "hls", "cdn", "stream", "tv", "com", "net", "org", "in", "io")]
    if host_parts:
        return host_parts[0].upper() if len(host_parts[0]) <= 4 else host_parts[0].title()
    return "Live TV"


def discover_site_filters():
    """Scrapes all country, language, and category labels from eja.tv and builds the noise filter."""
    options = {"country": {}, "language": {}, "category": {}}
    noise_words = set(BASE_NOISE_WORDS)

    try:
        resp = requests.get(BASE_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for a_tag in soup.find_all("a", href=True):
            parsed = urlparse(urljoin(BASE_URL, a_tag["href"]))
            qs = parse_qs(parsed.query)
            label = clean_text(a_tag.get_text(" ", strip=True))
            if not label:
                continue
            for field in ("country", "language", "category"):
                if field in qs and qs[field]:
                    val = qs[field][0].strip()
                    if val:
                        options[field][val.lower()] = (val, label)
                        options[field][label.lower()] = (val, label)
                        noise_words.add(label.lower())
                        noise_words.add(val.lower())

        for field in ("country", "language", "category"):
            select = soup.find("select", {"name": field})
            if select:
                for opt in select.find_all("option"):
                    val = (opt.get("value") or "").strip()
                    label = clean_text(opt.get_text(strip=True))
                    if val and label:
                        options[field][val.lower()] = (val, label)
                        options[field][label.lower()] = (val, label)
                        noise_words.add(label.lower())
                        noise_words.add(val.lower())
    except Exception as e:
        print(f"[WARN] Filter discovery warning: {e}")

    for field_aliases in ALIASES.values():
        for k, v in field_aliases.items():
            noise_words.add(k.lower())
            noise_words.add(v.lower())

    return options, noise_words


def resolve_filter_param(field, raw_input, discovered):
    key = raw_input.strip().lower()
    if key in discovered[field]:
        return discovered[field][key]
    if key in ALIASES[field]:
        mapped_val = ALIASES[field][key]
        if mapped_val.lower() in discovered[field]:
            return discovered[field][mapped_val.lower()]
        return (mapped_val, raw_input.strip().title() if len(raw_input) > 3 else raw_input.strip().upper())
    return (raw_input.strip(), raw_input.strip())


def is_filter_or_nav_url(parsed_url):
    path = parsed_url.path.strip("/").lower()
    if path in STATIC_SLUGS:
        return True
    if path.endswith((".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp")):
