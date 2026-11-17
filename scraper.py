import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import os
import re
import time
from urllib.parse import parse_qs, urljoin, urlparse
from bs4 import BeautifulSoup, NavigableString
import requests
from requests.adapters import HTTPAdapter

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


def build_session(pool_size=32):
    sess = requests.Session()
    adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    return sess


SESSION = build_session()


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
    low = re.sub(r"\[[^\]]*\]", "", low)
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
        resp = SESSION.get(BASE_URL, headers=PAGE_HEADERS, timeout=20)
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

    box = el
    parent = el.parent
    for _ in range(4):
        if not parent or parent.name in ("body", "html", "[document]"):
            break
        if len(set(extract_m3u8_from_text(str(parent)))) > 1:
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

    return list(items.values()), offsets, pages


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
            resp = SESSION.get(BASE_URL, params=params, headers=PAGE_HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception:
            break

        page_items, offsets, pages = parse_listing_page(
            resp.text, resp.url, noise, default_category=category
        )
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

    return list(items.values())


def probe_audio_tracks(master_text, base_url):
    """Checks #EXT-X-MEDIA:TYPE=AUDIO entries and verifies secondary audio URIs actually respond."""
    verified_langs = []
    for line in master_text.splitlines():
        line = line.strip()
        if not line.startswith("#EXT-X-MEDIA:") or "TYPE=AUDIO" not in line:
            continue
        lang_m = re.search(r'LANGUAGE="([^"]+)"', line)
        name_m = re.search(r'NAME="([^"]+)"', line)
        uri_m = re.search(r'URI="([^"]+)"', line)

        label = (lang_m.group(1) if lang_m else (name_m.group(1) if name_m else "")).strip().upper()
        if not label:
            continue

        # If audio has an external sub-playlist URI, verify it isn't 404 before advertising it
        if uri_m:
            aud_url = urljoin(base_url, uri_m.group(1))
            try:
                ar = SESSION.get(aud_url, headers=STREAM_HEADERS, timeout=5, allow_redirects=True)
                if ar.status_code != 200 or "#EXTM3U" not in ar.text[:200]:
                    continue
            except Exception:
                continue

        if label not in verified_langs:
            verified_langs.append(label)
    return verified_langs


def inspect_and_verify_hls(url):
    """
    Deep HLS inspector:
    - Verifies live video segment bytes
    - Extracts max resolution (1080p/720p/SD), HEVC codec flag, latency, and live audio tracks
    """
    t0 = time.monotonic()
    try:
        r = SESSION.get(url, headers=STREAM_HEADERS, timeout=8, allow_redirects=True)
        if r.status_code != 200 or "#EXTM3U" not in r.text[:300]:
            return None

        final_m3u8 = r.url
        text = r.text
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        max_height = 0
        is_hevc = False
        best_variant_url = ""
        best_bw = -1
        audio_langs = []

        if any(ln.startswith("#EXT-X-STREAM-INF") for ln in lines):
            audio_langs = probe_audio_tracks(text, final_m3u8)
            for idx, ln in enumerate(lines):
                if not ln.startswith("#EXT-X-STREAM-INF"):
                    continue
                res_m = re.search(r"RESOLUTION=\d+x(\d+)", ln)
                if res_m:
                    h = int(res_m.group(1))
                    if h > max_height:
                        max_height = h
                if re.search(r'CODECS="[^"]*(?:hev1|hvc1)', ln, re.I):
                    is_hevc = True
                bw_m = re.search(r"BANDWIDTH=(\d+)", ln)
                bw = int(bw_m.group(1)) if bw_m else 0
                if idx + 1 < len(lines) and not lines[idx + 1].startswith("#"):
                    if bw >= best_bw:
                        best_bw = bw
                        best_variant_url = urljoin(final_m3u8, lines[idx + 1])

            if not best_variant_url:
                return None
            vr = SESSION.get(best_variant_url, headers=STREAM_HEADERS, timeout=8, allow_redirects=True)
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
        sr = SESSION.get(seg_url, headers=STREAM_HEADERS, timeout=6, allow_redirects=True, stream=True)
        chunk = next(sr.iter_content(chunk_size=256), b"")
        sr.close()

        if sr.status_code in (200, 206) and len(chunk) > 0:
            latency_ms = int((time.monotonic() - t0) * 1000)
            q_tag = f"{max_height}p" if max_height >= 480 else ""
            return {
                "stream_url": final_m3u8,
                "height": max_height,
                "quality_tag": q_tag,
                "is_hevc": is_hevc,
                "audio_langs": audio_langs,
                "latency_ms": latency_ms,
            }
    except Exception:
        pass
    return None


def resolve_item(item, noise, verify=True):
    name = item["name"]

    if item["stream_url"]:
        if verify:
            info = inspect_and_verify_hls(item["stream_url"])
            if not info:
                return None
        else:
            info = {
                "stream_url": item["stream_url"],
                "height": 0,
                "quality_tag": "",
                "is_hevc": False,
                "audio_langs": [],
                "latency_ms": 999,
            }
        final_name = name if valid_name(name, noise) else humanize_url(info["stream_url"])
        if not valid_name(final_name, noise):
            return None
        info["name"] = final_name
        return info

    try:
        r = SESSION.get(item["watch_url"], headers=PAGE_HEADERS, timeout=10, allow_redirects=True)
        if r.status_code != 200:
            return None
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
            info = inspect_and_verify_hls(s_url) if verify else {
                "stream_url": s_url,
                "height": 0,
                "quality_tag": "",
                "is_hevc": False,
                "audio_langs": [],
                "latency_ms": 999,
            }
            if info:
                final_name = name if valid_name(name, noise) else humanize_url(info["stream_url"])
                if valid_name(final_name, noise):
                    info["name"] = final_name
                    return info
    except Exception:
        pass

    return None


def mirror_rank_key(ch):
    return (
        len(ch.get("audio_langs", [])),
        ch.get("height", 0),
        1 if ch["stream_url"].startswith("https://") else 0,
        name_quality_score(ch["name"]),
        -ch.get("latency_ms", 9999),
    )


def deduplicate_channels(channels, keep_backups=False):
    grouped = {}
    for ch in channels:
        norm = normalize_channel_key(ch["name"]) or ch["stream_url"]
        grouped.setdefault(norm, []).append(dict(ch))

    output = []
    for norm, mirrors in grouped.items():
        mirrors.sort(key=mirror_rank_key, reverse=True)
        best_title = max(mirrors, key=lambda x: name_quality_score(x["name"]))["name"]

        # Merge metadata across mirrors into the primary entry
        for fld in ("logo", "country", "language", "category"):
            if not mirrors[0].get(fld):
                for m in mirrors[1:]:
                    if m.get(fld):
                        mirrors[0][fld] = m[fld]
                        break

        mirrors[0]["name"] = best_title
        if not keep_backups:
            output.append(mirrors[0])
        else:
            seen_hosts = set()
            kept = 0
            for m in mirrors:
                host = urlparse(m["stream_url"]).netloc
                if kept > 0 and host in seen_hosts:
                    continue
                seen_hosts.add(host)
                copy_m = dict(m)
                copy_m["name"] = best_title if kept == 0 else f"{best_title} [Backup {kept}]"
                output.append(copy_m)
                kept += 1
                if kept >= 2:
                    break

    return output


def format_display_name(ch, tag_info=True):
    base = ch["name"]
    if not tag_info:
        return base
    tags = []
    if ch.get("quality_tag"):
        tags.append(ch["quality_tag"])
    if len(ch.get("audio_langs", [])) > 1:
        tags.append("Audio: " + "/".join(ch["audio_langs"][:4]))
    if ch.get("is_hevc"):
        tags.append("HEVC-MPV")
    return f"{base} [{' | '.join(tags)}]" if tags else base


def write_m3u(path, channels, epg_urls=None, deduplicate=True, keep_backups=False, tag_info=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    clean_list = deduplicate_channels(channels, keep_backups=keep_backups) if deduplicate else channels

    if epg_urls:
        epg_attr = ",".join(epg_urls)
        header = f'#EXTM3U url-tvg="{epg_attr}" x-tvg-url="{epg_attr}"'
    else:
        header = "#EXTM3U"

    lines, seen_urls = [header], set()

    for ch in sorted(clean_list, key=lambda x: x["name"].lower()):
        if ch["stream_url"] in seen_urls:
            continue
        seen_urls.add(ch["stream_url"])

        raw_name = ch["name"]
        disp_name = format_display_name(ch, tag_info=tag_info)
        tvg_id = re.sub(r"[^a-zA-Z0-9]+", ".", raw_name.lower()).strip(".")
        group = ch.get("category") or ch.get("country") or "General"
        verified_audio = ", ".join(ch.get("audio_langs", []))
        lang_field = verified_audio or ch.get("language", "")

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{raw_name}" '
            f'tvg-logo="{ch.get("logo", "")}" tvg-country="{ch.get("country", "")}" '
            f'tvg-language="{lang_field}" group-title="{group}",{disp_name}'
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
    tag_info = cfg.get("tag_stream_info", True)
    epg_urls = cfg.get("epg_urls", [])
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

    for term in cfg.get("telugu_hub_searches", []):
        tasks.append(("telugu_hub", term.title(), {"max_pages": max_pages, "search": term}))

    for term in cfg.get("custom_searches", []):
        tasks.append(("searches", term.title(), {"max_pages": max_pages, "search": term}))

    registry = {}
    membership = {
        "countries": {}, "languages": {}, "categories": {},
        "searches": {}, "telugu_hub": {},
    }

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
            elif bucket == "telugu_hub" and not registry[k]["language"]:
                registry[k]["language"] = "Telugu"

            if bucket in membership:
                membership[bucket].setdefault(lbl, set()).add(k)

    print(f"Deep-inspecting & verifying {len(registry)} unique streams...")
    resolved = {}
    with ThreadPoolExecutor(max_workers=24) as pool:
        fmap = {pool.submit(resolve_item, data, noise, verify): k for k, data in registry.items()}
        for fut in as_completed(fmap):
            k = fmap[fut]
            info = fut.result()
            if info:
                entry = dict(registry[k])
                entry.update(info)
                resolved[k] = entry

    all_working = list(resolved.values())

    # 1. Master & Backup Playlists
    total_all = write_m3u(
        "playlists/all.m3u", all_working,
        epg_urls=epg_urls, deduplicate=dedup, keep_backups=False, tag_info=tag_info,
    )
    total_backups = write_m3u(
        "playlists/special/with_backups.m3u", all_working,
        epg_urls=epg_urls, deduplicate=True, keep_backups=True, tag_info=tag_info,
    )

    # 2. Special Curated Playlists
    telugu_keys = set(membership["languages"].get("Telugu", set()))
    for sub_keys in membership["telugu_hub"].values():
        telugu_keys.update(sub_keys)
    telugu_channels = [resolved[k] for k in telugu_keys if k in resolved]
    total_telugu_hub = write_m3u(
        "playlists/special/telugu_hub.m3u", telugu_channels,
        epg_urls=epg_urls, deduplicate=dedup, tag_info=tag_info,
    )

    multi_audio_channels = [c for c in all_working if len(c.get("audio_langs", [])) > 1]
    total_multi_audio = write_m3u(
        "playlists/special/multi_audio.m3u", multi_audio_channels,
        epg_urls=epg_urls, deduplicate=dedup, tag_info=tag_info,
    )

    hd_channels = [c for c in all_working if c.get("height", 0) >= 720]
    total_hd = write_m3u(
        "playlists/special/hd_1080p.m3u", hd_channels,
        epg_urls=epg_urls, deduplicate=dedup, tag_info=tag_info,
    )

    # 3. Standard Country, Language, Category, and Custom Search Playlists
    summary = {"Country": [], "Language": [], "Category": [], "Custom Search": []}
    lmap = {
        "countries": "Country",
        "languages": "Language",
        "categories": "Category",
        "searches": "Custom Search",
    }
    for bucket, ldict in membership.items():
        if bucket not in lmap:
            continue
        for lbl, keys in ldict.items():
            clist = [resolved[k] for k in keys if k in resolved]
            fname = f"playlists/{bucket}/{safe_filename(lbl)}.m3u"
            written_count = write_m3u(
                fname, clist,
                epg_urls=epg_urls, deduplicate=dedup, tag_info=tag_info,
            )
            summary[lmap[bucket]].append((lbl, fname, written_count))

    # 4. Build Live Health & URL Dashboard in README.md
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")
    repo = os.environ.get("GITHUB_REPOSITORY", "Tarke4618/iptv")
    raw_base = f"https://raw.githubusercontent.com/{repo}/main"

    md = [
        "# Auto-Updating eja.tv IPTV Playlists (.m3u)",
        "",
        f"**Last Synced:** `{now_ist}` (Auto-refreshes every **12 hours**)",
        "",
        "### Featured & Smart Playlists (IPTVnator Ready)",
        "| Playlist | Channels | Raw M3U URL |",
        "| :--- | :---: | :--- |",
        f"| **All Verified Channels (Best Mirror)** | {total_all} | `{raw_base}/playlists/all.m3u` |",
        f"| **All Channels + Backup Mirrors** | {total_backups} | `{raw_base}/playlists/special/with_backups.m3u` |",
        f"| **Telugu Hub (All Telugu Sources)** | {total_telugu_hub} | `{raw_base}/playlists/special/telugu_hub.m3u` |",
        f"| **Verified Multi-Audio Channels** | {total_multi_audio} | `{raw_base}/playlists/special/multi_audio.m3u` |",
        f"| **HD 720p / 1080p Streams** | {total_hd} | `{raw_base}/playlists/special/hd_1080p.m3u` |",
        "",
    ]

    for sec, rows in summary.items():
        if rows:
            md.append(f"### By {sec}")
            md.append("| Filter | Channels | Raw M3U URL |")
            md.append("| :--- | :---: | :--- |")
            for lbl, rel, cnt in sorted(rows, key=lambda x: x[0].lower()):
                md.append(f"| **{lbl}** | {cnt} | `{raw_base}/{rel}` |")
            md.append("")

    with open("README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"Done! Master={total_all}, TeluguHub={total_telugu_hub}, MultiAudio={total_multi_audio}, HD={total_hd}")


if __name__ == "__main__":
    main()
