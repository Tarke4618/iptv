use anyhow::Result;
use reqwest::Client;
use std::collections::HashMap;
use tracing::{info, warn};

use crate::config::{Config, ExternalSource};
use crate::language_classifier::LanguageClassifier;
use crate::models::Channel;

const DEFAULT_UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

pub struct M3UParser {
    config: Config,
    client: Client,
}

impl M3UParser {
    pub fn new(config: Config) -> Self {
        let client = Client::builder()
            .user_agent(DEFAULT_UA)
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .expect("Failed to create HTTP client");

        Self { config, client }
    }

    pub async fn parse_source(
        &self,
        source: &ExternalSource,
        lang_classifier: &LanguageClassifier,
    ) -> Result<Vec<Channel>> {
        let resp = self.client.get(&source.url).send().await?;
        if !resp.status().is_success() {
            warn!(
                "Failed to fetch M3U source '{}': {}",
                source.name,
                resp.status()
            );
            return Ok(Vec::new());
        }

        let body = resp.text().await?;
        let mut channels = self.parse_m3u_text(&body, source, lang_classifier)?;

        // Apply language filtering if configured
        if self.config.only_matching_external_languages
            && !source.telugu_only
            && !source.always_keep
        {
            let allowed_langs: HashMap<String, bool> = self
                .config
                .languages
                .iter()
                .map(|l| (l.to_lowercase(), true))
                .collect();
            channels.retain(|ch| {
                let lang_blob =
                    format!("{} {} {}", ch.language, ch.category, ch.name).to_lowercase();
                allowed_langs.keys().any(|l| lang_blob.contains(l))
            });
        }

        info!("Parsed {} channels from {}", channels.len(), source.name);
        Ok(channels)
    }

    fn parse_m3u_text(
        &self,
        text: &str,
        source: &ExternalSource,
        lang_classifier: &LanguageClassifier,
    ) -> Result<Vec<Channel>> {
        let mut channels = Vec::new();
        let mut current: Option<Channel> = None;
        let mut current_headers = HashMap::new();

        let re_extinf_id = regex::Regex::new(r#"(?i)tvg-id="([^"]*)""#).unwrap();
        let re_extinf_logo = regex::Regex::new(r#"(?i)tvg-logo="([^"]*)""#).unwrap();
        let re_extinf_lang = regex::Regex::new(r#"(?i)tvg-language="([^"]*)""#).unwrap();
        let re_extinf_cntry = regex::Regex::new(r#"(?i)tvg-country="([^"]*)""#).unwrap();
        let re_extinf_grp = regex::Regex::new(r#"(?i)group-title="([^"]*)""#).unwrap();
        let re_extinf_ua = regex::Regex::new(r#"(?i)user-agent="([^"]*)""#).unwrap();
        let re_extinf_ref = regex::Regex::new(r#"(?i)referer="([^"]*)""#).unwrap();
        let re_bracket = regex::Regex::new(r"\[[^\]]*\]|\([^\)]*\)").unwrap();

        for line in text.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }

            if line.starts_with("#EXTINF:") {
                current_headers.clear();

                let tvg_id = re_extinf_id
                    .captures(line)
                    .and_then(|c| c.get(1))
                    .map(|m| m.as_str().trim().to_string())
                    .unwrap_or_default();
                let logo = re_extinf_logo
                    .captures(line)
                    .and_then(|c| c.get(1))
                    .map(|m| m.as_str().trim().to_string())
                    .unwrap_or_default();
                let language = re_extinf_lang
                    .captures(line)
                    .and_then(|c| c.get(1))
                    .map(|m| m.as_str().trim().to_string())
                    .unwrap_or_else(|| source.default_language.clone());
                let country = re_extinf_cntry
                    .captures(line)
                    .and_then(|c| c.get(1))
                    .map(|m| m.as_str().trim().to_string())
                    .unwrap_or_else(|| source.default_country.clone());
                let group = re_extinf_grp
                    .captures(line)
                    .and_then(|c| c.get(1))
                    .map(|m| m.as_str().trim().to_string())
                    .unwrap_or_default();

                if let Some(m) = re_extinf_ua.captures(line).and_then(|c| c.get(1)) {
                    current_headers.insert("User-Agent".to_string(), m.as_str().trim().to_string());
                }
                if let Some(m) = re_extinf_ref.captures(line).and_then(|c| c.get(1)) {
                    current_headers.insert("Referer".to_string(), m.as_str().trim().to_string());
                }

                // Extract title after comma
                let title = line.split(',').nth(1).unwrap_or("").trim();
                let clean_title = clean_text(&re_bracket.replace_all(title, ""));

                current = Some(Channel {
                    stream_url: String::new(),
                    name: clean_title,
                    tvg_id,
                    logo,
                    country,
                    language,
                    category: group,
                    extra_headers: current_headers.clone(),
                    source_name: source.name.clone(),
                    is_external: true,
                    is_fast: source.is_fast,
                    explicit_telugu: source.telugu_only,
                    height: 0,
                    quality_tag: String::new(),
                    latency_ms: 0,
                    watch_url: String::new(),
                    is_master_m3u8: true,
                    audio_tracks: Vec::new(),
                });
            } else if line.starts_with("#EXTVLCOPT:") && current.is_some() {
                let opt = line.strip_prefix("#EXTVLCOPT:").unwrap().trim();
                let low_opt = opt.to_lowercase();
                if let Some(val) = low_opt.strip_prefix("http-user-agent=") {
                    current_headers.insert("User-Agent".to_string(), val.trim().to_string());
                } else if let Some(val) = low_opt
                    .strip_prefix("http-referrer=")
                    .or_else(|| low_opt.strip_prefix("http-referer="))
                {
                    current_headers.insert("Referer".to_string(), val.trim().to_string());
                }
            } else if line.starts_with("#EXTHTTP:") && current.is_some() {
                let json_str = line.strip_prefix("#EXTHTTP:").unwrap().trim();
                if let Ok(map) = serde_json::from_str::<HashMap<String, String>>(json_str) {
                    for (k, v) in map {
                        match k.to_lowercase().as_str() {
                            "user-agent" => current_headers.insert("User-Agent".to_string(), v),
                            "referer" | "referrer" => {
                                current_headers.insert("Referer".to_string(), v)
                            }
                            _ => None,
                        };
                    }
                }
            } else if !line.starts_with('#') && current.is_some() {
                // This is a stream URL
                let (clean_url, pipe_headers) = split_pipe_headers(line);
                let mut merged_headers = current_headers.clone();
                for (k, v) in pipe_headers {
                    merged_headers.insert(k, v);
                }

                if is_usable_hls_url(&clean_url, self.config.skip_raw_ip_streams) {
                    let mut ch = current.take().unwrap();
                    ch.stream_url = clean_url;
                    ch.extra_headers = merged_headers;

                    // Apply language classification
                    let (detected_lang, detected_country) =
                        lang_classifier.classify(&ch.name, &ch.language, &ch.category, &ch.country);
                    if ch.language.is_empty() {
                        ch.language = detected_lang;
                    }
                    if ch.country.is_empty() {
                        ch.country = detected_country;
                    }

                    // Validate name
                    if !valid_name(&ch.name) {
                        ch.name = humanize_url(&ch.stream_url);
                    }

                    if valid_name(&ch.name) {
                        channels.push(ch);
                    }
                }

                current = None;
                current_headers.clear();
            }
        }

        Ok(channels)
    }
}

fn split_pipe_headers(raw: &str) -> (String, HashMap<String, String>) {
    let mut headers = HashMap::new();
    if let Some(idx) = raw.find('|') {
        let base = raw[..idx].trim().to_string();
        let pipe_part = &raw[idx + 1..];
        for pair in pipe_part.split(['&', '|']) {
            if let Some(eq_idx) = pair.find('=') {
                let key = pair[..eq_idx].trim().to_lowercase();
                let mut val = pair[eq_idx + 1..].trim().to_string();
                // URL decode
                val = urlencoding::decode(&val.clone())
                    .unwrap_or_else(|_| val.into())
                    .into_owned();
                val = val.trim_matches(|c| c == '"' || c == '\'').to_string();
                if val.is_empty() {
                    continue;
                }
                match key.as_str() {
                    "user-agent" | "http-user-agent" => {
                        headers.insert("User-Agent".to_string(), val)
                    }
                    "referer" | "referrer" | "http-referrer" | "http-referer" => {
                        headers.insert("Referer".to_string(), val)
                    }
                    "origin" => headers.insert("Origin".to_string(), val),
                    "cookie" => headers.insert("Cookie".to_string(), val),
                    _ => None,
                };
            }
        }
        (base, headers)
    } else {
        (raw.trim().to_string(), headers)
    }
}

fn is_usable_hls_url(url: &str, skip_raw_ip: bool) -> bool {
    let low = url.to_lowercase();
    if !low.starts_with("http://") && !low.starts_with("https://") {
        return false;
    }
    let path_part = low.split('?').next().unwrap_or("");
    if path_part.contains(".mpd") {
        return false; // Widevine DRM
    }
    for bad in [
        "youtube.com/",
        "youtu.be/",
        "twitch.tv/",
        "dailymotion.com/",
    ] {
        if low.contains(bad) {
            return false;
        }
    }
    if skip_raw_ip {
        let re_ip = regex::Regex::new(r"(?i)^https?://\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?/").unwrap();
        if re_ip.is_match(url) {
            return false;
        }
    }
    true
}

fn valid_name(name: &str) -> bool {
    if name.is_empty() {
        return false;
    }
    let lower = name.to_lowercase();
    let low = lower.trim();
    if low.len() < 2 || low.len() > 80 {
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
    !re_hex.is_match(low) && !re_generic.is_match(low)
}

fn humanize_url(url: &str) -> String {
    let Ok(parsed) = url::Url::parse(url) else {
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

fn clean_text(text: &str) -> String {
    let re_html = regex::Regex::new(r"<[^>]*>").unwrap();
    let re_multi = regex::Regex::new(r"\s+").unwrap();

    let mut t = re_html.replace_all(text, " ").to_string();
    t = re_multi.replace_all(&t, " ").to_string();
    t.trim().to_string()
}
