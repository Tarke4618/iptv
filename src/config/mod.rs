use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::Path;
use tokio::fs;

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Config {
    pub max_pages_per_query: usize,
    pub max_verify_seconds: u64,
    pub max_workers: usize,
    pub verify_streams: bool,
    pub deduplicate_channels: bool,
    pub tag_stream_info: bool,
    pub skip_raw_ip_streams: bool,
    pub only_matching_external_languages: bool,
    pub epg_urls: Vec<String>,
    pub external_m3u_sources: Vec<ExternalSource>,
    pub countries: Vec<String>,
    pub languages: Vec<String>,
    pub categories: Vec<String>,
    pub telugu_hub_searches: Vec<String>,
    pub custom_searches: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ExternalSource {
    pub name: String,
    pub url: String,
    #[serde(default)]
    pub telugu_only: bool,
    #[serde(default)]
    pub always_keep: bool,
    #[serde(default)]
    pub is_fast: bool,
    #[serde(default)]
    pub default_country: String,
    #[serde(default)]
    pub default_language: String,
}

impl Config {
    pub async fn load(path: &Path) -> Result<Self> {
        let content = fs::read_to_string(path)
            .await
            .with_context(|| format!("Failed to read config file: {}", path.display()))?;
        let config: Config =
            serde_json::from_str(&content).with_context(|| "Failed to parse config.json")?;
        Ok(config.with_defaults())
    }

    fn with_defaults(mut self) -> Self {
        if self.max_pages_per_query == 0 || self.max_pages_per_query > 15 {
            self.max_pages_per_query = 12;
        }
        if self.max_verify_seconds == 0 {
            self.max_verify_seconds = 300;
        }
        if self.max_workers == 0 {
            self.max_workers = 450;
        }
        self
    }
}
