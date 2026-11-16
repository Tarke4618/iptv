import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, urljoin, urlparse
import requests
from bs4 import BeautifulSoup, NavigableString

BASE_URL = "https://eja.tv/"

# Clean browser User-Agent without a hotlink Referer so CDN streams do not block playback
PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

STREAM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

FILTER_KEYS = {
    "country", "language", "category", "search",
    "offset", "limit", "page", "sort", "order", "level",
}
STATIC_SLUGS = {
    "about", "help", "privacy", "terms", "contact",
    "faq", "login", "register", "dmca",
}
STATIC_EXTS = (
    ".css", ".js", ".png", ".jpg", ".jpeg",
    ".gif", ".svg", ".ico", ".webp",
)

NOISE = {
    "eja", "eja.tv", "home", "tv", "live", "live tv",
    "channel", "channels", "stream", "streams", "watch",
    "play", "player", "video", "hd", "sd", "fhd",
    "next", "previous", "prev", "more", "all",
    "search", "filter", "menu", "sign languages",
    "sign language", "unknown", "untitled", "index",
    "playlist", "master", "chunklist", "manifest",
}

GENERIC_URL_PARTS = {
    "hls", "live", "stream", "streams", "index", "playlist",
    "master", "chunklist", "manifest", "tv", "iptv", "out",
    "v1", "v2", "v3", "cdn", "edge", "pub", "app", "smil",
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


def safe_filename(name):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip())
    return cleaned.strip("_").lower() or "general"


def clean_text(text):
    t = text or ""
    # Strip any leaked HTML tag fragments or angle brackets
    t = re.sub(r"<[^>]*>", " ", t)
    t = t.replace("<", " ").replace(">", " ").replace(",", " ")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(
        r"(?i)^eja\.tv\s*[-|:–—]+\s*|\s*[-|:–—]+\s*eja\.tv$",
        "",
        t,
    )
    return t.strip(" -|:–—/")


def valid_name(name, noise):
    if not name:
        return False
    low = name.strip().lower()
    if len(low) < 2 or len(low) > 80:
        return False
    if low in noise:
        return False
    if any(ch in low for ch in ("<", ">", "{", "}", "\\", "//")):
        return False
    if low.startswith(("http:", "https:", "www.")):
        return False
    if low.isdigit():
        return False
    if re.match(r"^(channel|stream|live|index|master|playlist)\s*\d*$", low):
        return False
    return True


def normalize_channel_key(name):
    """Creates a canonical key so 'Zee Telugu HD' and 'Zeeteluguhd' merge into one clean entry."""
    low = (name or "").lower()
    low = re.sub(r"\b(hd|fhd|uhd|sd|4k|tv|live|channel|india)\b", "", low)
    key = re.sub(r"[^a-z0-9\u0900-\u0d7f]+", "", low)
    return key or re.sub(r"[^a-z0-9\u0900-\u0d7f]+", "", (name or "").lower())


def name_quality_score(name):
    """Scores human-readable spaced names higher than raw URL slugs like 'zeeteluguhd'."""
    if not name:
        return 0
    score = 10
    if " " in name:
        score += 20
    if any(c.isupper() for c in name) and any(c.islower() for c in name):
        score += 10
    if re.search(r"\b(HD|News|TV|Music|Musix|Movies|Plus)\b", name):
        score += 10
    return score


def humanize_url(stream_url):
    """Extracts a meaningful channel name from the URL path (ignoring query strings & index.m3u8)."""
    parsed = urlparse(stream_url)
    path_parts = [p for p in parsed.path.split("/") if p]
    for part in reversed(path_parts):
        stem = re.sub(r"\.(m3u8|m3u|mpd|ts)$", "", part, flags=re.I)
        stem = re.sub(r"\.smil$", "", stem, flags=re.I)
        low_stem = stem.lower()
        if low_stem in GENERIC_URL_PARTS or stem.isdigit() or len(stem) < 2:
            continue
        if re.match(r"^(tracks|chunklist|index|playlist|master|live)[_-]", low_stem):
            continue
        word = re.sub(r"[_-]+", " ", stem).strip()
        return word.upper() if len(word) <= 4 else word.title()

    skip_hosts = {
        "www", "live", "hls", "cdn", "stream", "streams",
        "tv", "com", "net", "org", "in", "io", "akamaized", "cloudfront",
    }
    host_parts = [
        h for h in parsed.netloc.split(":")[0].split(".")
        if h.lower() not in skip_hosts and not h.isdigit()
    ]
    if host_parts:
        first = host_parts[0]
        return first.upper() if len(first) <= 4 else first.title()
    return ""


def discover_filters():
    opts = {"country": {}, "language": {}, "category": {}}
    noise = set(NOISE)
    try:
        resp = requests.get(BASE_URL, headers=PAGE_HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            qs = parse_qs(urlparse(urljoin(BASE_URL, a["href"])).query)
            lbl = clean_text(a.get_text(" ", strip=True))
            if not lbl:
                continue
            for f in opts:
                if f in qs and qs[f] and qs[f][0].strip():
                    val = qs[f][0].strip()
                    opts[f][val.lower()] = (val, lbl)
                    opts[f][lbl.lower()] = (val, lbl)
                    noise.update([val.lower(), lbl.lower()])
    except Exception as e:
        print(f"[WARN] Filter discovery: {e}")

    for group in ALIASES.values():
        for k, v in group.items():
            noise.update([k.lower(), v.lower()])
    return opts, noise


def resolve_param(field, raw, discovered):
    k = raw.strip().lower()
    if k in discovered[field]:
        return discovered[field][k]
    if k in ALIASES[field]:
        mapped = ALIASES[field][k]
        if mapped.lower() in discovered[field]:
            return discovered[field][mapped.lower()]
        label = raw.strip().title() if len(raw) > 3 else raw.strip().upper()
        return (mapped, label)
    return (raw.strip(), raw.strip())


def is_filter_url(parsed):
    path = parsed.path.strip("/").lower()
    if path in STATIC_SLUGS or path.endswith(STATIC_EXTS):
        return True
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if not path and not qs:
        return True
    if path:
        return False
    return all(k.lower() in FILTER_KEYS for k in qs)


def is_channel_link(a, page_url):
    href = (a.get("href") or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:")):
        return False
    full = urljoin(page_url, href)
    if ".m3u8" in full.lower():
        return True
    parsed = urlparse(full)
    return "eja.tv" in parsed.netloc.lower() and not is_filter_url(parsed)


def extract_m3u8s(html):
    found = []
    pat = r'https?://[^\s"\'<>\\()]+?\.m3u8[^\s"\'<>\\()]*'
    for m in re.findall(pat, html, re.I):
        u = m.replace("\\/", "/").strip()
        if u not in found:
            found.append(u)
    return found


def extract_card(a, page_url, noise):
    img = a.find("img")
    logo = urljoin(page_url, img["src"]) if img and img.get("src") else ""
    cands = [
        clean_text(x)
        for x in [
            a.get("title"),
            img.get("alt") if img else "",
            img.get("title") if img else "",
            a.get_text(" ", strip=True),
        ]
        if x
    ]
    country, langs = "", []

    box, parent = None, a.parent
    for _ in range(4):
        if not parent or parent.name in ("body", "html", "[document]"):
            break
        ch_links = [
            x for x in parent.find_all("a", href=True)
            if is_channel_link(x, page_url)
        ]
        if len(ch_links) == 1:
            box = parent
            parent = parent.parent
        else:
            break

    if box:
        if not logo:
            b_img = box.find("img")
            if b_img and b_img.get("src"):
                logo = urljoin(page_url, b_img["src"])
                if b_img.get("alt"):
                    cands.append(clean_text(b_img["alt"]))
        for lnk in box.find_all("a", href=True):
            q = parse_qs(urlparse(urljoin(page_url, lnk["href"])).query)
            txt = clean_text(lnk.get_text(" ", strip=True))
            if "country" in q and txt:
                country = txt
            elif "language" in q and txt and txt not in langs:
                langs.append(txt)
        for node in box.find_all(string=True):
            if not isinstance(node, NavigableString):
                continue
            if node.parent and node.parent.name in ("script", "style"):
                continue
            in_filter = any(
                anc.name == "a"
                and anc.get("href")
                and is_filter_url(urlparse(urljoin(page_url, anc["href"])))
                for anc in node.parents
                if anc != box
            )
            if not in_filter:
                cands.append(clean_text(str(node)))

    name = next((c for c in cands if valid_name(c, noise)), "")
    return name, logo, country, ", ".join(langs)


def crawl_pages(noise, max_pages=25, country="", language="", category="", search=""):
    items = {}
    offset, page, use_page, count = 0, 1, False, 0

    while max_pages <= 0 or count < max_pages:
        params = {
            k: v
            for k, v in [
                ("country", country),
                ("language", language),
                ("category", category),
                ("search", search),
            ]
            if v
        }
        if use_page and page > 1:
            params["page"] = page
        elif not use_page and offset > 0:
            params["offset"] = offset

        try:
            resp = requests.get(BASE_URL, params=params, headers=PAGE_HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        added = 0

        # Parse ONLY structured channel <a> links (never blind raw-HTML regex sweeps)
        for a in soup.find_all("a", href=True):
            if not is_channel_link(a, resp.url):
                continue
            full = urljoin(resp.url, a["href"].strip())
            if full in items:
                continue

            name, logo, c_country, c_langs = extract_card(a, resp.url, noise)
            host = urlparse(full).netloc.lower()
            direct = ".m3u8" in full.lower() and "eja.tv" not in host

            if not name and direct:
                name = humanize_url(full)

            items[full] = {
                "key": full,
                "name": name,
                "stream_url": full if direct else "",
                "watch_url": "" if direct else full,
                "logo": logo,
                "country": c_country,
                "language": c_langs,
                "category": category,
            }
            added += 1

        if added == 0:
            break

        count += 1
        offsets = sorted({
            int(q["offset"][0])
            for a in soup.find_all("a", href=True)
            for q in [parse_qs(urlparse(urljoin(resp.url, a["href"])).query)]
            if "offset" in q and q["offset"][0].isdigit()
        })
        pages = sorted({
            int(q["page"][0])
            for a in soup.find_all("a", href=True)
            for q in [parse_qs(urlparse(urljoin(resp.url, a["href"])).query)]
            if "page" in q and q["page"][0].isdigit()
        })

        hi_off = [o for o in offsets if o > offset]
        hi_pg = [p for p in pages if p > page]
        if hi_off:
            offset = hi_off[0]
        elif hi_pg:
            use_page, page = True, hi_pg[0]
        else:
            offset += max(added, 18)

    return list(items.values())


def verify_hls_deep(url):
    """
    2-step HLS verification:
    1. Fetches the .m3u8 manifest (following master -> variant playlist if needed).
    2. Confirms at least one actual video/audio media segment (.ts/.m4s/.aac) responds with HTTP 200/206.
    """
    try:
        r = requests.get(url, headers=STREAM_HEADERS, timeout=8, allow_redirects=True)
        if r.status_code != 200 or "#EXTM3U" not in r.text[:300]:
            return None

        final_m3u8 = r.url
        lines = [ln.strip() for ln in r.text.splitlines() if ln.strip()]

        # If this is a master playlist, follow the first variant stream playlist
        if any(ln.startswith("#EXT-X-STREAM-INF") for ln in lines):
            variant_rel = next((ln for ln in lines if not ln.startswith("#")), "")
            if not variant_rel:
                return None
            variant_url = urljoin(final_m3u8, variant_rel)
            vr = requests.get(variant_url, headers=STREAM_HEADERS, timeout=8, allow_redirects=True)
            if vr.status_code != 200 or "#EXTM3U" not in vr.text[:300]:
                return None
            lines = [ln.strip() for ln in vr.text.splitlines() if ln.strip()]
            playlist_base = vr.url
        else:
            playlist_base = final_m3u8

        # Locate the first actual media segment URI and verify it serves live bytes
        seg_rel = next((ln for ln in lines if not ln.startswith("#")), "")
        if not seg_rel:
            return None

        seg_url = urljoin(playlist_base, seg_rel)
        seg_headers = dict(STREAM_HEADERS)
        seg_headers["Range"] = "bytes=0-511"
        sr = requests.get(seg_url, headers=seg_headers, timeout=6, allow_redirects=True, stream=True)
        chunk = next(sr.iter_content(chunk_size=256), b"")
        sr.close()

        if sr.status_code in (200, 206) and len(chunk) > 0:
            return final_m3u8
    except Exception:
        pass
    return None


def resolve_item(item, noise, verify=True):
    name = item["name"]

    if item["stream_url"]:
        u = verify_hls_deep(item["stream_url"]) if verify else item["stream_url"]
        if not u:
            return None, ""
        final_name = name if valid_name(name, noise) else humanize_url(u)
        if not valid_name(final_name, noise):
            return None, ""
        return u, final_name

    try:
        r = requests.get(item["watch_url"], headers=PAGE_HEADERS, timeout=10, allow_redirects=True)
        if r.status_code != 200:
            return None, ""

        ctype = (r.headers.get("Content-Type") or "").lower()
        if ".m3u8" in r.url.lower() or "mpegurl" in ctype or "#EXTM3U" in r.text[:200]:
            u = verify_hls_deep(r.url) if verify else r.url
            if not u:
                return None, ""
            final_name = name if valid_name(name, noise) else humanize_url(u)
            return (u, final_name) if valid_name(final_name, noise) else (None, "")

        soup = BeautifulSoup(r.text, "html.parser")
        if not valid_name(name, noise):
            for tag in ("h1", "h2", "h3", "title"):
                el = soup.find(tag)
                if el:
                    cand = clean_text(el.get_text(" ", strip=True))
                    if valid_name(cand, noise):
                        name = cand
                        break

        for s_url in extract_m3u8s(r.text):
            u = verify_hls_deep(s_url) if verify else s_url
            if u:
                final_name = name if valid_name(name, noise) else humanize_url(u)
                if valid_name(final_name, noise):
                    return u, final_name
    except Exception:
        pass

    return None, ""


def deduplicate_channels(channels):
    """
    Merges duplicate mirrors of the same channel (e.g. 6x V6 News or Zee Telugu HD + Zeeteluguhd),
    keeping the cleanest display name and best working stream.
    """
    best_by_norm = {}
    for ch in channels:
        norm = normalize_channel_key(ch["name"])
        if not norm:
            norm = ch["stream_url"]

        if norm not in best_by_norm:
            best_by_norm[norm] = dict(ch)
        else:
            existing = best_by_norm[norm]
            # Upgrade display name if the new candidate has a cleaner human-readable title
            if name_quality_score(ch["name"]) > name_quality_score(existing["name"]):
                existing["name"] = ch["name"]
                existing["stream_url"] = ch["stream_url"]
            # Prefer HTTPS streams when quality is tied
            elif ch["stream_url"].startswith("https://") and not existing["stream_url"].startswith("https://"):
                existing["stream_url"] = ch["stream_url"]
            # Merge missing metadata
            for fld in ("logo", "country", "language", "category"):
                if not existing.get(fld) and ch.get(fld):
                    existing[fld] = ch[fld]

    return list(best_by_norm.values())


def write_m3u(path, channels, deduplicate=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    clean_list = deduplicate_channels(channels) if deduplicate else channels
    lines, seen_urls = ["#EXTM3U"], set()

    for ch in sorted(clean_list, key=lambda x: x["name"].lower()):
        if ch["stream_url"] in seen_urls:
            continue
        seen_urls.add(ch["stream_url"])

        name = ch["name"]
        tvg_id = re.sub(r"[^a-zA-Z0-9]+", ".", name.lower()).strip(".")
        group = ch.get("category") or ch.get("country") or "General"
        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{name}" '
            f'tvg-logo="{ch.get("logo", "")}" tvg-country="{ch.get("country", "")}" '
            f'tvg-language="{ch.get("language", "")}" group-title="{group}",{name}'
        )
        lines.append(ch["stream_url"])

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(seen_urls)


def main():
    with open("config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    max_pages = cfg.get("max_pages_per_query", 25)
    verify = cfg.get("verify_streams", True)
    dedup = cfg.get("deduplicate_channels", True)
    discovered, noise = discover_filters()

    tasks = [("general", "Global Feed", {"max_pages": max_pages})]
    if cfg.get("scrape_all_filters", False):
        for val, lbl in sorted(set(discovered["country"].values()), key=lambda x: x[1]):
            tasks.append(("countries", lbl, {"max_pages": max_pages, "country": val}))
        for val, lbl in sorted(set(discovered["language"].values()), key=lambda x: x[1]):
            tasks.append(("languages", lbl, {"max_pages": max_pages, "language": val}))
        for val, lbl in sorted(set(discovered["category"].values()), key=lambda x: x[1]):
            tasks.append(("categories", lbl, {"max_pages": max_pages, "category": val}))
    else:
        for c in cfg.get("countries", []):
            val, lbl = resolve_param("country", c, discovered)
            tasks.append(("countries", lbl, {"max_pages": max_pages, "country": val}))
        for l in cfg.get("languages", []):
            val, lbl = resolve_param("language", l, discovered)
            tasks.append(("languages", lbl, {"max_pages": max_pages, "language": val}))
        for cat in cfg.get("categories", []):
            val, lbl = resolve_param("category", cat, discovered)
            tasks.append(("categories", lbl, {"max_pages": max_pages, "category": val}))

    for term in cfg.get("custom_searches", []):
        tasks.append(("searches", term.title(), {"max_pages": max_pages, "search": term}))

    registry = {}
    membership = {"countries": {}, "languages": {}, "categories": {}, "searches": {}}

    for bucket, lbl, kwargs in tasks:
        print(f"Crawling [{bucket}] -> {lbl}...")
        found = crawl_pages(noise, **kwargs)
        print(f"  Found {len(found)} candidates.")
        for item in found:
            k = item["key"]
            if k not in registry:
                registry[k] = item
            else:
                for fld in ("name", "logo", "country", "language", "category"):
                    if not registry[k][fld] and item[fld]:
                        registry[k][fld] = item[fld]
            if bucket in ("countries", "languages", "categories"):
                singular = {"countries": "country", "languages": "language", "categories": "category"}[bucket]
                if not registry[k][singular]:
                    registry[k][singular] = lbl
            if bucket in membership:
                membership[bucket].setdefault(lbl, set()).add(k)

    print(f"Deep-verifying {len(registry)} unique streams...")
    resolved = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        fmap = {pool.submit(resolve_item, data, noise, verify): k for k, data in registry.items()}
        for fut in as_completed(fmap):
            k = fmap[fut]
            u, final_name = fut.result()
            if u and final_name:
                entry = dict(registry[k])
                entry["stream_url"] = u
                entry["name"] = final_name
                resolved[k] = entry

    all_working = list(resolved.values())
    total_all = write_m3u("playlists/all.m3u", all_working, deduplicate=dedup)
    print(f"Saved {total_all} verified unique channels to playlists/all.m3u")

    summary = {"Country": [], "Language": [], "Category": [], "Custom Search": []}
    lmap = {
        "countries": "Country",
        "languages": "Language",
        "categories": "Category",
        "searches": "Custom Search",
    }
    for bucket, ldict in membership.items():
        for lbl, keys in ldict.items():
            clist = [resolved[k] for k in keys if k in resolved]
            fname = f"playlists/{bucket}/{safe_filename(lbl)}.m3u"
            written_count = write_m3u(fname, clist, deduplicate=dedup)
            summary[lmap[bucket]].append((lbl, fname, written_count))

    repo = os.environ.get("GITHUB_REPOSITORY", "Tarke4618/iptv")
    raw_base = f"https://raw.githubusercontent.com/{repo}/main"
    md = [
        "# Auto-Updating eja.tv IPTV Playlists (.m3u)",
        "",
        "Automatically refreshed every **12 hours** via GitHub Actions. Copy any **Raw M3U URL** below and paste it into your IPTV player (Open TV, TiviMate, OTT Navigator, Televizo, VLC, Kodi).",
        "",
        "### Master Playlist (All Verified Channels)",
        f"- **All Channels** ({total_all} channels): `{raw_base}/playlists/all.m3u`",
        "",
    ]
    for sec, rows in summary.items():
        if rows:
            md.append(f"### By {sec}")
            for lbl, rel, cnt in sorted(rows, key=lambda x: x[0].lower()):
                md.append(f"- **{lbl}** ({cnt} channels): `{raw_base}/{rel}`")
            md.append("")
    with open("README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))


if __name__ == "__main__":
    main()
