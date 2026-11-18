import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import os
import re
import subprocess
import time
from urllib.parse import parse_qs, unquote, urljoin, urlparse
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
    "ngrp", "amlst", "disk1", "liveevent", "bpk-tv", "tracks-v1a1",
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

TELUGU_LANG_CODES = {"tel", "te", "telugu", "తెలుగు"}
RAW_IP_PORT_RE = re.compile(r"^https?://\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?/", re.I)


def build_session(pool_size=96):
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
    if re.match(r"^(channel|stream|live|index|master|playlist|tracks)\s*\d*$", low):
        return False
    return True


def normalize_channel_key(name):
    low = (name or "").lower()
    low = re.sub(r"\[[^\]]*\]", "", low)
    low = re.sub(r"\([^\)]*\)", "", low)
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
    if re.search(r"\b(HD|News|TV|Music|Musix|Movies|Plus|Cinema|Cinemalu)\b", name):
        score += 10
    return score


def split_pipe_headers(raw_url):
    if "|" not in raw_url:
        return raw_url.strip(), {}

    base_url, pipe_part = raw_url.split("|", 1)
    hdrs = {}
    for pair in re.split(r"[&|]", pipe_part):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        k_low = k.strip().lower()
        v_clean = unquote(v.strip()).strip("\"'")
        if not v_clean:
            continue
        if k_low in ("user-agent", "http-user-agent"):
            hdrs["User-Agent"] = v_clean
        elif k_low in ("referer", "referrer", "http-referrer", "http-referer"):
            hdrs["Referer"] = v_clean
        elif k_low == "origin":
            hdrs["Origin"] = v_clean
        elif k_low == "cookie":
            hdrs["Cookie"] = v_clean
    return base_url.strip(), hdrs


def is_usable_hls_url(url, skip_raw_ip=True):
    low = url.lower()
    if not low.startswith(("http://", "https://")):
        return False
    if ".mpd" in low.split("?")[0]:
        return False
    if skip_raw_ip and RAW_IP_PORT_RE.match(url):
        return False
    return True


def humanize_url(stream_url):
    parsed = urlparse(stream_url)
    path_parts = [p for p in parsed.path.split("/") if p]
    for part in reversed(path_parts):
        stem = re.sub(r"\.(m3u8|m3u|mpd|ts|smil|sdp)$", "", part, flags=re.I)
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


def classify_telugu_group(ch):
    blob = f" {ch.get('name', '')} {ch.get('category', '')} ".lower()
    audio_langs = [a.lower() for a in ch.get("audio_langs", [])]
    if ch.get("has_telugu_audio") or (len(audio_langs) > 1 and not ch.get("is_telugu")):
        return "Telugu - Multi-Audio & Kids"
    if any(k in blob for k in ("news", "tv9", "ntv", "tv5", "sakshi", "abn", "v6", "10tv", "hmtv", "t news", "mahaa", "prime9", "99tv", "6tv", "studio n", "raj news", "big tv", "brk", "q news")):
        return "Telugu - News"
    if any(k in blob for k in ("movie", "cinema", "cinemalu", "gold", "sitara")):
        return "Telugu - Movies"
    if any(k in blob for k in ("music", "musix", "beats", "raj musix")):
        return "Telugu - Music"
    if any(k in blob for k in ("svbc", "bhakti", "subhagruha", "calvary", "aradana", "devotional", "hindu", "christian")):
        return "Telugu - Devotional"
    if any(k in blob for k in ("kushi", "bal bharat", "yay", "nick", "cartoon", "pogo", "hungama", "discovery", "nat geo", "history", "kids")):
        return "Telugu - Kids & Infotainment"
    return "Telugu - Entertainment"


def discover_filters():
    opts = {"country": {}, "language": {}, "category": {}}
    noise = set(NOISE)
    try:
        resp = SESSION.get(BASE_URL, headers=PAGE_HEADERS, timeout=(5, 10))
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
                "extra_headers": {},
                "source_name": "eja.tv",
                "is_external": False,
                "is_telugu": False,
                "is_multi_audio_candidate": False,
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
                "extra_headers": {},
                "source_name": "eja.tv",
                "is_external": False,
                "is_telugu": False,
                "is_multi_audio_candidate": False,
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
                "extra_headers": {},
                "source_name": "eja.tv",
                "is_external": False,
                "is_telugu": False,
                "is_multi_audio_candidate": False,
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


def crawl_pages(noise, max_pages=15, country="", language="", category="", search=""):
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
            resp = SESSION.get(BASE_URL, params=params, headers=PAGE_HEADERS, timeout=(4, 8))
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


def matches_keywords(text_blob, keywords):
    low = f" {text_blob} ".lower()
    for kw in keywords:
        kw_clean = kw.strip().lower()
        if len(kw_clean) >= 3 and re.search(rf"\b{re.escape(kw_clean)}\b", low):
            return True
    return False


def is_telugu_channel(name, lang, group, stream_url, telugu_keywords):
    blob = f" {name} {lang} {group} {stream_url} ".lower()
    if "telugu" in blob or "tel" in re.split(r"[^a-z]+", lang.lower()):
        return True
    return matches_keywords(blob, telugu_keywords)


def resolve_single_youtube(item):
    name = item.get("name", "YouTube Live")
    yt_url = item.get("url", "").strip()
    lang = item.get("language", "Telugu")
    cat = item.get("category", "News")
    if not yt_url:
        return None

    m3u8_url = ""
    try:
        proc = subprocess.run(
            ["yt-dlp", "-g", "-f", "best", "--no-warnings", yt_url],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            first_line = proc.stdout.strip().splitlines()[0].strip()
            if ".m3u8" in first_line:
                m3u8_url = first_line
    except Exception:
        pass

    if not m3u8_url:
        try:
            r = SESSION.get(yt_url, headers=PAGE_HEADERS, timeout=(4, 8))
            m = re.search(r'"hlsManifestUrl"\s*:\s*"([^"]+\.m3u8[^"]*)"', r.text)
            if m:
                m3u8_url = m.group(1).replace("\\/", "/").encode("utf-8").decode("unicode_escape")
        except Exception:
            pass

    if not m3u8_url:
        return None

    return {
        "key": m3u8_url,
        "name": name,
        "stream_url": m3u8_url,
        "watch_url": "",
        "logo": "",
        "country": "India",
        "language": lang,
        "category": cat,
        "extra_headers": {},
        "source_name": "YouTube Live",
        "is_external": True,
        "is_telugu": lang.lower() == "telugu",
        "is_multi_audio_candidate": False,
    }


def resolve_youtube_live_channels(yt_list):
    resolved_yt = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(resolve_single_youtube, it) for it in yt_list]
        for fut in as_completed(futs):
            res = fut.result()
            if res:
                resolved_yt.append(res)
    return resolved_yt


def parse_external_m3u(src_cfg, allowed_langs, telugu_keywords, multi_audio_keywords, only_matching_langs, skip_raw_ip, noise):
    src_name = src_cfg.get("name", "External")
    src_url = src_cfg.get("url", "").strip()
    telugu_only = src_cfg.get("telugu_only", False)
    always_keep = src_cfg.get("always_keep", False)
    def_country = src_cfg.get("default_country", "")
    def_lang = src_cfg.get("default_language", "")

    if not src_url:
        return []

    try:
        resp = SESSION.get(src_url, headers=STREAM_HEADERS, timeout=(5, 12))
        resp.raise_for_status()
    except Exception as e:
        print(f"[WARN] Failed external M3U '{src_name}': {e}")
        return []

    results = []
    cur_meta = None
    cur_headers = {}

    for raw_line in resp.text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#EXTINF:"):
            cur_headers = {}
            logo_m = re.search(r'tvg-logo="([^"]*)"', line, re.I)
            lang_m = re.search(r'tvg-language="([^"]*)"', line, re.I)
            cntry_m = re.search(r'tvg-country="([^"]*)"', line, re.I)
            grp_m = re.search(r'group-title="([^"]*)"', line, re.I)
            ua_m = re.search(r'user-agent="([^"]*)"', line, re.I)
            ref_m = re.search(r'referer="([^"]*)"', line, re.I)

            if ua_m and ua_m.group(1).strip():
                cur_headers["User-Agent"] = ua_m.group(1).strip()
            if ref_m and ref_m.group(1).strip():
                cur_headers["Referer"] = ref_m.group(1).strip()

            title_part = line.rsplit(",", 1)[-1] if "," in line else ""
            clean_title = re.sub(r"\s*[\[(][^\])]*[\])]", "", title_part).strip()
            clean_title = clean_text(clean_title)

            cur_meta = {
                "name": clean_title,
                "logo": logo_m.group(1).strip() if logo_m else "",
                "language": (lang_m.group(1).strip() if lang_m else "") or def_lang,
                "country": (cntry_m.group(1).strip() if cntry_m else "") or def_country,
                "category": grp_m.group(1).strip() if grp_m else "",
            }
        elif line.startswith("#EXTVLCOPT:") and cur_meta is not None:
            opt = line.split(":", 1)[1].strip()
            if opt.lower().startswith("http-user-agent="):
                cur_headers["User-Agent"] = opt.split("=", 1)[1].strip()
            elif opt.lower().startswith(("http-referrer=", "http-referer=")):
                cur_headers["Referer"] = opt.split("=", 1)[1].strip()
        elif line.startswith("#EXTHTTP:") and cur_meta is not None:
            try:
                http_json = json.loads(line.split(":", 1)[1].strip())
                for hk, hv in http_json.items():
                    if hk.lower() == "user-agent":
                        cur_headers["User-Agent"] = str(hv)
                    elif hk.lower() in ("referer", "referrer"):
                        cur_headers["Referer"] = str(hv)
            except Exception:
                pass
        elif not line.startswith("#") and cur_meta is not None:
            clean_url, pipe_hdrs = split_pipe_headers(line)
            merged_hdrs = dict(cur_headers)
            merged_hdrs.update(pipe_hdrs)

            if is_usable_hls_url(clean_url, skip_raw_ip=skip_raw_ip):
                ch_name = cur_meta["name"] or humanize_url(clean_url)
                if valid_name(ch_name, noise):
                    tel_flag = telugu_only or is_telugu_channel(
                        ch_name, cur_meta["language"], cur_meta["category"], clean_url, telugu_keywords
                    )
                    multi_cand = matches_keywords(
                        f"{ch_name} {cur_meta['category']}", multi_audio_keywords
                    )
                    if tel_flag and not cur_meta["language"]:
                        cur_meta["language"] = "Telugu"

                    lang_blob = f"{cur_meta['language']} {cur_meta['category']} {ch_name}".lower()
                    lang_ok = (
                        always_keep
                        or tel_flag
                        or multi_cand
                        or any(lg in lang_blob for lg in allowed_langs)
                    )

                    if not only_matching_langs or lang_ok:
                        results.append({
                            "key": clean_url,
                            "name": ch_name,
                            "stream_url": clean_url,
                            "watch_url": "",
                            "logo": cur_meta["logo"],
                            "country": cur_meta["country"],
                            "language": cur_meta["language"],
                            "category": cur_meta["category"],
                            "extra_headers": merged_hdrs,
                            "source_name": src_name,
                            "is_external": True,
                            "is_telugu": tel_flag,
                            "is_multi_audio_candidate": multi_cand,
                        })
            cur_meta = None
            cur_headers = {}

    return results


def probe_audio_tracks(master_text, base_url, req_headers):
    verified_langs = []
    has_telugu_audio = False

    for line in master_text.splitlines():
        line = line.strip()
        if not line.startswith("#EXT-X-MEDIA:") or "TYPE=AUDIO" not in line:
            continue
        lang_m = re.search(r'LANGUAGE="([^"]+)"', line, re.I)
        name_m = re.search(r'NAME="([^"]+)"', line, re.I)
        uri_m = re.search(r'URI="([^"]+)"', line, re.I)

        raw_lang = (lang_m.group(1) if lang_m else "").strip()
        raw_name = (name_m.group(1) if name_m else "").strip()
        label = (raw_lang or raw_name).upper()
        if not label:
            continue

        if uri_m:
            aud_url = urljoin(base_url, uri_m.group(1))
            try:
                ar = SESSION.get(aud_url, headers=req_headers, timeout=(2.5, 4), allow_redirects=True)
                if ar.status_code != 200 or "#EXTM3U" not in ar.text[:200]:
                    continue
            except Exception:
                continue

        if raw_lang.lower() in TELUGU_LANG_CODES or raw_name.lower() in TELUGU_LANG_CODES or "telugu" in raw_name.lower():
            has_telugu_audio = True
            label = "TEL"

        if label not in verified_langs:
            verified_langs.append(label)

    return verified_langs, has_telugu_audio


def inspect_and_verify_hls(url, extra_headers=None, skip_raw_ip=True):
    req_headers = dict(STREAM_HEADERS)
    if extra_headers:
        req_headers.update(extra_headers)

    t0 = time.monotonic()
    try:
        current_url = url
        max_height = 0
        is_hevc = False
        audio_langs = []
        has_telugu_audio = False
        export_m3u8 = url

        lines = []
        playlist_base = current_url
        for hop in range(3):
            if not is_usable_hls_url(current_url, skip_raw_ip=skip_raw_ip):
                return None
            r = SESSION.get(current_url, headers=req_headers, timeout=(2.5, 4.5), allow_redirects=True)
            if r.status_code != 200 or "#EXTM3U" not in r.text[:300]:
                return None

            playlist_base = r.url
            if hop == 0:
                export_m3u8 = r.url
            elif "raw.githubusercontent.com" in export_m3u8.lower():
                export_m3u8 = r.url

            text = r.text
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

            if any(ln.startswith("#EXT-X-STREAM-INF") for ln in lines):
                hop_langs, hop_tel = probe_audio_tracks(text, playlist_base, req_headers)
                for lg in hop_langs:
                    if lg not in audio_langs:
                        audio_langs.append(lg)
                if hop_tel:
                    has_telugu_audio = True

                best_variant_url = ""
                best_bw = -1
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
                            best_variant_url = urljoin(playlist_base, lines[idx + 1])

                if not best_variant_url:
                    return None
                current_url = best_variant_url
            else:
                break

        seg_rel = next((ln for ln in lines if not ln.startswith("#")), "")
        if not seg_rel:
            return None

        seg_url = urljoin(playlist_base, seg_rel)
        sr = SESSION.get(seg_url, headers=req_headers, timeout=(2.5, 4), allow_redirects=True, stream=True)
        chunk = next(sr.iter_content(chunk_size=256), b"")
        sr.close()

        if sr.status_code in (200, 206) and len(chunk) > 0:
            latency_ms = int((time.monotonic() - t0) * 1000)
            q_tag = f"{max_height}p" if max_height >= 480 else ""
            return {
                "stream_url": export_m3u8,
                "height": max_height,
                "quality_tag": q_tag,
                "is_hevc": is_hevc,
                "audio_langs": audio_langs,
                "has_telugu_audio": has_telugu_audio,
                "latency_ms": latency_ms,
            }
    except Exception:
        pass
    return None


def resolve_item(item, noise, verify=True, skip_raw_ip=True):
    name = item["name"]
    extra_headers = item.get("extra_headers", {})

    if item["stream_url"]:
        if verify:
            info = inspect_and_verify_hls(
                item["stream_url"], extra_headers=extra_headers, skip_raw_ip=skip_raw_ip
            )
            if not info:
                return None
        else:
            info = {
                "stream_url": item["stream_url"],
                "height": 0,
                "quality_tag": "",
                "is_hevc": False,
                "audio_langs": [],
                "has_telugu_audio": False,
                "latency_ms": 999,
            }
        final_name = name if valid_name(name, noise) else humanize_url(info["stream_url"])
        if not valid_name(final_name, noise):
            return None
        info["name"] = final_name
        return info

    try:
        r = SESSION.get(item["watch_url"], headers=PAGE_HEADERS, timeout=(3, 6), allow_redirects=True)
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
            info = inspect_and_verify_hls(
                s_url, extra_headers=extra_headers, skip_raw_ip=skip_raw_ip
            ) if verify else {
                "stream_url": s_url,
                "height": 0,
                "quality_tag": "",
                "is_hevc": False,
                "audio_langs": [],
                "has_telugu_audio": False,
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
        1 if ch.get("has_telugu_audio") else 0,
        len(ch.get("audio_langs", [])),
        ch.get("height", 0),
        1 if ch["stream_url"].startswith("https://") else 0,
        name_quality_score(ch["name"]),
        -ch.get("latency_ms", 9999),
    )


def deduplicate_channels(channels, max_mirrors=1):
    grouped = {}
    for ch in channels:
        norm = normalize_channel_key(ch["name"]) or ch["stream_url"]
        grouped.setdefault(norm, []).append(dict(ch))

    output = []
    for norm, mirrors in grouped.items():
        mirrors.sort(key=mirror_rank_key, reverse=True)
        best_title = max(mirrors, key=lambda x: name_quality_score(x["name"]))["name"]

        for fld in ("logo", "country", "language", "category"):
            if not mirrors[0].get(fld):
                for m in mirrors[1:]:
                    if m.get(fld):
                        mirrors[0][fld] = m[fld]
                        break

        mirrors[0]["name"] = best_title
        if max_mirrors <= 1:
            output.append(mirrors[0])
        else:
            seen_urls = set()
            kept = 0
            for m in mirrors:
                if m["stream_url"] in seen_urls:
                    continue
                seen_urls.add(m["stream_url"])
                copy_m = dict(m)
                copy_m["name"] = best_title if kept == 0 else f"{best_title} [Mirror {kept + 1}]"
                output.append(copy_m)
                kept += 1
                if kept >= max_mirrors:
                    break

    return output


def format_display_name(ch, tag_info=True):
    base = ch["name"]
    if not tag_info:
        return base
    tags = []
    if ch.get("quality_tag"):
        tags.append(ch["quality_tag"])
    if ch.get("has_telugu_audio"):
        other_langs = [a for a in ch.get("audio_langs", []) if a != "TEL"][:3]
        tags.append("Audio: TEL+" + "/".join(other_langs) if other_langs else "Audio: TEL")
    elif len(ch.get("audio_langs", [])) > 1:
        tags.append("Audio: " + "/".join(ch["audio_langs"][:4]))
    if ch.get("is_hevc"):
        tags.append("HEVC-MPV")
    return f"{base} [{' | '.join(tags)}]" if tags else base


def write_m3u(path, channels, epg_urls=None, deduplicate=True, max_mirrors=1, tag_info=True, subgroup_telugu=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    clean_list = deduplicate_channels(channels, max_mirrors=max_mirrors) if deduplicate else channels

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
        group = classify_telugu_group(ch) if subgroup_telugu else (ch.get("category") or ch.get("country") or "General")
        verified_audio = ", ".join(ch.get("audio_langs", []))
        lang_field = verified_audio or ch.get("language", "")

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{raw_name}" '
            f'tvg-logo="{ch.get("logo", "")}" tvg-country="{ch.get("country", "")}" '
            f'tvg-language="{lang_field}" group-title="{group}",{disp_name}'
        )
        extra_hdrs = ch.get("extra_headers", {})
        if extra_hdrs.get("User-Agent"):
            lines.append(f'#EXTVLCOPT:http-user-agent={extra_hdrs["User-Agent"]}')
        if extra_hdrs.get("Referer"):
            lines.append(f'#EXTVLCOPT:http-referrer={extra_hdrs["Referer"]}')
        lines.append(ch["stream_url"])

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(seen_urls)


def cap_candidate_mirrors(registry, max_per_channel=6):
    """Limits pre-verification candidate mirrors per channel name so dead mirrors don't stall CI."""
    buckets = {}
    for k, item in registry.items():
        norm = normalize_channel_key(item["name"]) or k
        buckets.setdefault(norm, []).append(k)

    kept_keys = set()
    for norm, keys in buckets.items():
        keys.sort(key=lambda x: (0 if not registry[x]["is_external"] else 1, -name_quality_score(registry[x]["name"])))
        kept_keys.update(keys[:max_per_channel])

    return {k: registry[k] for k in kept_keys}


def main():
    start_time = time.monotonic()
    with open("config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    max_pages = min(cfg.get("max_pages_per_query", 15), 15)
    verify = cfg.get("verify_streams", True)
    dedup = cfg.get("deduplicate_channels", True)
    tag_info = cfg.get("tag_stream_info", True)
    skip_raw_ip = cfg.get("skip_raw_ip_streams", True)
    only_match_ext = cfg.get("only_matching_external_languages", True)
    epg_urls = cfg.get("epg_urls", [])
    telugu_keywords = cfg.get("telugu_hub_searches", ["telugu"])
    multi_audio_keywords = cfg.get("multi_audio_indian_keywords", [])
    allowed_langs = {lg.strip().lower() for lg in cfg.get("languages", [])}
    allowed_langs.update(["telugu", "hindi", "tamil", "english"])

    print("Step 1/4: Discovering eja.tv filters...", flush=True)
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

    for term in telugu_keywords:
        tasks.append(("telugu_hub", term.title(), {"max_pages": 6, "search": term}))

    for term in cfg.get("custom_searches", []):
        tasks.append(("searches", term.title(), {"max_pages": 8, "search": term}))

    registry = {}
    membership = {
        "countries": {}, "languages": {}, "categories": {},
        "searches": {}, "telugu_hub": {}, "external": {},
    }

    print(f"Step 2/4: Crawling {len(tasks)} eja.tv queries in parallel...", flush=True)
    with ThreadPoolExecutor(max_workers=10) as pool:
        fmap = {
            pool.submit(crawl_pages, noise, **kwargs): (bucket, lbl)
            for bucket, lbl, kwargs in tasks
        }
        for fut in as_completed(fmap):
            bucket, lbl = fmap[fut]
            found = fut.result()
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
                elif bucket == "telugu_hub":
                    registry[k]["is_telugu"] = True
                    if not registry[k]["language"]:
                        registry[k]["language"] = "Telugu"

                if bucket in membership:
                    membership[bucket].setdefault(lbl, set()).add(k)

    print(f"  eja.tv crawl finished ({len(registry)} unique items in {int(time.monotonic() - start_time)}s).", flush=True)

    print("Step 3/4: Importing external M3U playlists & YouTube Live...", flush=True)
    source_candidate_counts = {}
    ext_sources = cfg.get("external_m3u_sources", [])
    with ThreadPoolExecutor(max_workers=8) as pool:
        fmap = {
            pool.submit(
                parse_external_m3u,
                src, allowed_langs, telugu_keywords, multi_audio_keywords,
                only_match_ext, skip_raw_ip, noise,
            ): src.get("name", "External")
            for src in ext_sources
        }
        for fut in as_completed(fmap):
            src_name = fmap[fut]
            ext_items = fut.result()
            source_candidate_counts[src_name] = len(ext_items)
            print(f"  [{src_name}] -> {len(ext_items)} candidates", flush=True)
            for item in ext_items:
                k = item["key"]
                if k not in registry:
                    registry[k] = item
                else:
                    for fld in ("name", "logo", "country", "language", "category"):
                        if not registry[k][fld] and item[fld]:
                            registry[k][fld] = item[fld]
                    if item.get("extra_headers") and not registry[k].get("extra_headers"):
                        registry[k]["extra_headers"] = item["extra_headers"]
                    if item.get("is_telugu"):
                        registry[k]["is_telugu"] = True
                    if item.get("is_multi_audio_candidate"):
                        registry[k]["is_multi_audio_candidate"] = True

                membership["external"].setdefault(src_name, set()).add(k)
                if item.get("is_telugu"):
                    membership["languages"].setdefault("Telugu", set()).add(k)
                    membership["telugu_hub"].setdefault(src_name, set()).add(k)
                if item.get("country") == "India":
                    membership["countries"].setdefault("India", set()).add(k)

    yt_channels = cfg.get("youtube_live_channels", [])
    if yt_channels:
        print(f"  Resolving {len(yt_channels)} official YouTube Live channels...", flush=True)
        yt_items = resolve_youtube_live_channels(yt_channels)
        source_candidate_counts["YouTube Live"] = len(yt_items)
        print(f"  [YouTube Live] -> {len(yt_items)} live streams", flush=True)
        for item in yt_items:
            k = item["key"]
            registry[k] = item
            membership["external"].setdefault("YouTube Live", set()).add(k)
            if item.get("is_telugu"):
                membership["languages"].setdefault("Telugu", set()).add(k)
                membership["telugu_hub"].setdefault("YouTube Live", set()).add(k)
            membership["countries"].setdefault("India", set()).add(k)

    trimmed_registry = cap_candidate_mirrors(registry, max_per_channel=6)
    total_to_check = len(trimmed_registry)
    print(f"Step 4/4: Deep-verifying {total_to_check} streams (trimmed from {len(registry)}) across 80 threads...", flush=True)

    resolved = {}
    checked = 0
    with ThreadPoolExecutor(max_workers=80) as pool:
        fmap = {
            pool.submit(resolve_item, data, noise, verify, skip_raw_ip): k
            for k, data in trimmed_registry.items()
        }
        for fut in as_completed(fmap):
            checked += 1
            k = fmap[fut]
            info = fut.result()
            if info:
                entry = dict(trimmed_registry[k])
                entry.update(info)
                resolved[k] = entry
            if checked % 150 == 0 or checked == total_to_check:
                print(f"  Progress: {checked}/{total_to_check} checked ({len(resolved)} live)...", flush=True)

    all_working = list(resolved.values())

    print("\n--- Per-Source Live Verification Report ---", flush=True)
    for src_name, keys in membership["external"].items():
        alive = sum(1 for k in keys if k in resolved)
        total_cand = source_candidate_counts.get(src_name, len(keys))
        print(f"  {src_name}: {alive} working / {total_cand} imported", flush=True)
    print("-------------------------------------------\n", flush=True)

    total_all = write_m3u(
        "playlists/all.m3u", all_working,
        epg_urls=epg_urls, deduplicate=dedup, max_mirrors=1, tag_info=tag_info,
    )
    total_backups = write_m3u(
        "playlists/special/with_backups.m3u", all_working,
        epg_urls=epg_urls, deduplicate=True, max_mirrors=4, tag_info=tag_info,
    )

    telugu_keys = set(membership["languages"].get("Telugu", set()))
    for sub_keys in membership["telugu_hub"].values():
        telugu_keys.update(sub_keys)

    telugu_multi_audio_list = []
    for k, ch in resolved.items():
        has_tel_audio = ch.get("has_telugu_audio", False)
        is_tel_meta = ch.get("is_telugu") or is_telugu_channel(
            ch["name"], ch.get("language", ""), ch.get("category", ""), ch["stream_url"], telugu_keywords
        )
        is_pan_india_multi = ch.get("is_multi_audio_candidate") and len(ch.get("audio_langs", [])) > 1

        if has_tel_audio or is_tel_meta or is_pan_india_multi:
            telugu_keys.add(k)
        if has_tel_audio or is_pan_india_multi or (is_tel_meta and len(ch.get("audio_langs", [])) > 1):
            telugu_multi_audio_list.append(ch)

    telugu_channels = [resolved[k] for k in telugu_keys if k in resolved]

    total_telugu_hub = write_m3u(
        "playlists/special/telugu_hub.m3u", telugu_channels,
        epg_urls=epg_urls, deduplicate=dedup, max_mirrors=1, tag_info=tag_info, subgroup_telugu=True,
    )
    total_telugu_backups = write_m3u(
        "playlists/special/telugu_hub_backups.m3u", telugu_channels,
        epg_urls=epg_urls, deduplicate=True, max_mirrors=4, tag_info=tag_info, subgroup_telugu=True,
    )
    total_telugu_multi = write_m3u(
        "playlists/special/telugu_multi_audio.m3u", telugu_multi_audio_list,
        epg_urls=epg_urls, deduplicate=dedup, max_mirrors=2, tag_info=tag_info, subgroup_telugu=True,
    )

    ext_channels = [c for c in all_working if c.get("is_external")]
    total_external = write_m3u(
        "playlists/special/external_verified.m3u", ext_channels,
        epg_urls=epg_urls, deduplicate=dedup, max_mirrors=1, tag_info=tag_info,
    )

    multi_audio_channels = [c for c in all_working if len(c.get("audio_langs", [])) > 1]
    total_multi_audio = write_m3u(
        "playlists/special/multi_audio.m3u", multi_audio_channels,
        epg_urls=epg_urls, deduplicate=dedup, max_mirrors=1, tag_info=tag_info,
    )

    hd_channels = [c for c in all_working if c.get("height", 0) >= 720]
    total_hd = write_m3u(
        "playlists/special/hd_1080p.m3u", hd_channels,
        epg_urls=epg_urls, deduplicate=dedup, max_mirrors=1, tag_info=tag_info,
    )

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
            if bucket == "languages" and lbl == "Telugu":
                clist = telugu_channels
                sub_flag = True
            else:
                clist = [resolved[k] for k in keys if k in resolved]
                sub_flag = False
            fname = f"playlists/{bucket}/{safe_filename(lbl)}.m3u"
            written_count = write_m3u(
                fname, clist,
                epg_urls=epg_urls, deduplicate=dedup, max_mirrors=1, tag_info=tag_info, subgroup_telugu=sub_flag,
            )
            summary[lmap[bucket]].append((lbl, fname, written_count))

    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")
    repo = os.environ.get("GITHUB_REPOSITORY", "Tarke4618/iptv")
    raw_base = f"https://raw.githubusercontent.com/{repo}/main"

    md = [
        "# Verified Multi-Source IPTV Playlists (.m3u)",
        "",
        f"**Last Synced:** {now_ist} (Refreshes every 12 hours — dead links, DRM links, and raw IP:port links stripped automatically)",
        "",
        "### Dedicated Telugu & Multi-Language Telugu Playlists (IPTVnator Ready)",
        "| Playlist | Verified Working Channels | Raw M3U URL |",
        "| :--- | :---: | :--- |",
        f"| **Telugu Master Hub (All Telugu + Multi-Audio Telugu)** | {total_telugu_hub} | {raw_base}/playlists/special/telugu_hub.m3u |",
        f"| **Telugu Master Hub + Backup Mirrors (Up to 4x)** | {total_telugu_backups} | {raw_base}/playlists/special/telugu_hub_backups.m3u |",
        f"| **Telugu Multi-Audio & Kids Only** | {total_telugu_multi} | {raw_base}/playlists/special/telugu_multi_audio.m3u |",
        f"| **Telugu Language Standard List** | {total_telugu_hub} | {raw_base}/playlists/languages/telugu.m3u |",
        "",
        "### Global & Smart Playlists",
        "| Playlist | Verified Working Channels | Raw M3U URL |",
        "| :--- | :---: | :--- |",
        f"| **All Verified Channels (Best Mirror)** | {total_all} | {raw_base}/playlists/all.m3u |",
        f"| **All Channels + Backup Mirrors (Up to 4x)** | {total_backups} | {raw_base}/playlists/special/with_backups.m3u |",
        f"| **All Multi-Audio Channels (Global)** | {total_multi_audio} | {raw_base}/playlists/special/multi_audio.m3u |",
        f"| **HD 720p / 1080p Verified Streams** | {total_hd} | {raw_base}/playlists/special/hd_1080p.m3u |",
        f"| **External GitHub Sources Verified** | {total_external} | {raw_base}/playlists/special/external_verified.m3u |",
        "",
    ]

    for sec, rows in summary.items():
        if rows:
            md.append(f"### By {sec}")
            md.append("| Filter | Verified Working Channels | Raw M3U URL |")
            md.append("| :--- | :---: | :--- |")
            for lbl, rel, cnt in sorted(rows, key=lambda x: x[0].lower()):
                md.append(f"| **{lbl}** | {cnt} | {raw_base}/{rel} |")
            md.append("")

    with open("README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    elapsed = int(time.monotonic() - start_time)
    print(f"Done in {elapsed}s! TeluguHub={total_telugu_hub}, TeluguBackups={total_telugu_backups}, TeluguMulti={total_telugu_multi}, All={total_all}", flush=True)


if __name__ == "__main__":
    main()
