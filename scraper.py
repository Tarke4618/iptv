import base64
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, urljoin, urlparse
import requests
from bs4 import BeautifulSoup, NavigableString

BASE_URL = "https://eja.tv/"

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
    "ngrp", "amlst", "disk1", "liveevent", "bpk-tv",
}

ALIASES = {
    "country": {
        "gb": ("uk", "United Kingdom"),
        "uk": ("uk", "United Kingdom"),
        "united kingdom": ("uk", "United Kingdom"),
        "in": ("in", "India"),
        "india": ("in", "India"),
        "us": ("us", "United States"),
        "usa": ("us", "United States"),
        "united states": ("us", "United States"),
        "ca": ("ca", "Canada"),
        "canada": ("ca", "Canada"),
        "au": ("au", "Australia"),
        "australia": ("au", "Australia"),
    },
    "language": {
        "english": ("eng", "English"),
        "hindi": ("hin", "Hindi"),
        "telugu": ("tel", "Telugu"),
        "tamil": ("tam", "Tamil"),
        "spanish": ("spa", "Spanish"),
        "french": ("fra", "French"),
        "german": ("deu", "German"),
        "arabic": ("ara", "Arabic"),
        "russian": ("rus", "Russian"),
        "portuguese": ("por", "Portuguese"),
        "italian": ("ita", "Italian"),
        "japanese": ("jpn", "Japanese"),
        "korean": ("kor", "Korean"),
        "chinese": ("zho", "Chinese"),
        "bengali": ("ben", "Bengali"),
        "marathi": ("mar", "Marathi"),
        "gujarati": ("guj", "Gujarati"),
        "punjabi": ("pan", "Punjabi"),
        "malayalam": ("mal", "Malayalam"),
        "kannada": ("kan", "Kannada"),
        "urdu": ("urd", "Urdu"),
        "bhojpuri": ("bho", "Bhojpuri"),
    },
    "category": {
        "movies": ("Movie", "Movie"),
        "movie": ("Movie", "Movie"),
    },
}


def safe_filename(name):
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip())
    return clean.strip("_").lower() or "general"


def clean_text(text):
    t = text or ""
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
    if re.fullmatch(r"[a-f0-9]{16,}", low):
        return False
    if re.match(r"^(channel|stream|live|index|master|playlist)\s*\d*$", low):
        return False
    return True


def normalize_channel_key(name):
    low = (name or "").lower()
    low = re.sub(r"\b(hd|fhd|uhd|sd|4k|tv|live|channel|india)\b", "", low)
    key = re.sub(r"[^a-z0-9\u0900-\u0d7f]+", "", low)
    return key or re.sub(r"[^a-z0-9\u0900-\u0d7f]+", "", (name or "").lower())


def name_quality_score(name):
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
    parsed = urlparse(stream_url)
    path_parts = [p for p in parsed.path.split("/") if p]
    for part in reversed(path_parts):
        stem = re.sub(r"\.(m3u8|m3u|mpd|ts|smil)$", "", part, flags=re.I)
        low_stem = stem.lower()
        if low_stem in GENERIC_URL_PARTS or stem.isdigit() or len(stem) < 2:
            continue
        if re.fullmatch(r"[a-f0-9]{12,}", low_stem):
            continue
        if re.match(r"^(tracks|chunklist|index|playlist|master|live)[_-]", low_stem):
            continue
        word = re.sub(r"[_-]+", " ", stem).strip()
        if len(word) >= 2:
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
        for k, (code, label) in group.items():
            noise.update([k.lower(), code.lower(), label.lower()])
    return opts, noise


def resolve_param(field, raw, discovered):
    k = raw.strip().lower()
    if k in ALIASES[field]:
        code, friendly_label = ALIASES[field][k]
        if code.lower() in discovered[field]:
            return (code, discovered[field][code.lower()][1])
        return (code, friendly_label)
    if k in discovered[field]:
        return discovered[field][k]
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


def extract_m3u8_from_text(text):
    found = []
    if not text:
        return found
    for pat in (
        r'https?://[^\s"\'<>\\()]+?\.m3u8[^\s"\'<>\\()]*',
        r'(?<![a-zA-Z0-9:])//[a-zA-Z0-9.-]+/[^\s"\'<>\\()]+?\.m3u8[^\s"\'<>\\()]*',
    ):
        for m in re.findall(pat, text, re.I):
            u = ("https:" + m if m.startswith("//") else m).replace("\\/", "/").strip()
            if u not in found:
                found.append(u)
    for b64 in re.findall(r'atob\(\s*["\']([A-Za-z0-9+/=]{16,})["\']\s*\)', text):
        try:
            dec = base64.b64decode(b64).decode("utf-8", errors="ignore").strip()
            if ".m3u8" in dec.lower() and dec.startswith("http") and dec not in found:
                found.append(dec)
        except Exception:
            pass
    return found


def extract_dom_card_metadata(el, page_url, noise):
    """Walks up the BeautifulSoup DOM from a stream element to find its clean card title & badges."""
    cands = []
    logo = ""
    country = ""
    langs = []

    for attr in ("title", "aria-label", "alt", "data-title", "data-name"):
        val = el.get(attr)
        if isinstance(val, str):
            cands.append(clean_text(val))

    if el.name == "img" and el.get("src"):
        logo = urljoin(page_url, el["src"])

    # Walk up up to 4 ancestor tags to locate the single-channel card box
    box = el
    parent = el.parent
    for _ in range(4):
        if not parent or parent.name in ("body", "html", "[document]"):
            break
        # Stop climbing if this parent wraps multiple distinct .m3u8 attributes
        parent_html = str(parent)
        if len(set(extract_m3u8_from_text(parent_html))) > 1:
            break
        box = parent
        parent = parent.parent

    if box:
        img = box.find("img")
        if img:
            if not logo and img.get("src"):
                logo = urljoin(page_url, img["src"])
            for attr in ("alt", "title"):
                if img.get(attr):
                    cands.append(clean_text(img[attr]))

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


def parse_listing_page(html_text, page_url, noise, default_category=""):
    soup = BeautifulSoup(html_text, "html.parser")
    items = {}
    attr_hits, script_hits, link_hits = 0, 0, 0

    # 1. Inspect all DOM tags' attributes for embedded .m3u8 URLs
    for tag in soup.find_all(True):
        if tag.name in ("script", "style", "meta", "head"):
            continue
        found_urls = []
        for attr_val in tag.attrs.values():
            if isinstance(attr_val, list):
                attr_val = " ".join(str(x) for x in attr_val)
            elif not isinstance(attr_val, str):
                continue
            if ".m3u8" in attr_val.lower() or "atob(" in attr_val.lower():
                found_urls.extend(extract_m3u8_from_text(attr_val))

        for s_url in found_urls:
            if s_url in items:
                continue
            name, logo, c_country, c_langs = extract_dom_card_metadata(tag, page_url, noise)
            items[s_url] = {
                "key": s_url,
                "name": name or humanize_url(s_url),
                "stream_url": s_url,
                "watch_url": "",
                "logo": logo,
                "country": c_country,
                "language": c_langs,
                "category": default_category,
            }
            attr_hits += 1

    # 2. Inspect <script> blocks for .m3u8 URLs and adjacent JS string titles
    for sc in soup.find_all("script"):
        sc_text = sc.string or sc.get_text() or ""
        if ".m3u8" not in sc_text.lower() and "atob(" not in sc_text.lower():
            continue
        for s_url in extract_m3u8_from_text(sc_text):
            if s_url in items:
                continue
            pos = sc_text.find(s_url)
            guessed = ""
            if pos != -1:
                snippet = sc_text[max(0, pos - 250):min(len(sc_text), pos + 250)]
                for q_str in re.findall(r'["\']([^"\'\\]{2,65})["\']', snippet):
                    if ".m3u8" in q_str.lower() or q_str.startswith("http"):
                        continue
                    cand = clean_text(q_str)
                    if valid_name(cand, noise):
                        guessed = cand
                        break
            items[s_url] = {
                "key": s_url,
                "name": guessed or humanize_url(s_url),
                "stream_url": s_url,
                "watch_url": "",
                "logo": "",
                "country": "",
                "language": "",
                "category": default_category,
            }
            script_hits += 1

    # 3. Inspect any non-filter internal <a> watch links
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        full = urljoin(page_url, href)
        parsed = urlparse(full)
        if "eja.tv" in parsed.netloc.lower() and not is_filter_url(parsed) and full not in items:
            name, logo, c_country, c_langs = extract_dom_card_metadata(a, page_url, noise)
            items[full] = {
                "key": full,
                "name": name,
                "stream_url": "",
                "watch_url": full,
                "logo": logo,
                "country": c_country,
                "language": c_langs,
                "category": default_category,
            }
            link_hits += 1

    offsets = sorted({
        int(q["offset"][0])
        for a in soup.find_all("a", href=True)
        for q in [parse_qs(urlparse(urljoin(page_url, a["href"])).query)]
        if "offset" in q and q["offset"][0].isdigit()
    })
    pages = sorted({
        int(q["page"][0])
        for a in soup.find_all("a", href=True)
        for q in [parse_qs(urlparse(urljoin(page_url, a["href"])).query)]
        if "page" in q and q["page"][0].isdigit()
    })

    return list(items.values()), offsets, pages, (attr_hits, script_hits, link_hits)


def crawl_pages(noise, max_pages=25, country="", language="", category="", search=""):
    items = {}
    offset, page, use_page, count = 0, 1, False, 0
    totals = [0, 0, 0]

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

        page_items, offsets, pages, hits = parse_listing_page(
            resp.text, resp.url, noise, default_category=category
        )
        totals[0] += hits[0]
        totals[1] += hits[1]
        totals[2] += hits[2]

        added = 0
        for it in page_items:
            if it["key"] not in items:
                items[it["key"]] = it
                added += 1

        if added == 0:
            break

        count += 1
        hi_off = [o for o in offsets if o > offset]
        hi_pg = [p for p in pages if p > page]
        if hi_off:
            offset = hi_off[0]
        elif hi_pg:
            use_page, page = True, hi_pg[0]
        else:
            offset += max(added, 18)

    return list(items.values()), tuple(totals)


def verify_hls_deep(url):
    """Confirms .m3u8 manifest is valid AND at least one live video/audio segment returns bytes."""
    try:
        r = requests.get(url, headers=STREAM_HEADERS, timeout=8, allow_redirects=True)
        if r.status_code != 200 or "#EXTM3U" not in r.text[:300]:
            return None

        final_m3u8 = r.url
        lines = [ln.strip() for ln in r.text.splitlines() if ln.strip()]

        if any(ln.startswith("#EXT-X-STREAM-INF") for ln in lines):
            variant_rel = next((ln for ln in lines if not ln.startswith("#")), "")
            if not variant_rel:
                return None
            vr = requests.get(
                urljoin(final_m3u8, variant_rel),
                headers=STREAM_HEADERS,
                timeout=8,
                allow_redirects=True,
            )
            if vr.status_code != 200 or "#EXTM3U" not in vr.text[:300]:
                return None
            lines = [ln.strip() for ln in vr.text.splitlines() if ln.strip()]
            playlist_base = vr.url
        else:
            playlist_base = final_m3u8

        seg_rel = next((ln for ln in lines if not ln.startswith("#")), "")
        if not seg_rel:
            return None

        seg_url = urljoin(playlist_base, seg_rel)
        sr = requests.get(seg_url, headers=STREAM_HEADERS, timeout=6, allow_redirects=True, stream=True)
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
        return (u, final_name) if valid_name(final_name, noise) else (None, "")

    try:
        r = requests.get(item["watch_url"], headers=PAGE_HEADERS, timeout=10, allow_redirects=True)
        if r.status_code != 200:
            return None, ""
        soup = BeautifulSoup(r.text, "html.parser")
        if not valid_name(name, noise):
            for tag in ("h1", "h2", "h3", "title"):
                el = soup.find(tag)
                if el:
                    cand = clean_text(el.get_text(" ", strip=True))
                    if valid_name(cand, noise):
                        name = cand
                        break
        for s_url in extract_m3u8_from_text(r.text):
            u = verify_hls_deep(s_url) if verify else s_url
            if u:
                final_name = name if valid_name(name, noise) else humanize_url(u)
                if valid_name(final_name, noise):
                    return u, final_name
    except Exception:
        pass

    return None, ""


def deduplicate_channels(channels):
    best_by_norm = {}
    for ch in channels:
        norm = normalize_channel_key(ch["name"]) or ch["stream_url"]
        if norm not in best_by_norm:
            best_by_norm[norm] = dict(ch)
        else:
            existing = best_by_norm[norm]
            if name_quality_score(ch["name"]) > name_quality_score(existing["name"]):
                existing["name"] = ch["name"]
                existing["stream_url"] = ch["stream_url"]
            elif ch["stream_url"].startswith("https://") and not existing["stream_url"].startswith("https://"):
                existing["stream_url"] = ch["stream_url"]
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
        found, hits = crawl_pages(noise, **kwargs)
        print(f"  Found {len(found)} candidates (dom={hits[0]}, script={hits[1]}, links={hits[2]}).")
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
