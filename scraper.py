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
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://eja.tv/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

FILTER_ONLY_KEYS = {
    "country", "language", "category", "search",
    "offset", "limit", "page", "sort", "order",
}
STATIC_SLUGS = {
    "about", "help", "privacy", "terms", "contact",
    "faq", "login", "register", "dmca",
}
STATIC_EXTS = (
    ".css", ".js", ".png", ".jpg", ".jpeg",
    ".gif", ".svg", ".ico", ".webp",
)

BASE_NOISE_WORDS = {
    "eja", "eja.tv", "home", "tv", "live", "live tv",
    "channel", "channels", "stream", "streams", "watch",
    "play", "player", "video", "hd", "sd", "fhd",
    "next", "previous", "prev", "more", "all",
    "search", "filter", "menu", "sign languages",
    "sign language", "unknown", "untitled", "index",
    "playlist", "master",
}

ALIASES = {
    "country": {
        "gb": "uk", "united kingdom": "uk", "uk": "uk",
        "india": "in", "united states": "us", "usa": "us",
        "canada": "ca", "australia": "au",
    },
    "language": {
        "english": "eng", "hindi": "hin", "telugu": "tel",
        "tamil": "tam", "spanish": "spa", "french": "fra",
        "german": "deu", "arabic": "ara", "russian": "rus",
        "portuguese": "por", "italian": "ita", "japanese": "jpn",
        "korean": "kor", "chinese": "zho", "bengali": "ben",
        "marathi": "mar", "gujarati": "guj", "punjabi": "pan",
        "malayalam": "mal", "kannada": "kan", "urdu": "urd",
        "bhojpuri": "bho",
    },
    "category": {
        "movies": "Movie",
        "movie": "Movie",
    },
}


def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def safe_filename(name):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip())
    return cleaned.strip("_").lower() or "general"


def clean_text(text):
    text = re.sub(r"\s+", " ", (text or "")).replace(",", " ").strip()
    text = re.sub(
        r"(?i)^eja\.tv\s*[-|:–—]+\s*|\s*[-|:–—]+\s*eja\.tv$",
        "",
        text,
    )
    return text.strip()


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
    parsed = urlparse(stream_url)
    parts = [p for p in parsed.path.split("/") if p]
    generic = {
        "hls", "live", "stream", "streams", "index.m3u8",
        "playlist.m3u8", "master.m3u8", "chunklist.m3u8",
        "manifest.m3u8", "tv", "iptv", "out", "v1", "v2",
    }
    for part in reversed(parts):
        stem = re.sub(r"\.m3u8$", "", part, flags=re.IGNORECASE)
        if stem.lower() not in generic and not stem.isdigit() and len(stem) >= 2:
            readable = re.sub(r"[_-]+", " ", stem).strip()
            return readable.upper() if len(readable) <= 4 else readable.title()

    host = parsed.netloc.split(":")[0]
    skip_hosts = {
        "www", "live", "hls", "cdn", "stream",
        "tv", "com", "net", "org", "in", "io",
    }
    host_parts = [h for h in host.split(".") if h not in skip_hosts]
    if host_parts:
        first = host_parts[0]
        return first.upper() if len(first) <= 4 else first.title()
    return "Live TV"


def discover_site_filters():
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
        mapped = ALIASES[field][key]
        if mapped.lower() in discovered[field]:
            return discovered[field][mapped.lower()]
        label = (
            raw_input.strip().title()
            if len(raw_input) > 3
            else raw_input.strip().upper()
        )
        return (mapped, label)
    return (raw_input.strip(), raw_input.strip())


def is_filter_or_nav_url(parsed_url):
    path = parsed_url.path.strip("/").lower()
    if path in STATIC_SLUGS:
        return True
    if path.endswith(STATIC_EXTS):
        return True

    qs = parse_qs(parsed_url.query, keep_blank_values=True)
    if not path and not qs:
        return True
    if path:
        return False

    non_filter_keys = [
        k for k in qs.keys() if k.lower() not in FILTER_ONLY_KEYS
    ]
    return len(non_filter_keys) == 0


def is_channel_anchor(a_tag, page_url):
    href = (a_tag.get("href") or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:")):
        return False
    full_url = urljoin(page_url, href)
    parsed = urlparse(full_url)
    if ".m3u8" in full_url.lower():
        return True
    is_eja = "eja.tv" in parsed.netloc.lower()
    return is_eja and not is_filter_or_nav_url(parsed)


def extract_card_details(a_tag, page_url, noise_words):
    img = a_tag.find("img")
    logo = urljoin(page_url, img["src"]) if img and img.get("src") else ""

    candidates = []
    if a_tag.get("title"):
        candidates.append(clean_text(a_tag["title"]))
    if img and img.get("alt"):
        candidates.append(clean_text(img["alt"]))
    if img and img.get("title"):
        candidates.append(clean_text(img["title"]))

    own_text = clean_text(a_tag.get_text(" ", strip=True))
    if own_text:
        candidates.append(own_text)

    card_country = ""
    card_langs = []

    card_box = None
    parent = a_tag.parent
    for _ in range(4):
        if not parent or parent.name in ("body", "html", "[document]"):
            break
        ch_links = [
            lnk for lnk in parent.find_all("a", href=True)
            if is_channel_anchor(lnk, page_url)
        ]
        if len(ch_links) == 1:
            card_box = parent
        else:
            break
        parent = parent.parent

    if card_box:
        if not logo:
            box_img = card_box.find("img")
            if box_img and box_img.get("src"):
                logo = urljoin(page_url, box_img["src"])
                if box_img.get("alt"):
                    candidates.append(clean_text(box_img["alt"]))

        for lnk in card_box.find_all("a", href=True):
            q = parse_qs(urlparse(urljoin(page_url, lnk["href"])).query)
            badge_txt = clean_text(lnk.get_text(" ", strip=True))
            if "country" in q and badge_txt:
                card_country = badge_txt
            elif "language" in q and badge_txt and badge_txt not in card_langs:
                card_langs.append(badge_txt)

        for text_node in card_box.find_all(string=True):
            if not isinstance(text_node, NavigableString):
                continue
            if text_node.parent and text_node.parent.name in ("script", "style"):
                continue
            inside_filter = False
            for anc in text_node.parents:
                if anc == card_box:
                    break
                if anc.name == "a" and anc.get("href"):
                    anc_parsed = urlparse(urljoin(page_url, anc["href"]))
                    if is_filter_or_nav_url(anc_parsed):
                        inside_filter = True
                        break
            if not inside_filter:
                cleaned = clean_text(str(text_node))
                if cleaned:
                    candidates.append(cleaned)

    best_name = ""
    for cand in candidates:
        if is_valid_channel_name(cand, noise_words):
            best_name = cand
            break

    return best_name, logo, card_country, ", ".join(card_langs)


def extract_m3u8_urls(html_text):
    urls = []
    seen = set()

    pat1 = r'https?://[^\s"\'<>\\()]+?\.m3u8[^\s"\'<>\\()]*'
    for m in re.findall(pat1, html_text, re.IGNORECASE):
        u = m.replace("\\/", "/").strip()
        if u not in seen:
            seen.add(u)
            urls.append(u)

    pat2 = r'(?<![a-zA-Z0-9:])//[a-zA-Z0-9.-]+/[^\s"\'<>\\()]+?\.m3u8[^\s"\'<>\\()]*'
    for m in re.findall(pat2, html_text, re.IGNORECASE):
        u = "https:" + m.replace("\\/", "/").strip()
        if u not in seen:
            seen.add(u)
            urls.append(u)

    pat3 = r'atob\(\s*["\']([A-Za-z0-9+/=]{16,})["\']\s*\)'
    for b64 in re.findall(pat3, html_text):
        try:
            decoded = base64.b64decode(b64).decode("utf-8", errors="ignore").strip()
            if ".m3u8" in decoded.lower() and decoded.startswith("http"):
                if decoded not in seen:
                    seen.add(decoded)
                    urls.append(decoded)
        except Exception:
            pass

    return urls


def parse_listing_page(html_text, page_url, noise_words, default_category=""):
    soup = BeautifulSoup(html_text, "html.parser")
    entries = []
    seen_keys = set()

    for a_tag in soup.find_all("a", href=True):
        if not is_channel_anchor(a_tag, page_url):
            continue

        full_url = urljoin(page_url, a_tag["href"].strip())
        if full_url in seen_keys:
            continue
        seen_keys.add(full_url)

        name, logo, card_country, card_langs = extract_card_details(
            a_tag, page_url, noise_words
        )
        host = urlparse(full_url).netloc.lower()
        is_direct = ".m3u8" in full_url.lower() and "eja.tv" not in host

        entries.append({
            "key": full_url,
            "name": name or (humanize_stream_url(full_url) if is_direct else ""),
            "stream_url": full_url if is_direct else "",
            "watch_url": "" if is_direct else full_url,
            "logo": logo,
            "country": card_country,
            "language": card_langs,
            "category": default_category,
        })

    for s_url in extract_m3u8_urls(html_text):
        if s_url in seen_keys:
            continue
        seen_keys.add(s_url)

        pos = html_text.find(s_url)
        guessed_name = ""
        if pos != -1:
            start = max(0, pos - 400)
            end = min(len(html_text), pos + 400)
            window = html_text[start:end]
            attr_pat = r'(?:title|name|channel|tvg-name|alt)\s*[:=]\s*["\']([^"\'<>]{2,70})["\']'
            for attr_match in re.findall(attr_pat, window, re.IGNORECASE):
                cand = clean_text(attr_match)
                if is_valid_channel_name(cand, noise_words):
                    guessed_name = cand
                    break

            if not guessed_name:
                frag = BeautifulSoup(window, "html.parser")
                for txt in frag.find_all(string=True):
                    if txt.parent and txt.parent.name == "a" and txt.parent.get("href"):
                        p_url = urlparse(urljoin(page_url, txt.parent["href"]))
                        if is_filter_or_nav_url(p_url):
                            continue
                    cand = clean_text(str(txt))
                    if is_valid_channel_name(cand, noise_words):
                        guessed_name = cand
                        break

        entries.append({
            "key": s_url,
            "name": guessed_name or humanize_stream_url(s_url),
            "stream_url": s_url,
            "watch_url": "",
            "logo": "",
            "country": "",
            "language": "",
            "category": default_category,
        })

    next_offsets = []
    next_pages = []
    for a_tag in soup.find_all("a", href=True):
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


def crawl_query_pages(noise_words, max_pages=25, country="", language="", category="", search=""):
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
            print(f"[WARN] Page {page_num + 1} failed ({params}): {e}")
            break

        entries, next_offsets, next_pages = parse_listing_page(
            resp.text, resp.url, noise_words, default_category=category
        )
        new_count = 0
        for item in entries:
            if item["key"] not in collected:
                collected[item["key"]] = item
                new_count += 1

        if new_count == 0:
            break

        page_num += 1
        higher_offsets = [o for o in next_offsets if o > offset]
        higher_pages = [p for p in next_pages if p > page]

        if higher_offsets:
            offset = higher_offsets[0]
        elif higher_pages:
            use_page_param = True
            page = higher_pages[0]
        else:
            offset += max(new_count, 18)

    return list(collected.values())


def verify_m3u8_url(stream_url):
    media_tags = (
        "#EXT-X-STREAM-INF", "#EXTINF:",
        ".ts", ".m4s", ".aac", ".mp4", ".m3u8",
    )
    try:
        resp = requests.get(
            stream_url, headers=HEADERS, timeout=8,
            allow_redirects=True, stream=True,
        )
        raw = next(resp.iter_content(chunk_size=2048), b"")
        chunk = raw.decode("utf-8", errors="ignore")
        resp.close()
        if resp.status_code == 200 and "#EXTM3U" in chunk:
            if any(tag in chunk for tag in media_tags):
                return resp.url
    except Exception:
        pass
    return None


def resolve_entry(entry, noise_words, verify=True):
    name = entry["name"]

    if entry.get("stream_url"):
        s_url = entry["stream_url"]
        final_url = verify_m3u8_url(s_url) if verify else s_url
        if not final_url:
            return None, ""
        final_name = (
            name
            if is_valid_channel_name(name, noise_words)
            else humanize_stream_url(final_url)
        )
        return final_url, final_name

    watch_url = entry.get("watch_url")
    if not watch_url:
        return None, ""

    try:
        resp = requests.get(
            watch_url, headers=HEADERS, timeout=10, allow_redirects=True
        )
        if resp.status_code != 200:
            return None, ""

        ctype = (resp.headers.get("Content-Type") or "").lower()
        is_hls = (
            ".m3u8" in resp.url.lower()
            or "mpegurl" in ctype
            or "#EXTM3U" in resp.text[:200]
        )
        if is_hls:
            final_name = (
                name
                if is_valid_channel_name(name, noise_words)
                else humanize_stream_url(resp.url)
            )
            return resp.url, final_name

        soup = BeautifulSoup(resp.text, "html.parser")
        if not is_valid_channel_name(name, noise_words):
            for tag_name in ("h1", "h2", "h3", "title"):
                tag = soup.find(tag_name)
                if tag:
                    cand = clean_text(tag.get_text(" ", strip=True))
                    if is_valid_channel_name(cand, noise_words):
                        name = cand
                        break

        for s_url in extract_m3u8_urls(resp.text):
            verified = verify_m3u8_url(s_url) if verify else s_url
            if verified:
                final_name = (
                    name
                    if is_valid_channel_name(name, noise_words)
                    else humanize_stream_url(verified)
                )
                return verified, final_name
    except Exception:
        pass

    return None, ""


def write_m3u(filepath, channels):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    lines = ["#EXTM3U"]
    seen_streams = set()

    for ch in sorted(channels, key=lambda x: x["name"].lower()):
        s_url = ch["stream_url"]
        if s_url in seen_streams:
            continue
        seen_streams.add(s_url)

        name = ch["name"]
        tvg_id = re.sub(r"[^a-zA-Z0-9]+", ".", name.lower()).strip(".")
        group = ch.get("category") or ch.get("country") or "General"
        lang = ch.get("language") or ""
        country = ch.get("country") or ""
        logo = ch.get("logo") or ""

        extinf = (
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{name}" '
            f'tvg-logo="{logo}" tvg-country="{country}" '
            f'tvg-language="{lang}" group-title="{group}",{name}'
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
        "Automatically refreshed every **12 hours** via GitHub Actions. "
        "Copy any **Raw M3U URL** below and paste it into your IPTV player "
        "(Open TV, TiviMate, OTT Navigator, Televizo, VLC, Kodi).",
        "",
        "### Master Playlist (All Verified Channels)",
        f"- **All Channels** ({total_working} channels): `{raw_base}/playlists/all.m3u`",
        "",
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

    print("Discovering filters & building noise blacklist from eja.tv...")
    discovered, noise_words = discover_site_filters()

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
    bucket_membership = {
        "countries": {}, "languages": {},
        "categories": {}, "searches": {},
    }

    for bucket, label, q_kwargs in tasks:
        print(f"Crawling [{bucket}] -> {label}...")
        items = crawl_query_pages(noise_words, **q_kwargs)
        print(f"  Found {len(items)} candidate entries.")

        for item in items:
            k = item["key"]
            if k not in registry:
                registry[k] = item
            else:
                for field in ("name", "logo", "country", "language", "category"):
                    if not registry[k][field] and item[field]:
                        registry[k][field] = item[field]

            if bucket == "countries" and not registry[k]["country"]:
                registry[k]["country"] = label
            elif bucket == "languages" and not registry[k]["language"]:
                registry[k]["language"] = label
            elif bucket == "categories" and not registry[k]["category"]:
                registry[k]["category"] = label

            if bucket in bucket_membership:
                bucket_membership[bucket].setdefault(label, set()).add(k)

    print(f"Resolving & verifying {len(registry)} unique streams...")
    resolved_by_key = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        future_map = {
            pool.submit(resolve_entry, data, noise_words, verify): key
            for key, data in registry.items()
        }
        for future in as_completed(future_map):
            key = future_map[future]
            stream_url, final_name = future.result()
            if stream_url:
                entry = dict(registry[key])
                entry["stream_url"] = stream_url
                entry["name"] = final_name
                resolved_by_key[key] = entry

    all_working = list(resolved_by_key.values())
    sample_names = [c["name"] for c in all_working[:15]]
    print(f"Verified {len(all_working)} working streams. Sample: {sample_names}")

    write_m3u("playlists/all.m3u", all_working)

    generated_summary = {
        "Country": [], "Language": [],
        "Category": [], "Custom Search": [],
    }
    label_map = {
        "countries": "Country",
        "languages": "Language",
        "categories": "Category",
        "searches": "Custom Search",
    }

    for bucket, label_dict in bucket_membership.items():
        for label, member_keys in label_dict.items():
            ch_list = [
                resolved_by_key[k]
                for k in member_keys
                if k in resolved_by_key
            ]
            fname = f"playlists/{bucket}/{safe_filename(label)}.m3u"
            write_m3u(
