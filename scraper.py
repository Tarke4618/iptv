import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urljoin
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://eja.tv/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://eja.tv/"
}

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def safe_filename(name):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_").lower()
    return cleaned or "general"

def fetch_filter_options():
    """Scrapes all available Country, Language, and Category dropdown options from eja.tv."""
    resp = requests.get(BASE_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    options = {"country": {}, "language": {}, "category": {}}
    for field in options.keys():
        select = soup.find("select", {"name": field})
        if select:
            for opt in select.find_all("option"):
                val = (opt.get("value") or "").strip()
                label = opt.get_text(strip=True)
                if val and label:
                    options[field][val.lower()] = (val, label)
                    options[field][label.lower()] = (val, label)
    return options

def query_eja(limit=200, country="", language="", category="", search=""):
    """Queries eja.tv with specific filter parameters and extracts candidate channel links."""
    params = {
        "limit": limit,
        "country": country,
        "language": language,
        "category": category,
        "search": search
    }
    try:
        resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[WARN] Failed query {params}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    candidates = []
    seen_urls = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if not href or href.startswith("#") or "limit=" in href or "country=" in href:
            continue

        full_url = urljoin(BASE_URL, href)
        # Channel watch links on eja.tv use query hashes like /?<32char>.m3u8 or /?<id>
        if "eja.tv/?" in full_url and full_url not in seen_urls:
            seen_urls.add(full_url)
            img = a_tag.find("img")
            logo = urljoin(BASE_URL, img["src"]) if img and img.get("src") else ""
            name = (
                a_tag.get("title")
                or (img.get("alt") if img else "")
                or a_tag.get_text(" ", strip=True)
                or "Unknown Channel"
            ).strip()
            candidates.append({
                "name": name.replace(",", " "),
                "watch_url": full_url,
                "logo": logo
            })
    return candidates

def resolve_stream(candidate, verify=True):
    """Resolves an eja.tv watch URL into a direct, working .m3u8 stream URL."""
    watch_url = candidate["watch_url"]
    try:
        resp = requests.get(watch_url, headers=HEADERS, timeout=10, allow_redirects=True, stream=True)
        final_url = resp.url
        content_type = (resp.headers.get("Content-Type") or "").lower()

        # Case 1: Direct redirect to an .m3u8 manifest or HLS stream
        if ".m3u8" in final_url.lower() or "mpegurl" in content_type:
            first_chunk = next(resp.iter_content(chunk_size=512), b"").decode("utf-8", errors="ignore")
            resp.close()
            if "#EXTM3U" in first_chunk or not verify:
                return final_url

        # Case 2: Watch page HTML containing an embedded .m3u8 URL
        html_body = resp.text
        resp.close()
        matches = re.findall(r'https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*', html_body)
        if matches:
            stream_url = matches[0]
            if not verify:
                return stream_url
            check = requests.get(stream_url, headers=HEADERS, timeout=8, stream=True)
            chunk = next(check.iter_content(chunk_size=512), b"").decode("utf-8", errors="ignore")
            check.close()
            if check.status_code == 200 and "#EXTM3U" in chunk:
                return stream_url
    except Exception:
        return None
    return None

def write_m3u(filepath, channels):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    lines = ["#EXTM3U"]
    for ch in sorted(channels, key=lambda x: x["name"].lower()):
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
        lines.append(ch["stream_url"])

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def build_readme(generated_files):
    repo = os.environ.get("GITHUB_REPOSITORY", "<YOUR_USERNAME>/<YOUR_REPO>")
    branch = "main"
    raw_base = f"https://raw.githubusercontent.com/{repo}/{branch}"

    md = [
        "# Auto-Updating eja.tv IPTV Playlists (.m3u)",
        "",
        "Automatically refreshed every **12 hours** via GitHub Actions. Copy any **Raw M3U URL** below and paste it into your IPTV player (TiviMate, OTT Navigator, Televizo, VLC, Kodi).",
        "",
        "### Master Playlist (All Channels with Country, Language & Category Tags)",
        f"- **All Channels:** `{raw_base}/playlists/all.m3u`",
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
    limit = config.get("max_channels_per_query", 250)
    verify = config.get("verify_streams", True)
    scrape_all = config.get("scrape_all_filters", False)

    print("Fetching available filter lists from eja.tv...")
    dropdowns = fetch_filter_options()

    # Build task list: (bucket_type, display_label, query_kwargs)
    tasks = [("general", "Global Top", {"limit": limit})]

    if scrape_all:
        for val, label in set(dropdowns["country"].values()):
            tasks.append(("countries", label, {"limit": limit, "country": val}))
        for val, label in set(dropdowns["language"].values()):
            tasks.append(("languages", label, {"limit": limit, "language": val}))
        for val, label in set(dropdowns["category"].values()):
            tasks.append(("categories", label, {"limit": limit, "category": val}))
    else:
        for c in config.get("countries", []):
            val, label = dropdowns["country"].get(c.lower(), (c, c.upper()))
            tasks.append(("countries", label, {"limit": limit, "country": val}))
        for l in config.get("languages", []):
            val, label = dropdowns["language"].get(l.lower(), (l, l))
            tasks.append(("languages", label, {"limit": limit, "language": val}))
        for cat in config.get("categories", []):
            val, label = dropdowns["category"].get(cat.lower(), (cat, cat))
            tasks.append(("categories", label, {"limit": limit, "category": val}))

    for term in config.get("custom_searches", []):
        tasks.append(("searches", term.title(), {"limit": limit, "search": term}))

    # Collect candidates across all queries
    watch_registry = {}
    for bucket, label, q_kwargs in tasks:
        print(f"Querying [{bucket}] -> {label}...")
        items = query_eja(**q_kwargs)
        for item in items:
            w_url = item["watch_url"]
            if w_url not in watch_registry:
                watch_registry[w_url] = {
                    "name": item["name"],
                    "watch_url": w_url,
                    "logo": item["logo"],
                    "country": "",
                    "language": "",
                    "category": "",
                    "buckets": []
                }
            entry = watch_registry[w_url]
            entry["buckets"].append((bucket, label))
            if bucket == "countries":
                entry["country"] = label
            elif bucket == "languages":
                entry["language"] = label
            elif bucket == "categories":
                entry["category"] = label

    print(f"Resolving & verifying {len(watch_registry)} unique candidate streams...")
    working_channels = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        future_map = {
            pool.submit(resolve_stream, data, verify): data
            for data in watch_registry.values()
        }
        for future in as_completed(future_map):
            data = future_map[future]
            stream_url = future.result()
            if stream_url:
                data["stream_url"] = stream_url
                working_channels.append(data)

    print(f"Verified {len(working_channels)} working live streams.")

    # Write Master Playlist
    write_m3u("playlists/all.m3u", working_channels)

    # Group into separate playlists by Country, Language, Category, and Custom Search
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

    build_readme(generated_summary)
    print("All playlists and README dashboard updated successfully.")

if __name__ == "__main__":
    main()
