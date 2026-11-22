use anyhow::Result;
use base64::Engine;
use reqwest::Client;
use scraper::{Html, Selector};
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Duration;
use tracing::{info, warn};
use url::Url;

use crate::config::Config;
use crate::language_classifier::LanguageClassifier;
use crate::models::Channel;

const BASE_URL: &str = "https://eja.tv/";
const DEFAULT_UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

pub struct EjaTvCrawler {
    config: Config,
    client: Client,
    #[allow(dead_code)]
    lang_classifier: Arc<LanguageClassifier>,
}

impl EjaTvCrawler {
    pub fn new(config: Config, lang_classifier: Arc<LanguageClassifier>) -> Self {
        let client = Client::builder()
            .user_agent(DEFAULT_UA)
            .timeout(Duration::from_secs(25))
            .build()
            .expect("Failed to create HTTP client");

        Self {
            config,
            client,
            lang_classifier,
        }
    }

    pub async fn crawl_all(&self, lang_classifier: &LanguageClassifier) -> Result<Vec<Channel>> {
        let mut all_channels: HashMap<String, Channel> = HashMap::new();
        let noise = self.build_noise_set();

        // Build crawl tasks
        let mut tasks = Vec::new();

        // Global feed
        tasks.push(CrawlTask {
            bucket: "general",
            label: "Global Feed".to_string(),
            max_pages: self.config.max_pages_per_query,
            country: None,
            language: None,
            category: None,
            query: None,
        });

        // Countries
        for c in &self.config.countries {
            let (code, label) = self.resolve_country(c);
            tasks.push(CrawlTask {
                bucket: "countries",
                label,
                max_pages: self.config.max_pages_per_query,
                country: Some(code),
                language: None,
                category: None,
                query: None,
            });
        }

        // Languages
        for l in &self.config.languages {
            let (code, label) = self.resolve_language(l);
            tasks.push(CrawlTask {
                bucket: "languages",
                label,
                max_pages: self.config.max_pages_per_query,
                country: None,
                language: Some(code),
                category: None,
                query: None,
            });
        }

        // Categories
        for cat in &self.config.categories {
            tasks.push(CrawlTask {
                bucket: "categories",
                label: cat.clone(),
                max_pages: self.config.max_pages_per_query,
                country: None,
                language: None,
                category: Some(cat.clone()),
                query: None,
            });
        }

        // Telugu hub searches
        for term in &self.config.telugu_hub_searches {
            tasks.push(CrawlTask {
                bucket: "telugu_search",
                label: title_case(term),
                max_pages: 5,
                country: None,
                language: None,
                category: None,
                query: Some(term.clone()),
            });
        }

        // Custom searches
        for term in &self.config.custom_searches {
            tasks.push(CrawlTask {
                bucket: "searches",
                label: title_case(term),
                max_pages: 6,
                country: None,
                language: None,
                category: None,
                query: Some(term.clone()),
            });
        }

        info!("Crawling {} EJA.tv tasks...", tasks.len());

        // Execute with semaphore for concurrency control
        let semaphore = Arc::new(tokio::sync::Semaphore::new(20));
        let mut handles = Vec::new();

        for task in tasks {
            let sem = semaphore.clone();
            let client = self.client.clone();
            let noise = noise.clone();
            let lang_classifier = lang_classifier.clone();

            let handle = tokio::spawn(async move {
                let _permit = sem.acquire().await.unwrap();
                let channels = crawl_task(&client, &task, &noise, &lang_classifier).await;
                (task.bucket, task.label, channels)
            });
            handles.push(handle);
        }

        for handle in handles {
            let (_bucket, _label, channels) = handle.await?;
            for ch in channels {
                all_channels
                    .entry(ch.stream_url.clone())
                    .and_modify(|existing| existing.merge(&ch))
                    .or_insert(ch);
            }
        }

        Ok(all_channels.into_values().collect())
    }

    fn build_noise_set(&self) -> HashSet<String> {
        let mut noise = HashSet::new();

        // Base noise words
        for w in [
            "eja",
            "eja.tv",
            "home",
            "tv",
            "live",
            "live tv",
            "channel",
            "channels",
            "stream",
            "streams",
            "watch",
            "play",
            "player",
            "video",
            "hd",
            "sd",
            "fhd",
            "next",
            "previous",
            "prev",
            "more",
            "all",
            "search",
            "filter",
            "menu",
            "sign languages",
            "sign language",
            "unknown",
            "untitled",
            "index",
            "playlist",
            "master",
            "chunklist",
            "manifest",
        ] {
            noise.insert(w.to_string());
        }

        // Country aliases
        let country_aliases = [
            ("gb", vec!["uk", "united kingdom"]),
            ("in", vec!["india"]),
            ("us", vec!["usa", "united states"]),
            ("ca", vec!["canada"]),
            ("au", vec!["australia"]),
        ];
        for (code, aliases) in country_aliases {
            noise.insert(code.to_string());
            for a in aliases {
                noise.insert(a.to_string());
            }
        }

        // Language aliases
        let lang_aliases = [
            ("english", ["eng"]),
            ("hindi", ["hin"]),
            ("telugu", ["tel"]),
            ("tamil", ["tam"]),
            ("spanish", ["spa"]),
            ("french", ["fra"]),
            ("german", ["deu"]),
            ("arabic", ["ara"]),
            ("russian", ["rus"]),
            ("portuguese", ["por"]),
            ("italian", ["ita"]),
            ("japanese", ["jpn"]),
            ("korean", ["kor"]),
            ("chinese", ["zho"]),
            ("bengali", ["ben"]),
            ("marathi", ["mar"]),
            ("gujarati", ["guj"]),
            ("punjabi", ["pan"]),
            ("malayalam", ["mal"]),
            ("kannada", ["kan"]),
            ("urdu", ["urd"]),
        ];
        for (lang, aliases) in lang_aliases {
            noise.insert(lang.to_string());
            for a in aliases {
                noise.insert(a.to_string());
            }
        }

        noise
    }

    fn resolve_country(&self, input: &str) -> (String, String) {
        let lower = input.to_lowercase().trim().to_string();
        match lower.as_str() {
            "gb" | "uk" | "united kingdom" => ("gb".to_string(), "United Kingdom".to_string()),
            "in" | "india" => ("in".to_string(), "India".to_string()),
            "us" | "usa" | "united states" => ("us".to_string(), "United States".to_string()),
            "ca" | "canada" => ("ca".to_string(), "Canada".to_string()),
            "au" | "australia" => ("au".to_string(), "Australia".to_string()),
            _ => (lower.clone(), title_case(&lower)),
        }
    }

    fn resolve_language(&self, input: &str) -> (String, String) {
        let lower = input.to_lowercase().trim().to_string();
        match lower.as_str() {
            "english" | "eng" => ("eng".to_string(), "English".to_string()),
            "hindi" | "hin" => ("hin".to_string(), "Hindi".to_string()),
            "telugu" | "tel" => ("tel".to_string(), "Telugu".to_string()),
            "tamil" | "tam" => ("tam".to_string(), "Tamil".to_string()),
            "spanish" | "spa" => ("spa".to_string(), "Spanish".to_string()),
            "french" | "fra" => ("fra".to_string(), "French".to_string()),
            "german" | "deu" => ("deu".to_string(), "German".to_string()),
            "arabic" | "ara" => ("ara".to_string(), "Arabic".to_string()),
            "russian" | "rus" => ("rus".to_string(), "Russian".to_string()),
            "portuguese" | "por" => ("por".to_string(), "Portuguese".to_string()),
            "italian" | "ita" => ("ita".to_string(), "Italian".to_string()),
            "japanese" | "jpn" => ("jpn".to_string(), "Japanese".to_string()),
            "korean" | "kor" => ("kor".to_string(), "Korean".to_string()),
            "chinese" | "zho" => ("zho".to_string(), "Chinese".to_string()),
            "bengali" | "ben" | "bangla" => ("ben".to_string(), "Bengali".to_string()),
            "marathi" | "mar" => ("mar".to_string(), "Marathi".to_string()),
            "gujarati" | "guj" => ("guj".to_string(), "Gujarati".to_string()),
            "punjabi" | "pan" => ("pan".to_string(), "Punjabi".to_string()),
            "malayalam" | "mal" => ("mal".to_string(), "Malayalam".to_string()),
            "kannada" | "kan" => ("kan".to_string(), "Kannada".to_string()),
            "urdu" | "urd" => ("urd".to_string(), "Urdu".to_string()),
            _ => (lower.clone(), title_case(&lower)),
        }
    }
}

struct CrawlTask {
    bucket: &'static str,
    label: String,
    max_pages: usize,
    country: Option<String>,
    language: Option<String>,
    category: Option<String>,
    query: Option<String>,
}

async fn crawl_task(
    client: &Client,
    task: &CrawlTask,
    noise: &HashSet<String>,
    lang_classifier: &LanguageClassifier,
) -> Vec<Channel> {
    let mut items: HashMap<String, Channel> = HashMap::new();
    let mut offset = 0;
    let mut page = 1;
    let mut use_page = false;
    let mut _count = 0;

    let link_selector = Selector::parse("a[href]").unwrap();
    let img_selector = Selector::parse("img[src]").unwrap();

    for _ in 0..task.max_pages {
        let mut query_params: Vec<(String, String)> = Vec::new();
        if let Some(c) = &task.country {
            query_params.push(("country".to_string(), c.clone()));
        }
        if let Some(l) = &task.language {
            query_params.push(("language".to_string(), l.clone()));
        }
        if let Some(cat) = &task.category {
            query_params.push(("category".to_string(), cat.clone()));
        }
        if let Some(q) = &task.query {
            query_params.push(("search".to_string(), q.clone()));
        }
        if use_page && page > 1 {
            query_params.push(("page".to_string(), page.to_string()));
        } else if !use_page && offset > 0 {
            query_params.push(("offset".to_string(), offset.to_string()));
        }

        let url = Url::parse_with_params(BASE_URL, &query_params).unwrap();

        let resp = match client.get(url.clone()).send().await {
            Ok(r) if r.status().is_success() => r,
            Ok(r) => {
                warn!("EJA.tv {} returned status {}", task.label, r.status());
                break;
            }
            Err(e) => {
                warn!("EJA.tv {} request failed: {}", task.label, e);
                break;
            }
        };

        let final_url = resp.url().clone();
        let html_text = match resp.text().await {
            Ok(t) => t,
            Err(e) => {
                warn!("EJA.tv {} read failed: {}", task.label, e);
                break;
            }
        };

        let document = Html::parse_document(&html_text);

        // Extract M3U8 URLs from page
        let m3u8_urls = extract_m3u8_from_text(&html_text);
        for m3u8 in &m3u8_urls {
            if items.contains_key(m3u8) {
                continue;
            }
            let name = humanize_url(m3u8);
            let (language, country) = lang_classifier.classify(&name, "", "", "");
            items.insert(
                m3u8.clone(),
                Channel {
                    stream_url: m3u8.clone(),
                    name,
                    tvg_id: String::new(),
                    logo: String::new(),
                    country,
                    language,
                    category: task.category.clone().unwrap_or_default(),
                    extra_headers: Default::default(),
                    source_name: "eja.tv".to_string(),
                    is_external: false,
                    is_fast: false,
                    explicit_telugu: false,
                    height: 0,
                    quality_tag: String::new(),
                    latency_ms: 0,
                    watch_url: String::new(),
                    is_master_m3u8: true,
                    audio_tracks: Vec::new(),
                },
            );
        }

        // Extract links to channel pages
        let mut added = 0;
        for link in document.select(&link_selector) {
            let href = link.value().attr("href").unwrap_or("");
            if href.is_empty() || href.starts_with('#') || href.starts_with("javascript:") {
                continue;
            }

            let full_url = final_url.join(href).unwrap_or_else(|_| final_url.clone());
            let parsed = match Url::parse(full_url.as_str()) {
                Ok(u) => u,
                Err(_) => continue,
            };

            // Check for pagination
            if let Some(off) = parsed
                .query_pairs()
                .find(|(k, _)| k == "offset")
                .and_then(|(_, v)| v.parse().ok())
                && off > offset
            {
                offset = off;
            }
            if let Some(pg) = parsed
                .query_pairs()
                .find(|(k, _)| k == "page")
                .and_then(|(_, v)| v.parse().ok())
                && pg > page
            {
                page = pg;
                use_page = true;
            }

            // Only follow eja.tv links that aren't filter URLs
            if !parsed.host_str().unwrap_or("").contains("eja.tv")
                || is_filter_url(full_url.as_str())
            {
                continue;
            }

            if items.contains_key(full_url.as_str()) {
                continue;
            }

            let inner_html = link.html();
            let mut name = clean_text(&inner_html);
            if !valid_name(&name, noise) {
                // Try to get from title/alt attributes
                for attr in ["title", "alt", "aria-label", "data-title", "data-name"] {
                    if let Some(val) = link.value().attr(attr) {
                        let cand = clean_text(val);
                        if valid_name(&cand, noise) {
                            name = cand;
                            break;
                        }
                    }
                }
            }

            if !valid_name(&name, noise) {
                continue;
            }

            // Extract logo from img inside link
            let mut logo = String::new();
            for img in link.select(&img_selector) {
                if let Some(src) = img.value().attr("src") {
                    logo = final_url
                        .join(src)
                        .unwrap_or_else(|_| final_url.clone())
                        .to_string();
                    break;
                }
            }

            let (language, country) =
                lang_classifier.classify(&name, "", task.category.as_deref().unwrap_or(""), "");

            items.insert(
                full_url.to_string(),
                Channel {
                    stream_url: full_url.to_string(),
                    name,
                    tvg_id: String::new(),
                    logo,
                    country,
                    language,
                    category: task.category.clone().unwrap_or_default(),
                    extra_headers: Default::default(),
                    source_name: "eja.tv".to_string(),
                    is_external: false,
                    is_fast: false,
                    explicit_telugu: false,
                    height: 0,
                    quality_tag: String::new(),
                    latency_ms: 0,
                    watch_url: full_url.to_string(),
                    is_master_m3u8: true,
                    audio_tracks: Vec::new(),
                },
            );
            added += 1;
        }

        if added == 0 && m3u8_urls.is_empty() {
            break;
        }

        _count += 1;

        // Determine next page/offset
        if offset > 0 && !use_page {
            // Continue with offset
        } else if page > 1 {
            use_page = true;
        } else if added < 18 {
            offset += 18;
        } else {
            offset += added;
        }
    }

    items.into_values().collect()
}

fn extract_m3u8_from_text(text: &str) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();

    let mut add = |u: &str| {
        let u = u.trim().replace("\\/", "/");
        if !u.is_empty() && seen.insert(u.clone()) {
            out.push(u);
        }
    };

    // Absolute m3u8 URLs
    let re_abs = regex::Regex::new(r#"(?i)https?://[^\s"'<>\\()]+?\.m3u8[^\s"'<>\\()]*"#).unwrap();
    for m in re_abs.find_iter(text) {
        add(m.as_str());
    }

    // Protocol-relative m3u8 URLs
    let re_proto = regex::Regex::new(
        r#"(?i)(?:^|[\s"'=(])(//[a-zA-Z0-9.-]+/[^\s"'<>\\()]+?\.m3u8[^\s"'<>\\()]*)"#,
    )
    .unwrap();
    for caps in re_proto.captures_iter(text) {
        if let Some(m) = caps.get(1) {
            add(&format!("https:{}", m.as_str()));
        }
    }

    // Base64 encoded (atob)
    let re_atob = regex::Regex::new(r#"atob\(\s*["']([A-Za-z0-9+/=]{16,})["']\s*\)"#).unwrap();
    for caps in re_atob.captures_iter(text) {
        if let Some(m) = caps.get(1)
            && let Ok(decoded) = base64::engine::general_purpose::STANDARD.decode(m.as_str())
        {
            let s = String::from_utf8_lossy(&decoded);
            if s.starts_with("http") && s.to_lowercase().contains(".m3u8") {
                add(s.trim());
            }
        }
    }

    out
}

fn is_filter_url(raw_url: &str) -> bool {
    let Ok(u) = Url::parse(raw_url) else {
        return true;
    };
    let path = u.path().trim_matches('/').to_lowercase();

    let static_slugs = [
        "about", "help", "privacy", "terms", "contact", "faq", "login", "register", "dmca",
    ];
    if static_slugs.contains(&path.as_str()) {
        return true;
    }

    let static_exts = [
        ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ];
    if static_exts.iter().any(|ext| path.ends_with(ext)) {
        return true;
    }

    if path.is_empty() && u.query().is_none() {
        return true;
    }

    if path.is_empty() {
        let query_keys: Vec<_> = u.query_pairs().map(|(k, _)| k.to_string()).collect();
        let filter_keys = [
            "country", "language", "category", "search", "offset", "limit", "page", "sort",
            "order", "level",
        ];
        if query_keys.iter().all(|k| filter_keys.contains(&k.as_str())) {
            return true;
        }
    }

    false
}

fn clean_text(text: &str) -> String {
    let re_html = regex::Regex::new(r"<[^>]*>").unwrap();
    let re_multi = regex::Regex::new(r"\s+").unwrap();
    let re_eja = regex::Regex::new(r"(?i)^eja\.tv\s*[-|:–—]+\s*|\s*[-|:–—]+\s*eja\.tv$").unwrap();

    let mut t = re_html.replace_all(text, " ").to_string();
    t = t.replace(&['<', '>', ','][..], " ");
    t = re_multi.replace_all(&t, " ").to_string();
    t = re_eja.replace_all(&t, "").to_string();
    t.trim()
        .trim_matches(|c: char| " -|:–—/".contains(c))
        .to_string()
}

fn valid_name(name: &str, noise: &HashSet<String>) -> bool {
    if name.is_empty() {
        return false;
    }
    let low = name.to_lowercase().trim().to_string();
    if low.len() < 2 || low.len() > 80 {
        return false;
    }
    if noise.contains(&low) {
        return false;
    }
    if low.contains(['<', '>', '{', '}', '\\']) || low.contains("//") {
        return false;
    }
    if low.starts_with("http:") || low.starts_with("https:") || low.starts_with("www.") {
        return false;
    }
    if low.chars().all(|c| c.is_ascii_digit()) {
        return false;
    }
    let re_hex = regex::Regex::new(r"^[a-f0-9]{12,}$").unwrap();
    let re_generic =
        regex::Regex::new(r"^(channel|stream|live|index|master|playlist|tracks)\s*\d*$").unwrap();
    !(re_hex.is_match(&low) || re_generic.is_match(&low))
}

fn humanize_url(url: &str) -> String {
    let Ok(parsed) = Url::parse(url) else {
        return String::new();
    };
    let parts: Vec<&str> = parsed.path().split('/').collect();

    let generic_parts = [
        "hls",
        "live",
        "stream",
        "streams",
        "index",
        "playlist",
        "master",
        "chunklist",
        "manifest",
        "tv",
        "iptv",
        "out",
        "v1",
        "v2",
        "v3",
        "cdn",
        "edge",
        "pub",
        "app",
        "smil",
        "ngrp",
        "amlst",
        "disk1",
        "liveevent",
        "bpk-tv",
        "tracks-v1a1",
    ];

    let re_hex = regex::Regex::new(r"^[a-f0-9]{12,}$").unwrap();

    for part in parts.iter().rev() {
        if part.is_empty() {
            continue;
        }
        let mut stem = *part;
        for ext in [".m3u8", ".m3u", ".mpd", ".ts", ".smil", ".sdp"] {
            if let Some(stripped) = stem.strip_suffix(ext) {
                stem = stripped;
                break;
            }
        }
        let low = stem.to_lowercase();
        if generic_parts.contains(&low.as_str()) || stem.len() < 2 || re_hex.is_match(&low) {
            continue;
        }
        let word = stem.replace(['_', '-'], " ").trim().to_string();
        if word.len() >= 2 {
            if word.len() <= 4 {
                return word.to_uppercase();
            }
            return title_case(&word);
        }
    }
    String::new()
}

fn title_case(s: &str) -> String {
    s.split_whitespace()
        .map(|w| {
            let mut chars = w.chars();
            match chars.next() {
                Some(first) => {
                    first.to_uppercase().collect::<String>() + &chars.as_str().to_lowercase()
                }
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}
