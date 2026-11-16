import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://eja.tv/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://eja.tv/"
}

CHANNEL_ID_RE = re.compile(r"^([a-f0-9]{32})(?:\.m3u8)?$", re.IGNORECASE)

# Common aliases so friendly names in config.json always map to eja.tv's exact query codes
ALIASES = {
    "country": {
        "gb": "uk",
        "united kingdom": "uk",
        "uk": "uk",
        "india": "in",
        "united states": "us",
        "usa": "us",
        "canada": "ca",
        "australia": "au"
    },
    "language": {
        "english": "eng",
        "hindi": "hin",
        "telugu": "tel",
        "tamil": "tam",
        "spanish": "spa",
        "french": "fra",
        "german": "deu",
        "arabic": "ara",
        "russian": "rus",
        "portuguese": "por",
        "italian": "ita",
        "japanese": "jpn",
        "korean": "kor",
        "chinese": "zho",
        "bengali": "ben",
        "marathi": "mar",
        "gujarati": "guj",
        "punjabi": "pan",
        "malayalam": "mal",
        "kannada": "kan",
        "urdu": "urd",
        "bhojpuri": "bho"
    },
    "category": {
        "movies": "Movie",
        "movie": "Movie"
    }
}


def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def safe_filename(name):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_").lower()
    return cleaned or "general"


def discover_site_filters():
    """Scrapes all country, language, and category filter links directly from eja.tv."""
    options = {"country": {}, "language": {}, "category": {}}
    try:
        resp = requests.get(BASE_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for a_tag in soup.find_all("a", href=True):
            parsed = urlparse(urljoin(BASE_URL, a_tag["href"]))
            qs = parse_qs(parsed.query)
            label = a_tag.get_text(" ", strip=True)
            if not label:
                continue
            for field in ("country", "language", "category"):
                if field in qs and qs[field]:
                    val = qs[field][0].strip()
                    if val:
                        options[field][val.lower()] = (val, label)
                        options[field][label.lower()] = (val, label)

        # Also check <select> elements if present
        for field in ("country", "language", "category"):
            select = soup.find("select", {"name": field})
            if select:
                for opt in select.find_all("option"):
                    val = (opt.get("value") or "").strip()
                    label = opt.get_text(strip=True)
                    if val and label:
                        options[field][val.lower()] = (val, label)
                        options[field][label.lower()] = (val, label)
    except Exception as e:
        print(f"[WARN] Could not auto-discover filters from homepage: {e}")

    return options


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


def extract_card_metadata(a_tag, default_name):
    """Extracts channel name, logo, country, and language list from the channel's listing card."""
    img = a_tag.find("img")
    logo = urljoin(BASE_URL, img["src"]) if img and img.get("src") else ""

    name = (
        a_tag.get_text(" ", strip=True)
        or a_tag.get("title")
        or (img.get("alt") if img else "")
        or default_name
    ).strip()

    country = ""
    langs = []

    # Inspect immediate card container for embedded country/language tags
    container = a_tag.find_parent(["div", "li", "article", "tr", "p"])
    if container:
        # Ensure container only wraps this single channel link
        ch_links_in_box = [
            link for link in container.find_all("a", href=True)
            if CHANNEL_ID_RE.match(urlparse(urljoin(BASE_URL, link["href"])).query)
        ]
        if len(ch_links_in_box) == 1:
            for link in container.find_all("a", href=True):
                q = parse_qs(urlparse(urljoin(BASE_URL, link["href"])).query)
                txt = link.get_text(" ", strip=True)
                if "country" in q and txt:
                    country = txt
                elif "language" in q and txt and txt not in langs:
                    langs.append(txt)

    return name.replace(",", " "), logo, country, ", ".join(langs)


def crawl_query_pages(max_pages=25, country="", language="", category="", search=""):
    """Crawls all paginated pages (offset=0, 18, 36...) for a given filter/search on eja.tv."""
    found_channels = {}
    offset = 0
    page_num = 0

    while True:
        if max_pages > 0 and page_num >= max_pages:
            break

        params = {
            "offset": offset,
            "country": country,
            "language": language,
            "category": category,
            "search": search
        }
        try:
            resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"[WARN] Request failed at offset={offset} ({params}): {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        new_in_page = 0

        for a_tag in soup.find_all("a", href=True):
            full_url = urljoin(BASE_URL, a_tag["href"].strip())
            parsed = urlparse(full_url)
            if "eja.tv" not in parsed.netloc.lower():
                continue

            match = CHANNEL_ID_RE.match(parsed.query)
            if not match:
                continue

            ch_id = match.group(1).lower()
            if ch_id not in found_channels:
                name, logo, card_country, card_langs = extract_card_metadata(a_tag, f"Channel {ch_id[:6]}")
                found_channels[ch_id] = {
                    "id": ch_id,
                    "name": name,
                    "watch_url": f"{BASE_URL}?{ch_id}",
                    "direct_m3u8_hint": f"{BASE_URL}?{ch_id}.m3u8",
                    "logo": logo,
                    "country": card_country,
                    "language": card_langs,
                    "category": category
                }
                new_in_page += 1
            else:
                # Enrich name if a later anchor for the same ID has cleaner text
                candidate_text = a_tag.get_text(" ", strip=True).replace(",", " ")
                if candidate_text and found_channels[ch_id]["name"].startswith("Channel "):
                    found_channels[ch_id]["name"] = candidate_text

        if new_in_page == 0:
            break

        # Detect next offset from pagination links if present, otherwise advance by items found (typically 18)
        next_offsets = []
        for a_tag in soup.find_all("a", href=True):
            q = parse_qs(urlparse(urljoin(BASE_URL, a_tag["href"])).query)
            if "offset" in q:
                try:
                    val = int(q["offset"][0])
                    if val > offset:
                        next_offsets.append(val)
                except ValueError:
                    pass

        offset = min(next_offsets) if next_offsets else (offset + max(new_in_page, 18))
        page_num += 1

    return list(found_channels.values())


def resolve_stream(channel, verify=True):
    """Resolves a 32-hex eja.tv channel ID into a direct, verified .m3u8 stream URL."""
    ch_id = channel["id"]
    m3u8_endpoint = f"{BASE_URL}?{ch_id}.m3u8"
    watch_url = f"{BASE_URL}?{ch_id}"

    # 1. Try eja.tv's direct .m3u8 redirect endpoint first
    try:
        resp = requests.get(m3u8_endpoint, headers=HEADERS, timeout=10, allow_redirects=True, stream=True)
        final_url = resp.url
        content_type = (resp.headers.get("Content-Type") or "").lower()
        first_chunk = next(resp.iter_content(chunk_size=512), b"").decode("utf-8", errors="ignore")
        resp.close()

        if resp.status_code == 200 and ("#EXTM3U" in first_chunk or (not verify and (".m3u8" in final_url.lower() or "mpegurl" in content_type))):
            return final_url, channel["name"]
    except Exception:
        pass

    # 2. Fallback: parse the channel player HTML page for embedded .m3u8 URL & <title>
    try:
        page = requests.get(watch_url, headers=HEADERS, timeout=10)
        if page.status_code == 200:
            soup = BeautifulSoup(page.text, "html.parser")
            title_tag = soup.find("title")
            clean_name = channel["name"]
            if title_tag and title_tag.get_text(strip=True):
                t_text = title_tag.get_text(strip=True).replace(",", " ")
                if t_text.lower() not in ("eja.tv", "home"):
                    clean_name = t_text

            matches = re.findall(r'https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*', page.text)
            if matches:
                stream_url = matches[0]
                if not verify:
                    return stream_url, clean_name
                chk = requests.get(stream_url, headers=HEADERS, timeout=8, stream=True)
                chunk = next(chk.iter_content(chunk_size=512), b"").decode("utf-8", errors="ignore")
                chk.close()
                if chk.status_code == 200 and "#EXTM3U" in chunk:
                    return stream_url, clean_name
    except Exception:
        pass

    return None, channel["name"]


def write_m3u(filepath, channels):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    lines = ["#EXTM3U"]
    seen_ids = set()
    for ch in sorted(channels, key=lambda x: x["name"].lower()):
        if ch["id"] in seen_ids:
            continue
        seen_ids.add(ch["id"])
        group = ch.get("category") or ch.get("country") or "General"
        lang = ch.get("language") or ""
        country = ch.get("country") or ""
        logo = ch.get("logo") or ""
        name = ch["name"]
        extinf = (
            f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-name="{name}" tvg-logo="{logo}" '
            f'tvg-country="{country}" tvg-language="{lang}" group-title="{group}",{name}'
        )
        lines.append(extinf)
        lines.append(ch["stream_url"])

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def build_readme(generated_files, total_working):
    repo = os.environ.get("GITHUB_REPOSITORY", "Tarke4618/iptv")
    raw_base = f"https://raw.githubusercontent.com/{repo}/main"

    md = [
        "# Auto-Updating eja.tv IPTV Playlists (.m3u)",
        "",
        "Automatically refreshed every **12 hours** via GitHub Actions. Copy any **Raw M3U URL** below and paste it into your IPTV player (TiviMate, OTT Navigator, Televizo, Sparkle TV, VLC, Kodi).",
        "",
        "### Master Playlist (All Verified Channels)",
        f"- **All Channels** ({total_working} channels): `{raw_base}/playlists/all.m3u`",
        ""
    ]

    for section, items in generated_files.items():
        if not items:
            continue
        md.append(f"### By {section}")
        for label, rel_path, count in sorted(items, key=lambda x: x[0].lower()):
            md.append(f"- **{label}** ({count} channels): `{raw_base}/{rel_path}`")
        md.append("")

    with open("README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))


def main():
    config = load_config()
    max_pages = config.get("max_pages_per_query", 25)
    verify = config.get("verify_streams", True)
    scrape_all = config.get("scrape_all_filters", False)

    print("Discovering available countries, languages, and categories from eja.tv...")
    discovered = discover_site_filters()

    tasks = [("general", "Global Top", {"max_pages": max_pages})]

    if scrape_all:
        for val, label in sorted(set(discovered["country"].values()), key=lambda x: x[1]):
            tasks.append(("countries", label, {"max_pages": max_pages, "country": val}))
        for val, label in sorted(set(discovered["language"].values()), key=lambda x: x[1]):
            tasks.append(("languages", label, {"max_pages": max_pages, "language": val}))
        for val, label in sorted(set(discovered["category"].values()), key=lambda x: x[1]):
            tasks.append(("categories", label, {"max_pages": max_pages, "category": val}))
    else:
        for c in config.get("countries", []):
            val, label = resolve_filter_param("country", c, discovered)
            tasks.append(("countries", label, {"max_pages": max_pages, "country": val}))
        for l in config.get("languages", []):
            val, label = resolve_filter_param("language", l, discovered)
            tasks.append(("languages", label, {"max_pages": max_pages, "language": val}))
        for cat in config.get("categories", []):
            val, label = resolve_filter_param("category", cat, discovered)
            tasks.append(("categories", label, {"max_pages": max_pages, "category": val}))

    for term in config.get("custom_searches", []):
        tasks.append(("searches", term.title(), {"max_pages": max_pages, "search": term}))

    registry = {}
    for bucket, label, q_kwargs in tasks:
        print(f"Crawling [{bucket}] -> {label} ({q_kwargs})...")
        items = crawl_query_pages(**q_kwargs)
        print(f"  Found {len(items)} channel entries for {label}.")
        for item in items:
            ch_id = item["id"]
            if ch_id not in registry:
                registry[ch_id] = item
                registry[ch_id]["buckets"] = []
            else:
                if not registry[ch_id]["country"] and item["country"]:
                    registry[ch_id]["country"] = item["country"]
                if not registry[ch_id]["language"] and item["language"]:
                    registry[ch_id]["language"] = item["language"]
                if not registry[ch_id]["category"] and item["category"]:
                    registry[ch_id]["category"] = item["category"]
                if not registry[ch_id]["logo"] and item["logo"]:
                    registry[ch_id]["logo"] = item["logo"]

            registry[ch_id]["buckets"].append((bucket, label))
            if bucket == "countries" and not registry[ch_id]["country"]:
                registry[ch_id]["country"] = label
            elif bucket == "languages" and not registry[ch_id]["language"]:
                registry[ch_id]["language"] = label
            elif bucket == "categories" and not registry[ch_id]["category"]:
                registry[ch_id]["category"] = label

    print(f"Resolving & verifying {len(registry)} unique candidate channels...")
    working_channels = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        future_map = {
            pool.submit(resolve_stream, data, verify): data
            for data in registry.values()
        }
        for future in as_completed(future_map):
            data = future_map[future]
            stream_url, resolved_name = future.result()
            if stream_url:
                data["stream_url"] = stream_url
                data["name"] = resolved_name
                working_channels.append(data)

    print(f"Verified {len(working_channels)} working live streams.")

    write_m3u("playlists/all.m3u", working_channels)

    grouped = {"countries": {}, "languages": {}, "categories": {}, "searches": {}}
    for ch in working_channels:
        for bucket, label in ch["buckets"]:
            if bucket in grouped:
                grouped[bucket].setdefault(label, []).append(ch)

    generated_summary = {"Country": [], "Language": [], "Category": [], "Custom Search": []}
    label_map = {
        "countries": "Country",
        "languages": "Language",
        "categories": "Category",
        "searches": "Custom Search"
    }

    for bucket, label_dict in grouped.items():
        for label, ch_list in label_dict.items():
            fname = f"playlists/{bucket}/{safe_filename(label)}.m3u"
            write_m3u(fname, ch_list)
            generated_summary[label_map[bucket]].append((label, fname, len(ch_list)))

    build_readme(generated_summary, len(working_channels))
    print("All playlists and README dashboard updated successfully.")


if __name__ == "__main__":
    main()
