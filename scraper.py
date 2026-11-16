import base64
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
    "Referer": "https://eja.tv/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

FILTER_ONLY_KEYS = {"country", "language", "category", "search", "offset", "limit", "page", "sort", "order"}
STATIC_SLUGS = {"about", "help", "privacy", "terms", "contact", "faq", "login", "register", "dmca"}

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
        "urdu": "urd"
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


def clean_text(text):
    return re.sub(r"\s+", " ", (text or "")).replace(",", " ").strip()


def discover_site_filters():
    """Scrapes live country, language, and category filter values from eja.tv."""
    options = {"country": {}, "language": {}, "category": {}}
    try:
        resp = requests.get(BASE_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        all_links = soup.find_all("a", href=True)
        m3u8_in_home = re.findall(r'https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*', resp.text)
        print(f"[DIAG] Homepage loaded ({len(resp.text)} bytes, {len(all_links)} links, {len(m3u8_in_home)} inline .m3u8 URLs)")

        for a_tag in all_links:
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

        for field in ("country", "language", "category"):
            select = soup.find("select", {"name": field})
            if select:
                for opt in select.find_all("option"):
                    val = (opt.get("value") or "").strip()
                    label = clean_text(opt.get_text(strip=True))
                    if val and label:
                        options[field][val.lower()] = (val, label)
                        options[field][label.lower()] = (val, label)
    except Exception as e:
        print(f"[WARN] Filter discovery error: {e}")

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


def extract_m3u8_urls(html_text, base_url=BASE_URL):
    """Finds direct, protocol-relative, or base64-encoded .m3u8 URLs in HTML/JS."""
    urls = []
    seen = set()

    # 1. Standard http(s)://...m3u8... URLs
    for m in re.findall(r'https?://[^\s"\'<>\\()]+?\.m3u8[^\s"\'<>\\()]*', html_text, re.IGNORECASE):
        u = m.replace("\\/", "/").strip()
        if u not in seen:
            seen.add(u)
            urls.append(u)

    # 2. Protocol-relative //...m3u8... URLs
    for m in re.findall(r'(?<![a-zA-Z0-9:])//[a-zA-Z0-9.-]+/[^\s"\'<>\\()]+?\.m3u8[^\s"\'<>\\()]*', html_text, re.IGNORECASE):
        u = "https:" + m.replace("\\/", "/").strip()
        if u not in seen:
            seen.add(u)
            urls.append(u)

    # 3. Base64-encoded stream URLs inside atob("...")
    for b64 in re.findall(r'atob\(\s*["\']([A-Za-z0-9+/=]{16,})["\']\s*\)', html_text):
        try:
            decoded = base64.b64decode(b64).decode("utf-8", errors="ignore").strip()
            if ".m3u8" in decoded.lower() and decoded.startswith("http") and decoded not in seen:
                seen.add(decoded)
                urls.append(decoded)
        except Exception:
            pass

    return urls


def is_filter_or_nav_url(parsed_url):
    """Returns True if an eja.tv link is just a filter button, pagination link, or static page."""
    path = parsed_url.path.strip("/").lower()
    if path in STATIC_SLUGS:
        return True
    if path.endswith((".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp")):
        return True

    qs = parse_qs(parsed_url.query, keep_blank_values=True)
    if not path and not qs:
        return True

    # If the URL has a non-empty path (e.g. /watch/123 or /tv/abc), it's a channel page, not a filter
    if path:
        return False

    # Root path `/` with query parameters:
    # Filter links ONLY use keys from FILTER_ONLY_KEYS (e.g., ?country=in, ?language=hin, ?offset=18)
    non_filter_keys = [k for k in qs.keys() if k.lower() not in FILTER_ONLY_KEYS]
    return len(non_filter_keys) == 0


def parse_listing_page(html_text, page_url, default_category=""):
    """Extracts both direct .m3u8 streams and channel watch links from a listing page."""
    soup = BeautifulSoup(html_text, "html.parser")
    entries = []
    seen_keys = set()

    # Pass A: Inspect all <a> tags for either direct .m3u8 links or channel watch pages
    for a_tag in soup.find_all("a", href=True):
        raw_href = a_tag["href"].strip()
        if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:")):
            continue

        full_url = urljoin(page_url, raw_href)
        parsed = urlparse(full_url)
        img = a_tag.find("img")
        logo = urljoin(page_url, img["src"]) if img and img.get("src") else ""

        # Extract channel name from text, title, alt, or parent heading
        name = clean_text(
            a_tag.get_text(" ", strip=True)
            or a_tag.get("title")
            or (img.get("alt") if img else "")
        )

        # Case A1: Anchor links directly to an .m3u8 stream
        if ".m3u8" in full_url.lower() and "eja.tv" not in parsed.netloc.lower():
            if full_url not in seen_keys:
                seen_keys.add(full_url)
                entries.append({
                    "key": full_url,
                    "name": name or "Live Stream",
                    "stream_url": full_url,
                    "watch_url": "",
                    "logo": logo,
                    "country": "",
                    "language": "",
                    "category": default_category
                })
            continue

        # Case A2: Internal eja.tv link that is a channel player page (not a filter button)
        if "eja.tv" in parsed.netloc.lower() and not is_filter_or_nav_url(parsed):
            if full_url not in seen_keys:
                seen_keys.add(full_url)
                entries.append({
                    "key": full_url,
                    "name": name or "Channel",
                    "stream_url": "",
                    "watch_url": full_url,
                    "logo": logo,
                    "country": "",
                    "language": "",
                    "category": default_category
                })

    # Pass B: Extract any inline .m3u8 URLs embedded in cards/scripts on the listing page
    inline_streams = extract_m3u8_urls(html_text, page_url)
    for idx, s_url in enumerate(inline_streams):
        if s_url in seen_keys:
            continue
        seen_keys.add(s_url)

        # Attempt to find a nearby title/name in the surrounding HTML/JS snippet
        pos = html_text.find(s_url)
        guessed_name = ""
        if pos != -1:
            window = html_text[max(0, pos - 350):min(len(html_text), pos + 350)]
            title_match = re.search(
                r'(?:title|name|channel|tvg-name)\s*[:=]\s*["\']([^"\'<>]{2,60})["\']',
                window,
                re.IGNORECASE
            )
            if title_match:
                guessed_name = clean_text(title_match.group(1))
            else:
                tag_text = re.findall(r'>([^<>]{2,50})<', window)
                for t in reversed(tag_text):
                    cleaned = clean_text(t)
                    if cleaned and not cleaned.startswith("http") and len(cleaned) > 1:
                        guessed_name = cleaned
                        break

        entries.append({
            "key": s_url,
            "name": guessed_name or f"Stream {idx + 1}",
            "stream_url": s_url,
            "watch_url": "",
            "logo": "",
            "country": "",
            "language": "",
            "category": default_category
        })

    # Detect next pagination offset/page from links on the page
    next_offsets = []
    next_pages = []
    for a_tag in soup.find_all("a", href=True):
        q = parse_qs(urljoin(page_url, a_tag["href"]))
        # Parse query string safely
        q = parse_qs(urlparse(urljoin(page_url, a_tag["href"])).query)
        if "offset" in q:
            try:
                next_offsets.append(int(q["offset"][0]))
            except ValueError:
                pass
        if "page" in q:
            try:
                next_pages.append(int(q["page"][0]))
            except ValueError:
                pass

    return entries, sorted(set(next_offsets)), sorted(set(next_pages))


def crawl_query_pages(max_pages=25, country="", language="", category="", search=""):
    """Crawls all paginated pages for a filter or search query on eja.tv."""
    collected = {}
    offset = 0
    page = 1
    use_page_param = False
    page_num = 0

    while True:
        if max_pages > 0 and page_num >= max_pages:
            break

        params = {}
        if country:
            params["country"] = country
        if language:
            params["language"] = language
        if category:
            params["category"] = category
        if search:
            params["search"] = search

        if use_page_param:
            if page > 1:
                params["page"] = page
        else:
            if offset > 0:
                params["offset"] = offset

        try:
            resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"[WARN] Failed page {page_num + 1} ({params}): {e}")
            break

        entries, next_offsets, next_pages = parse_listing_page(resp.text, resp.url, default_category=category)
        new_count = 0
        for item in entries:
            if item["key"] not in collected:
                collected[item["key"]] = item
                new_count += 1

        if new_count == 0:
            break

        page_num += 1

        # Advance pagination using the site's actual offset or page parameter
        higher_offsets = [o for o in next_offsets if o > offset]
        higher_pages = [p for p in next_pages if p > page]

        if higher_offsets:
            offset = higher_offsets[0]
        elif higher_pages:
            use_page_param = True
            page = higher_pages[0]
        else:
            # Fallback step increment if links use JS/relative offsets
            offset += max(new_count, 18)

    return list(collected.values())


def verify_m3u8_url(stream_url):
    """Checks that a stream URL responds with valid HLS playlist content."""
    try:
        resp = requests.get(stream_url, headers=HEADERS, timeout=8, allow_redirects=True, stream=True)
        chunk = next(resp.iter_content(chunk_size=1024), b"").decode("utf-8", errors="ignore")
        resp.close()
        if resp.status_code == 200 and ("#EXTM3U" in chunk or "#EXT-X-" in chunk):
            return resp.url
    except Exception:
        pass
    return None


def resolve_entry(entry, verify=True):
    """Resolves and verifies an entry into a working .m3u8 URL and clean channel name."""
    name = entry["name"]

    # Case 1: Direct .m3u8 URL already extracted from listing page
    if entry.get("stream_url"):
        s_url = entry["stream_url"]
        if not verify:
            return s_url, name
        verified = verify_m3u8_url(s_url)
        return verified, name

    # Case 2: Watch page URL on eja.tv
    watch_url = entry.get("watch_url")
    if not watch_url:
        return None, name

    try:
        resp = requests.get(watch_url, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return None, name

        content_type = (resp.headers.get("Content-Type") or "").lower()
        if ".m3u8" in resp.url.lower() or "mpegurl" in content_type or "#EXTM3U" in resp.text[:200]:
            return resp.url, name

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag_name in ("h1", "h2", "title"):
            tag = soup.find(tag_name)
            if tag:
                t_clean = clean_text(tag.get_text(" ", strip=True))
                if t_clean and t_clean.lower() not in ("eja.tv", "home", "tv"):
                    name = t_clean
                    break

        candidates = extract_m3u8_urls(resp.text, resp.url)
        for s_url in candidates:
            if not verify:
                return s_url, name
            verified = verify_m3u8_url(s_url)
            if verified:
                return verified, name
    except Exception:
        pass

    return None, name


def write_m3u(filepath, channels):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    lines = ["#EXTM3U"]
    seen_streams = set()
    for ch in sorted(channels, key=lambda x: x["name"].lower()):
        s_url = ch["stream_url"]
        if s_url in seen_streams:
            continue
        seen_streams.add(s_url)

        group = ch.get("category") or ch.get("country") or "General"
        lang = ch.get("language") or ""
        country = ch.get("country") or ""
        logo = ch.get("logo") or ""
        name = ch["name"]

        extinf = (
            f'#EXTINF:-1 tvg-name="{name}" tvg-logo="{logo}" '
            f'tvg-country="{country}" tvg-language="{lang}" group-title="{group}",{name}'
        )
        lines.append(extinf)
        lines.append(s_url)

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

    print("Discovering filters from eja.tv...")
    discovered = discover_site_filters()

    tasks = [("general", "Global Feed", {"max_pages": max_pages})]

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
        print(f"Crawling [{bucket}] -> {label}...")
        items = crawl_query_pages(**q_kwargs)
        print(f"  Found {len(items)} candidate items.")
        for item in items:
            k = item["key"]
            if k not in registry:
                registry[k] = item
                registry[k]["buckets"] = []
            registry[k]["buckets"].append((bucket, label))
            if bucket == "countries" and not registry[k]["country"]:
                registry[k]["country"] = label
            elif bucket == "languages" and not registry[k]["language"]:
                registry[k]["language"] = label
            elif bucket == "categories" and not registry[k]["category"]:
                registry[k]["category"] = label

    print(f"Resolving & verifying {len(registry)} unique candidates...")
    working_by_stream = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        future_map = {
            pool.submit(resolve_entry, data, verify): data
            for data in registry.values()
        }
        for future in as_completed(future_map):
            data = future_map[future]
            stream_url, resolved_name = future.result()
            if stream_url:
                if stream_url not in working_by_stream:
                    data["stream_url"] = stream_url
                    data["name"] = resolved_name
                    working_by_stream[stream_url] = data
                else:
                    working_by_stream[stream_url]["buckets"].extend(data["buckets"])

    working_channels = list(working_by_stream.values())
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
    print("Done!")


if __name__ == "__main__":
    main()
