use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Channel {
    pub stream_url: String,
    pub name: String,
    pub tvg_id: String,
    pub logo: String,
    pub country: String,
    pub language: String,
    pub category: String,
    pub extra_headers: HashMap<String, String>,
    pub source_name: String,
    pub is_external: bool,
    pub is_fast: bool,
    pub explicit_telugu: bool,
    pub height: u32,
    pub quality_tag: String,
    pub latency_ms: u64,
    pub watch_url: String,
    pub is_master_m3u8: bool,
    pub audio_tracks: Vec<AudioTrack>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AudioTrack {
    pub name: String,
    pub language: String,
    pub is_default: bool,
    pub uri: String,
}

impl Channel {
    pub fn new(stream_url: String, name: String, source_name: String) -> Self {
        Self {
            stream_url,
            name,
            tvg_id: String::new(),
            logo: String::new(),
            country: String::new(),
            language: String::new(),
            category: String::new(),
            extra_headers: HashMap::new(),
            source_name,
            is_external: false,
            is_fast: false,
            explicit_telugu: false,
            height: 0,
            quality_tag: String::new(),
            latency_ms: 0,
            watch_url: String::new(),
            is_master_m3u8: true,
            audio_tracks: Vec::new(),
        }
    }

    pub fn merge(&mut self, other: &Channel) {
        if self.name.is_empty() && !other.name.is_empty() {
            self.name = other.name.clone();
        }
        if self.tvg_id.is_empty() && !other.tvg_id.is_empty() {
            self.tvg_id = other.tvg_id.clone();
        }
        if self.logo.is_empty() && !other.logo.is_empty() {
            self.logo = other.logo.clone();
        }
        if self.country.is_empty() && !other.country.is_empty() {
            self.country = other.country.clone();
        }
        if self.language.is_empty() && !other.language.is_empty() {
            self.language = other.language.clone();
        }
        if self.category.is_empty() && !other.category.is_empty() {
            self.category = other.category.clone();
        }
        if self.extra_headers.is_empty() && !other.extra_headers.is_empty() {
            self.extra_headers = other.extra_headers.clone();
        }
        if other.explicit_telugu {
            self.explicit_telugu = true;
        }
        if other.is_fast {
            self.is_fast = true;
        }
        if other.height > self.height {
            self.height = other.height;
        }
        if !other.audio_tracks.is_empty() {
            self.audio_tracks = other.audio_tracks.clone();
        }
    }

    pub fn dedup_key(&self) -> String {
        normalize_channel_key(&self.name)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CatalogEntry {
    pub id: String,
    pub name: String,
    pub logo: String,
    pub country_code: String,
    pub category: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceStat {
    pub source: String,
    pub alive: usize,
    pub imported: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RawIPTVChannel {
    pub id: String,
    pub name: String,
    pub alt_names: Vec<String>,
    pub country: String,
    pub categories: Vec<String>,
    pub closed: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RawIPTVLogo {
    pub channel: String,
    pub url: String,
    pub format: String,
}

pub fn normalize_channel_key(name: &str) -> String {
    let mut key = name.to_lowercase();

    // Remove bracket content
    let re_bracket = regex::Regex::new(r"\[[^\]]*\]|\([^\)]*\)").unwrap();
    key = re_bracket.replace_all(&key, "").to_string();

    // Remove quality/resolution words but PRESERVE distinguishing tokens
    // DO NOT strip: News, Movies, Cinema, Music, Kids, Sports, Gold, Plus, Bhakti, language names
    let re_strip = regex::Regex::new(r"\b(hd|fhd|uhd|sd|4k|tv|live|channel|india)\b").unwrap();
    key = re_strip.replace_all(&key, "").to_string();

    // Remove non-alphanumeric (keep unicode for Indian languages)
    let re_norm = regex::Regex::new(r"[^a-z0-9\u{0900}-\u{0D7F}]+").unwrap();
    key = re_norm.replace_all(&key, "").to_string();

    if key.is_empty() {
        // Fallback: just alphanumeric + unicode
        let re_fallback = regex::Regex::new(r"[^a-z0-9\u{0900}-\u{0D7F}]+").unwrap();
        key = re_fallback
            .replace_all(&name.to_lowercase(), "")
            .to_string();
    }

    key
}

pub fn name_quality_score(name: &str) -> i32 {
    let mut score = 0;
    let lower = name.to_lowercase();

    // Prefer names with distinguishing tokens (News, Movies, HD, etc.)
    for token in [
        "news",
        "movies",
        "cinema",
        "music",
        "kids",
        "sports",
        "gold",
        "plus",
        "bhakti",
        "hd",
        "fhd",
        "4k",
        "uhd",
        "max",
        "premium",
        "select",
        "action",
        "comedy",
        "drama",
        "life",
        "world",
        "entertainment",
        "india",
        "bharat",
    ] {
        if lower.contains(token) {
            score += 10;
        }
    }

    // Penalize generic names
    if lower.contains("channel") || lower.contains("tv live") {
        score -= 5;
    }

    // Prefer shorter, cleaner names
    score -= name.len() as i32 / 2;

    score
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_preserves_distinguishing_tokens() {
        // These should NOT collide
        assert_ne!(
            normalize_channel_key("Zee Telugu"),
            normalize_channel_key("Zee Telugu News")
        );
        assert_ne!(
            normalize_channel_key("Star Sports"),
            normalize_channel_key("Star Sports Hindi")
        );
        assert_ne!(
            normalize_channel_key("Sony Max"),
            normalize_channel_key("Sony Max 2")
        );
        assert_ne!(
            normalize_channel_key("ETV Telugu"),
            normalize_channel_key("ETV Andhra Pradesh")
        );
    }

    #[test]
    fn test_normalize_strips_noise() {
        assert_eq!(
            normalize_channel_key("Zee Telugu HD"),
            normalize_channel_key("Zee Telugu")
        );
        assert_eq!(
            normalize_channel_key("Star Sports [Live]"),
            normalize_channel_key("Star Sports")
        );
    }
}
