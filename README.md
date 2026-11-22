# Verified Multi-Source IPTV Playlists (Rust v5.0.0)

**Last Synced:** `22 Nov 2026` | **Build Time:** `~90s` | **Architecture:** Rust 1.80+ (Edition 2024) · Tokio · reqwest (rustls) · scraper

---

### Dedicated Telugu & India Playlists (Strict Filter — IPTVnator Ready)
| Playlist | Verified Channels | Raw M3U URL |
| :--- | :---: | :--- |
| **Telugu Master Hub (Clean Best Mirror)** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/special/telugu_hub.m3u` |
| **Telugu Master Hub + Backup Mirrors (Up to 4x)** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/special/telugu_hub_backups.m3u` |
| **All India Verified Channels** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/special/india_all.m3u` |
| **Telugu Language Standard List** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/languages/telugu.m3u` |

---

### FAST Movies, Sports, Kodi & Global Smart Playlists
| Playlist | Verified Channels | Raw M3U URL |
| :--- | :---: | :--- |
| **FAST Movies & Live TV (LG, Samsung, Vizio, Roku, Tubi, Whale, Kodi)** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/special/fast_movies_tv.m3u` |
| **Live Sports Hub (Cricket, Football, Global Sports)** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/special/sports_live.m3u` |
| **All Verified Channels (Best Mirror)** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/all.m3u` |
| **All Channels + Backup Mirrors (Up to 3x)** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/special/with_backups.m3u` |
| **HD 720p / 1080p Verified Streams** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/special/hd_1080p.m3u` |

---

### By Country
| Filter | Verified Channels | Raw M3U URL |
| :--- | :---: | :--- |
| **Australia** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/countries/australia.m3u` |
| **Canada** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/countries/canada.m3u` |
| **India** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/countries/india.m3u` |
| **United Kingdom** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/countries/united_kingdom.m3u` |
| **United States** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/countries/united_states.m3u` |

---

### By Language
| Filter | Verified Channels | Raw M3U URL |
| :--- | :---: | :--- |
| **English** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/languages/english.m3u` |
| **Hindi** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/languages/hindi.m3u` |
| **Spanish** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/languages/spanish.m3u` |
| **Tamil** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/languages/tamil.m3u` |
| **Telugu** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/languages/telugu.m3u` |

---

### By Category
| Filter | Verified Channels | Raw M3U URL |
| :--- | :---: | :--- |
| **Documentary** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/categories/documentary.m3u` |
| **Entertainment** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/categories/entertainment.m3u` |
| **Movie** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/categories/movie.m3u` |
| **Music** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/categories/music.m3u` |
| **News** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/categories/news.m3u` |
| **Sports** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/categories/sports.m3u` |

---

### By Custom Search
| Filter | Verified Channels | Raw M3U URL |
| :--- | :---: | :--- |
| **Cartoon** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/searches/cartoon.m3u` |
| **Cricket** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/searches/cricket.m3u` |
| **Discovery** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/searches/discovery.m3u` |
| **History** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/searches/history.m3u` |
| **National Geographic** | — | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/searches/national_geographic.m3u` |

---

### Geo-Blocked Playlists (403/451 Streams — Preserved, Not Marked Dead)
| Filter | Raw M3U URL |
| :--- | :--- |
| **United States** | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/geoblocked/united_states.m3u` |
| **India** | `https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/geoblocked/india.m3u` |
| *(auto-generated per country)* | `playlists/geoblocked/<country>.m3u` |

---

## Architecture Highlights (Rust v5)

| Feature | Implementation |
|---|---|
| **Concurrency** | 450+ async workers, fair round-robin per-host (4 concurrent/host via `Semaphore`) |
| **Language Isolation** | Universal `LanguageClassifier` (positive/negative patterns + IPTV-org catalog enrichment, ISO 639-3) — zero cross-language leakage |
| **Deduplication** | Best-mirror selection preserving distinguishing tokens (News, Movies, HD, FHD, 4K, +1, etc.) |
| **Geo-Blocked Handling** | 403/451 streams → per-country `playlists/geoblocked/<country>.m3u`, never added to dead cache |
| **Health Cache** | `cache/health_cache.json` — 72-hour cooldown after 3 consecutive failures |
| **Master URL Preservation** | Keeps `.m3u8` master playlists for multi-audio track support (e.g., History TV18) |
| **TLS** | `rustls` only — no OpenSSL system dependency |
| **Schedule** | GitHub Actions every 12 hours (`0 */12 * * *`) |

---

## Local Development

```bash
# Build
cargo build --release

# Dry run (no playlist write)
cargo run --release -- --config config.json --dry-run

# Full run
cargo run --release -- --config config.json

# Lint
cargo fmt --check && cargo clippy -- -D warnings

# Test
cargo test
```

---

## Configuration

`config.json` controls:
- `external_m3u_sources` — 70+ global M3U playlists (language, country, category, FAST)
- `epg_urls` — EPG sources for enrichment
- `countries` / `languages` / `categories` — target filters
- `telugu_hub_searches` / `custom_searches` — EJA.tv query terms
- `max_workers` (450), `max_verify_seconds` (300), `verify_streams`, `deduplicate_channels`
- `skip_raw_ip_streams` — set `false` to preserve valid regional ISP mirrors

---

## Output Structure

```
playlists/
├── all.m3u                              # Best mirror per channel
├── special/
│   ├── telugu_hub.m3u                   # Telugu master hub
│   ├── telugu_hub_backups.m3u           # Up to 4 mirrors per channel
│   ├── india_all.m3u                    # All India verified
│   ├── fast_movies_tv.m3u               # FAST platforms
│   ├── sports_live.m3u                  # Live sports
│   ├── with_backups.m3u                 # All + backup mirrors
│   └── hd_1080p.m3u                     # 720p/1080p only
├── languages/
│   ├── english.m3u
│   ├── hindi.m3u
│   ├── telugu.m3u
│   ├── tamil.m3u
│   ├── spanish.m3u
│   └── ... (25+ languages)
├── countries/
│   ├── india.m3u
│   ├── united_states.m3u
│   ├── united_kingdom.m3u
│   └── ...
├── categories/
│   ├── news.m3u
│   ├── sports.m3u
│   ├── movie.m3u
│   └── ...
├── searches/
│   ├── cricket.m3u
│   ├── discovery.m3u
│   └── ...
└── geoblocked/
    ├── united_states.m3u    # 403/451 streams
    ├── india.m3u
    └── ...
```

---

## CI Pipeline (`.github/workflows/update_playlists.yml`)

1. **Setup** — Rust stable toolchain + `rustfmt` + `clippy`, cached deps
2. **Pre-flight** — `cargo fmt --check`, `cargo clippy -- -D warnings`, `cargo build --release`
3. **Run** — `cargo run --release -- --config config.json`
4. **Validate** — ≥50 live channels in `playlists/all.m3u`, per-language counts in job summary
5. **Commit** — Auto-commit updated playlists + `cache/health_cache.json` + `playlists/index.json`

---

## License

MIT — Free for personal use. Streams belong to their respective owners.