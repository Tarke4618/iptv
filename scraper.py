import base64
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from datetime import datetime, timedelta, timezone
import json
import os
import re
import time
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from bs4 import BeautifulSoup, NavigableString
import requests
from requests.adapters import HTTPAdapter

BASE_URL = "https://eja.tv/"
IPTV_ORG_CHANNELS_API = "https://iptv-org.github.io/api/channels.json"
IPTV_ORG_LOGOS_API = "https://iptv-org.github.io/api/logos.json"
HEALTH_CACHE_PATH = "cache/health_cache.json"

PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

STREAM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

try:
    BeautifulSoup("<html></html>", "lxml")
    BS_PARSER = "lxml"
except Exception:
    BS_PARSER = "html.parser"

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

RAW_IP_PORT_RE = re.compile(r"^https?://\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?/", re.I)

STRICT_TELUGU_RE = re.compile(
    r"(?i)(?:"
    r"\btelugu\b|తెలుగు|"
    r"\betv(?:\s+(?:telugu|andhra|telangana|cinema|plus|abhiruchi|life|bal\s*bharat|news))?\b|"
    r"\bgemini(?:\s+(?:tv|movies|music|comedy|life))?\b|"
    r"\b(?:star\s+)?maa(?:\s+(?:tv|movies|music|gold))?\b|"
    r"\bzee\s+(?:telugu|cinemalu)\b|"
    r"\btv9\s*telugu\b|\bntv\s*telugu\b|\btv5\s*(?:news|telugu)\b|"
    r"\bsakshi(?:\s*tv)?\b|\babn\s*andhra(?:\s*jyothi)?\b|"
    r"\bv6\s*(?:news|telugu)?\b|\b10\s*tv\b|\bhmtv\b|\bt\s*news\b|"
    r"\bmahaa(?:\s+(?:news|max|bhakti))?\b|"
    r"\braj\s+(?:news|musix)\s*telugu\b|\bvissa(?:\s*tv)?\b|\bvanitha(?:\s*tv)?\b|"
    r"\bprime\s*9(?:\s*news)?\b|\b99\s*tv\b|\b6\s*tv\b|\bcvr(?:\s+(?:news|health|spiritual|english))?\b|"
    r"\bsvbc\b|\bbhakti\s*tv\b|\bsubhagruha\b|\bcalvary\s*tv\b|\baradana\b|"
    r"\bkushi\s*tv\b|\btelugu\s*one\b|\bsitara\s*tv\b|\bmango\s*(?:news|music|mobile)\b|"
    r"\bdd\s+(?:yadagiri|saptagiri)\b|\binews\b|\bbig\s*tv\b|\bbrk\s*news\b|\bq\s*news\b|"
    r"\bntv\b|\btv9\b"
    r")"
)

NON_TELUGU_EXCLUDE_RE = re.compile(
    r"(?i)\b(?:"
    r"malayalam|bangla|bengali|tamil|kannada|marathi|gujarati|punjabi|odia|oriya|assamese|bhojpuri|urdu|"
    r"arabic|persian|farsi|kurdish|turkish|russian|spanish|french|german|korean|japanese|chinese|"
    r"kairali|asianet|manorama|mathrubhumi|mazhavil|surya\s*tv|flowers\s*tv|amrita\s*tv|janam\s*tv|"
    r"media\s*one|reporter\s*tv|twentyfour|24\s*news|jeevan\s*tv|kaumudy|safari\s*tv|"
    r"sun\s*tv|vijay\s*tv|kalaignar|jaya\s*tv|polimer|puthiya|thanthi|captain\s*tv|"
    r"somoy|jamuna|ekattor|independent\s*tv|channel\s*i|ntv\s*bangla|atn\s*bangla|rtv\s*bangla|maasranga|"
    r"irib|tehran|kurdsat|fashiontv|fashion\s*tv|son\s*of\s*god|michigan|al\s*jazeera|trt|dw\s*news|france\s*24"
    r")\b"
)


def build_session(pool_size=160):
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


class HealthCache:
    """Cross-run failure cache: cools down dead URLs for 72h after 3 consecutive failures."""

    def __init__(self, path=HEALTH_CACHE_PATH):
        self.path = path
        self.data = {}
        self.now = int(time.time())
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            cutoff = self.now - (14 * 86400)
            self.data = {
                k: v for k, v in raw.items()
                if isinstance(v, dict) and v.get("ts", 0) >= cutoff
            }
        except Exception:
            self.data = {}

    def should_skip(self, url):
        rec = self.data.get(url)
        if not rec:
            return False
        return rec.get("fails", 0) >= 3 and (self.now - rec.get("ts", 0)) < (72 * 3600)

    def mark_alive(self, url):
        self.data.pop(url, None)

    def mark_dead(self, url):
        rec = self.data.get(url, {"fails": 0, "ts": self.now})
        rec["fails"] = min(rec.get("fails", 0) + 1, 10)
        rec["ts"] = self.now
        self.data[url] = rec

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, separators=(",", ":"))


def load_iptv_org_catalog():
    """Loads official iptv-org channel IDs & logos for accurate EPG matching in IPTVnator."""
    catalog = {}
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_ch = pool.submit(SESSION.get, IPTV_ORG_CHANNELS_API, headers=STREAM_HEADERS, timeout=(5, 15))
            f_lg = pool.submit(SESSION.get, IPTV_ORG_LOGOS_API, headers=STREAM_HEADERS, timeout=(5, 15))
            r_ch = f_ch.result()
            r_lg = f_lg.result()

        logo_map = {}
        if r_lg.status_code == 200:
            for lg in r_lg.json():
                cid = lg.get("channel")
                url = lg.get("url")
                if not cid or not url:
                    continue
                fmt = (lg.get("format") or "").upper()
                # Prefer PNG/WebP/JPEG over SVG for maximum IPTV player compatibility
                if cid not in logo_map or (fmt in ("PNG", "WEBP", "JPEG") and logo_map[cid].endswith(".svg")):
                    logo_map[cid] = url

        if r_ch.status_code == 200:
            for ch in r_ch.json():
                cid = ch.get("id")
                cname = ch.get("name")
                if not cid or not cname or ch.get("closed"):
                    continue
                country_code = (ch.get("country") or "").upper()
                cats = ch.get("categories") or []
                entry = {
                    "id": cid,
                    "name": cname,
                    "logo": logo_map.get(cid, ""),
                    "country_code": country_code,
                    "category": cats[0].title() if cats else "",
                }
                names_to_index = [cname] + (ch.get("alt_names") or [])
                for nm in names_to_index:
                    k = normalize_channel_key(nm)
                    if not k:
                        continue
                    # Prioritize Indian channels on key collisions
                    if k not in catalog or (country_code == "IN" and catalog[k]["country_code"] != "IN"):
                        catalog[k] = entry
    except Exception as e:
        print(f"[WARN] iptv-org API catalog load skipped: {e}", flush=True)
    return catalog


def enrich_channel(ch, catalog):
    if not catalog:
        return
    k = normalize_channel_key(ch["name"])
    meta = catalog.get(k)
    if not meta:
        return
    if not ch.get("tvg_id"):
        ch["tvg_id"] = meta["id"]
    if not ch.get("logo") and meta.get("logo"):
        ch["logo"] = meta["logo"]
    if not ch.get("category") and meta.get("category"):
        ch["category"] = meta["category"]


def is_strictly_telugu(name, lang="", group="", country="", explicit_telugu_source=False):
    full_blob = f"{name} | {lang} | {group} | {country}"
    name_and_group = f"{name} | {group}"

    if NON_TELUGU_EXCLUDE_RE.search(name_and_group):
        return False

    lang_tokens = {tok for tok in re.split(r"[^a-z]+", (lang or "").lower()) if tok}
    other_indian_langs = {
        "malayalam", "mal", "bengali", "ben", "bangla", "tamil", "tam",
        "hindi", "hin", "kannada", "kan", "marathi", "mar", "punjabi",
        "pan", "gujarati", "guj", "urdu", "urd", "odia", "ori",
    }
    if lang_tokens & other_indian_langs and not (lang_tokens & {"telugu", "tel"}):
        return False

    if explicit_telugu_source:
        return True

    if lang_tokens & {"telugu", "tel"}:
        return True

    c_low = (country or "").strip().lower()
    if c_low and c_low not in ("india", "in", ""):
        return False

    return bool(STRICT_TELUGU_RE.search(full_blob))


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
    if any(dom in low for dom in ("youtube.com/", "youtu.be/", "twitch.tv/", "dailymotion.com/")):
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
    if any(k in blob for k in ("news", "tv9", "ntv", "tv5", "sakshi", "abn", "v6", "10tv", "hmtv", "t news", "mahaa", "prime9", "99tv", "6tv", "studio n", "raj news", "big tv", "brk", "q news", "inews", "mango news")):
        return "Telugu - News"
    if any(k in blob for k in ("movie", "cinema", "cinemalu", "gold", "sitara")):
        return "Telugu - Movies"
    if any(k in blob for k in ("music", "musix", "beats", "raj musix")):
        return "Telugu - Music"
    if any(k in blob for k in ("svbc", "bhakti", "subhagruha", "calvary", "aradana", "devotional", "hindu", "christian", "spiritual")):
        return "Telugu - Devotional"
    if any(k in blob for k in ("kushi", "bal bharat", "kids")):
        return "Telugu - Kids"
    return "Telugu - Entertainment"


def discover_filters():
    opts = {"country": {}, "language": {}, "category": {}}
    noise = set(NOISE)
    try:
        resp = SESSION.get(BASE_URL, headers=PAGE_HEADERS, timeout=(5, 10))
        soup = BeautifulSoup(resp.text, BS_PARSER)
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
        print(f"[WARN] Filter discovery: {e}", flush=True)

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
    soup = BeautifulSoup(html_text, BS_PARSER)
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
                "tvg_id": "",
                "stream_url": s_url,
                "watch_url": "",
                "logo": logo,
                "country": c_country,
                "language": c_langs,
                "category": default_category,
                "extra_headers": {},
                "source_name": "eja.tv",
                "is_external": False,
                "is_fast": False,
                "explicit_telugu": False,
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
                "tvg_id": "",
                "stream_url": s_url,
                "watch_url": "",
                "logo": "",
                "country": "",
                "language": "",
                "category": default_category,
                "extra_headers": {},
                "source_name": "eja.tv",
                "is_external": False,
                "is_fast": False,
                "explicit_telugu": False,
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
                "tvg_id": "",
                "stream_url": "",
                "watch_url": full,
                "logo": logo,
                "country": c_country,
                "language": c_langs,
                "category": default_category,
                "extra_headers": {},
                "source_name": "eja.tv",
                "is_external": False,
                "is_fast": False,
                "explicit_telugu": False,
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


def crawl_pages(noise, max_pages=12, country="", language="", category="", search=""):
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


def parse_external_m3u(src_cfg, allowed_langs, only_matching_langs, skip_raw_ip, noise):
    src_name = src_cfg.get("name", "External")
    src_url = src_cfg.get("url", "").strip()
    telugu_only = src_cfg.get("telugu_only", False)
    always_keep = src_cfg.get("always_keep", False)
    is_fast = src_cfg.get("is_fast", False)
    def_country = src_cfg.get("default_country", "")
    def_lang = src_cfg.get("default_language", "")

    if not src_url:
        return []

    try:
        resp = SESSION.get(src_url, headers=STREAM_HEADERS, timeout=(5, 12))
        resp.raise_for_status()
    except Exception as e:
        print(f"[WARN] Failed external M3U '{src_name}': {e}", flush=True)
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
            id_m = re.search(r'tvg-id="([^"]*)"', line, re.I)
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
                "tvg_id": id_m.group(1).strip() if id_m else "",
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
                    tel_flag = is_strictly_telugu(
                        ch_name,
                        cur_meta["language"],
                        cur_meta["category"],
                        cur_meta["country"],
                        explicit_telugu_source=telugu_only,
                    )
                    if tel_flag and not cur_meta["language"]:
                        cur_meta["language"] = "Telugu"

                    lang_blob = f"{cur_meta['language']} {cur_meta['category']} {ch_name}".lower()
                    lang_ok = (
                        always_keep
                        or tel_flag
                        or any(lg in lang_blob for lg in allowed_langs)
                    )

                    if not only_matching_langs or lang_ok:
                        results.append({
                            "key": clean_url,
                            "name": ch_name,
                            "tvg_id": cur_meta["tvg_id"],
                            "stream_url": clean_url,
                            "watch_url": "",
                            "logo": cur_meta["logo"],
                            "country": cur_meta["country"],
                            "language": cur_meta["language"],
                            "category": cur_meta["category"],
                            "extra_headers": merged_hdrs,
                            "source_name": src_name,
                            "is_external": True,
                            "is_fast": is_fast,
                            "explicit_telugu": telugu_only,
                        })
            cur_meta = None
            cur_headers = {}

    return results


def inspect_and_verify_hls(url, extra_headers=None, skip_raw_ip=True):
    req_headers = dict(STREAM_HEADERS)
    if extra_headers:
        req_headers.update(extra_headers)

    t0 = time.monotonic()
    try:
        current_url = url
        max_height = 0
        export_m3u8 = url

        lines = []
        playlist_base = current_url
        for hop in range(3):
            if not is_usable_hls_url(current_url, skip_raw_ip=skip_raw_ip):
                return None
            r = SESSION.get(current_url, headers=req_headers, timeout=(2.0, 3.5), allow_redirects=True)
            if r.status_code != 200 or "#EXTM3U" not in r.text[:300]:
                return None

            playlist_base = r.url
            if hop == 0:
                export_m3u8 = r.url

            text = r.text
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

            if any(ln.startswith("#EXT-X-STREAM-INF") for ln in lines):
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
        sr = SESSION.get(seg_url, headers=req_headers, timeout=(2.0, 3.0), allow_redirects=True, stream=True)
        chunk = next(sr.iter_content(chunk_size=256), b"")
        sr.close()

        if sr.status_code in (200, 206) and len(chunk) > 0:
            latency_ms = int((time.monotonic() - t0) * 1000)
            q_tag = f"{max_height}p" if max_height >= 480 else ""
            return {
                "stream_url": export_m3u8,
                "height": max_height,
                "quality_tag": q_tag,
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
                "latency_ms": 999,
            }
        final_name = name if valid_name(name, noise) else humanize_url(info["stream_url"])
        if not valid_name(final_name, noise):
            return None
        info["name"] = final_name
        return info

    try:
        r = SESSION.get(item["watch_url"], headers=PAGE_HEADERS, timeout=(2.5, 5.0), allow_redirects=True)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, BS_PARSER)
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
        ch.get("height", 0),
        1 if ch["stream_url"].startswith("https://") else 0,
        1 if ch.get("tvg_id") else 0,
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

        for fld in ("tvg_id", "logo", "country", "language", "category"):
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
    q_tag = ch.get("quality_tag")
    return f"{base} [{q_tag}]" if q_tag else base


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
        tvg_id = ch.get("tvg_id") or re.sub(r"[^a-zA-Z0-9]+", ".", raw_name.lower()).strip(".")
        group = classify_telugu_group(ch) if subgroup_telugu else (ch.get("category") or ch.get("country") or "General")
        lang_field = ch.get("language", "")

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


def verification_priority(item):
    """Ensures Telugu -> India -> FAST -> Global verification order."""
    if item.get("explicit_telugu") or is_strictly_telugu(
        item.get("name", ""), item.get("language", ""), item.get("category", ""), item.get("country", "")
    ):
        return 0
    if (item.get("country") or "").lower() in ("india", "in"):
        return 1
    if item.get("is_fast"):
        return 2
    return 3


def prepare_verification_queue(registry, health_cache, max_per_channel=5):
    buckets = {}
    skipped_cached = 0
    for k, item in registry.items():
        if health_cache.should_skip(k):
            skipped_cached += 1
            continue
        norm = normalize_channel_key(item["name"]) or k
        buckets.setdefault(norm, []).append(k)

    kept_keys = []
    for norm, keys in buckets.items():
        keys.sort(key=lambda x: (0 if registry[x]["is_external"] else 1, -name_quality_score(registry[x]["name"])))
        kept_keys.extend(keys[:max_per_channel])

    kept_keys.sort(key=lambda x: (verification_priority(registry[x]), -name_quality_score(registry[x]["name"])))
    return [(k, registry[k]) for k in kept_keys], skipped_cached


def main():
    start_time = time.monotonic()
    with open("config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    max_pages = min(cfg.get("max_pages_per_query", 12), 15)
    max_verify_sec = cfg.get("max_verify_seconds", 420)
    max_workers = cfg.get("max_workers", 120)
    verify = cfg.get("verify_streams", True)
    dedup = cfg.get("deduplicate_channels", True)
    tag_info = cfg.get("tag_stream_info", True)
    skip_raw_ip = cfg.get("skip_raw_ip_streams", True)
    only_match_ext = cfg.get("only_matching_external_languages", True)
    epg_urls = cfg.get("epg_urls", [])
    telugu_keywords = cfg.get("telugu_hub_searches", ["telugu"])
    allowed_langs = {lg.strip().lower() for lg in cfg.get("languages", [])}
    allowed_langs.update(["telugu", "hindi", "tamil", "english"])

    print(f"Step 1/5: Loading iptv-org metadata catalog & eja.tv filters (parser={BS_PARSER})...", flush=True)
    health_cache = HealthCache()
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_cat = pool.submit(load_iptv_org_catalog)
        f_flt = pool.submit(discover_filters)
        catalog = f_cat.result()
        discovered, noise = f_flt.result()
    print(f"  Catalog indexed {len(catalog)} official channel signatures for EPG & logo enrichment.", flush=True)

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
        tasks.append(("telugu_search", term.title(), {"max_pages": 5, "search": term}))

    for term in cfg.get("custom_searches", []):
        tasks.append(("searches", term.title(), {"max_pages": 6, "search": term}))

    registry = {}
    membership = {
        "countries": {}, "languages": {}, "categories": {},
        "searches": {}, "external": {},
    }

    print(f"Step 2/5: Crawling {len(tasks)} eja.tv queries in parallel...", flush=True)
    with ThreadPoolExecutor(max_workers=14) as pool:
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

                if bucket in membership:
                    membership[bucket].setdefault(lbl, set()).add(k)

    print(f"  eja.tv crawl finished ({len(registry)} unique items in {int(time.monotonic() - start_time)}s).", flush=True)

    print("Step 3/5: Importing 28 external public M3U repositories...", flush=True)
    source_candidate_counts = {}
    ext_sources = cfg.get("external_m3u_sources", [])
    with ThreadPoolExecutor(max_workers=14) as pool:
        fmap = {
            pool.submit(
                parse_external_m3u,
                src, allowed_langs, only_match_ext, skip_raw_ip, noise,
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
                    for fld in ("name", "tvg_id", "logo", "country", "language", "category"):
                        if not registry[k].get(fld) and item.get(fld):
                            registry[k][fld] = item[fld]
                    if item.get("extra_headers") and not registry[k].get("extra_headers"):
                        registry[k]["extra_headers"] = item["extra_headers"]
                    if item.get("explicit_telugu"):
                        registry[k]["explicit_telugu"] = True
                    if item.get("is_fast"):
                        registry[k]["is_fast"] = True

                membership["external"].setdefault(src_name, set()).add(k)
                if (item.get("country") or "").lower() in ("india", "in"):
                    membership["countries"].setdefault("India", set()).add(k)

    queue, skipped_cached = prepare_verification_queue(registry, health_cache, max_per_channel=5)
    total_to_check = len(queue)
    print(
        f"Step 4/5: Priority-verifying {total_to_check} streams "
        f"(skipped {skipped_cached} cached dead URLs) across {max_workers} threads (max {max_verify_sec}s)...",
        flush=True,
    )

    resolved = {}
    checked = 0
    pool = ThreadPoolExecutor(max_workers=max_workers)
    fmap = {
        pool.submit(resolve_item, data, noise, verify, skip_raw_ip): (k, data)
        for k, data in queue
    }
    try:
        for fut in as_completed(fmap, timeout=max_verify_sec):
            checked += 1
            k, data = fmap[fut]
            try:
                info = fut.result(timeout=1)
            except Exception:
                info = None
            if info:
                entry = dict(data)
                entry.update(info)
                enrich_channel(entry, catalog)
                resolved[k] = entry
                health_cache.mark_alive(k)
            else:
                health_cache.mark_dead(k)

            if checked % 200 == 0 or checked == total_to_check:
                print(f"  Progress: {checked}/{total_to_check} checked ({len(resolved)} live)...", flush=True)
    except TimeoutError:
        print(
            f"  [INFO] Reached {max_verify_sec}s budget at {checked}/{total_to_check} — "
            f"saving {len(resolved)} verified live streams!",
            flush=True,
        )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    health_cache.save()
    all_working = list(resolved.values())

    print("Step 5/5: Writing EPG-enriched M3U playlists & health reports...", flush=True)
    source_stats = []
    for src_name, keys in sorted(membership["external"].items()):
        alive = sum(1 for k in keys if k in resolved)
        total_cand = source_candidate_counts.get(src_name, len(keys))
        source_stats.append({"source": src_name, "alive": alive, "imported": total_cand})
        print(f"  {src_name}: {alive} working / {total_cand} imported", flush=True)

    total_all = write_m3u(
        "playlists/all.m3u", all_working,
        epg_urls=epg_urls, deduplicate=dedup, max_mirrors=1, tag_info=tag_info,
    )
    total_backups = write_m3u(
        "playlists/special/with_backups.m3u", all_working,
        epg_urls=epg_urls, deduplicate=True, max_mirrors=3, tag_info=tag_info,
    )

    telugu_channels = []
    for ch in all_working:
        if is_strictly_telugu(
            ch["name"],
            ch.get("language", ""),
            ch.get("category", ""),
            ch.get("country", ""),
            explicit_telugu_source=ch.get("explicit_telugu", False),
        ):
            ch_copy = dict(ch)
            ch_copy["language"] = "Telugu"
            telugu_channels.append(ch_copy)

    total_telugu_hub = write_m3u(
        "playlists/special/telugu_hub.m3u", telugu_channels,
        epg_urls=epg_urls, deduplicate=dedup, max_mirrors=1, tag_info=tag_info, subgroup_telugu=True,
    )
    total_telugu_backups = write_m3u(
        "playlists/special/telugu_hub_backups.m3u", telugu_channels,
        epg_urls=epg_urls, deduplicate=True, max_mirrors=4, tag_info=tag_info, subgroup_telugu=True,
    )

    india_channels = [c for c in all_working if (c.get("country") or "").lower() in ("india", "in")]
    total_india = write_m3u(
        "playlists/special/india_all.m3u", india_channels,
        epg_urls=epg_urls, deduplicate=dedup, max_mirrors=1, tag_info=tag_info,
    )

    fast_channels = [c for c in all_working if c.get("is_fast")]
    total_fast = write_m3u(
        "playlists/special/fast_movies_tv.m3u", fast_channels,
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
    epg_matched = sum(1 for c in all_working if c.get("tvg_id"))
    epg_pct = int((epg_matched / len(all_working)) * 100) if all_working else 0
    elapsed = int(time.monotonic() - start_time)

    os.makedirs("playlists", exist_ok=True)
    with open("playlists/health.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "last_synced_ist": now_ist,
                "elapsed_seconds": elapsed,
                "totals": {
                    "all": total_all,
                    "telugu_hub": total_telugu_hub,
                    "telugu_backups": total_telugu_backups,
                    "india_all": total_india,
                    "fast_movies_tv": total_fast,
                    "hd_1080p": total_hd,
                    "epg_match_percent": epg_pct,
                },
                "sources": source_stats,
            },
            f,
            indent=2,
        )

    repo = os.environ.get("GITHUB_REPOSITORY", "Tarke4618/iptv")
    raw_base = f"https://raw.githubusercontent.com/{repo}/main"

    md = [
        "# Verified Multi-Source IPTV Playlists (.m3u)",
        "",
        f"**Last Synced:** `{now_ist}` | **Build Time:** `{elapsed}s` | **EPG & Logo Match:** `{epg_pct}%`",
        "",
        "### Dedicated Telugu & India Playlists (Strict Filter — IPTVnator Ready)",
        "| Playlist | Verified Channels | Raw M3U URL |",
        "| :--- | :---: | :--- |",
        f"| **Telugu Master Hub (Clean Best Mirror)** | {total_telugu_hub} | `{raw_base}/playlists/special/telugu_hub.m3u` |",
        f"| **Telugu Master Hub + Backup Mirrors (Up to 4x)** | {total_telugu_backups} | `{raw_base}/playlists/special/telugu_hub_backups.m3u` |",
        f"| **All India Verified Channels** | {total_india} | `{raw_base}/playlists/special/india_all.m3u` |",
        f"| **Telugu Language Standard List** | {total_telugu_hub} | `{raw_base}/playlists/languages/telugu.m3u` |",
        "",
        "### FAST Movies, Global & HD Smart Playlists",
        "| Playlist | Verified Channels | Raw M3U URL |",
        "| :--- | :---: | :--- |",
        f"| **FAST Free Movies & Live TV (LG, Samsung, Roku, Tubi, DistroTV, Fire TV)** | {total_fast} | `{raw_base}/playlists/special/fast_movies_tv.m3u` |",
        f"| **All Verified Channels (Best Mirror)** | {total_all} | `{raw_base}/playlists/all.m3u` |",
        f"| **All Channels + Backup Mirrors (Up to 3x)** | {total_backups} | `{raw_base}/playlists/special/with_backups.m3u` |",
        f"| **HD 720p / 1080p Verified Streams** | {total_hd} | `{raw_base}/playlists/special/hd_1080p.m3u` |",
        "",
    ]

    for sec, rows in summary.items():
        if rows:
            md.append(f"### By {sec}")
            md.append("| Filter | Verified Channels | Raw M3U URL |")
            md.append("| :--- | :---: | :--- |")
            for lbl, rel, cnt in sorted(rows, key=lambda x: x[0].lower()):
                md.append(f"| **{lbl}** | {cnt} | `{raw_base}/{rel}` |")
            md.append("")

    with open("README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print(
        f"Done in {elapsed}s! Telugu={total_telugu_hub}, India={total_india}, "
        f"FAST={total_fast}, HD={total_hd}, All={total_all}, EPG={epg_pct}%",
        flush=True,
    )


if __name__ == "__main__":
    main()
