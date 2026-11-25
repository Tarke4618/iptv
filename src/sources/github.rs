//! GitHub-sourced M3U Discovery

use anyhow::{Result, anyhow};
use async_trait::async_trait;
use regex::Regex;
use tracing::{debug, info};

use super::source::{ChannelSource, SourceCategory, SourceMeta};
use crate::models::Channel;

/// GitHub-sourced IPTV list
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct GithubSource {
    meta: SourceMeta,
    client: reqwest::Client,
}

impl GithubSource {
    pub fn new(owner: &str, repo: &str, path: &str, description: &str) -> Self {
        let id = format!("github-{}-{}", owner, repo);
        let url = format!(
            "https://raw.githubusercontent.com/{}/{}/{}",
            owner, repo, path
        );

        let meta = SourceMeta {
            id: id.clone(),
            name: format!("{} - {}", owner, repo),
            description: description.to_string(),
            url,
            category: SourceCategory::Community,
            region: None,
            language: None,
            is_auth_required: false,
            update_interval_hours: 24,
            last_checked: None,
            channel_count: None,
            rating: None,
            tags: vec!["community".to_string(), "github".to_string()],
        };

        Self {
            meta,
            client: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(30))
                .build()
                .unwrap(),
        }
    }

    /// Discover sources from popular GitHub repos
    pub fn discover_all() -> Vec<Self> {
        let repos = [
            ("iptv-org", "iptv", "iptv", "Official IPTV-org collection"),
            ("TVP-iptv", "Streams", "README.md", "TVP-iptv streams"),
            ("free-IPTV", "free-tv", "README.md", "Free TV lists"),
            ("zeroclutch", "iptv", "README.md", "Various IPTV lists"),
            ("iptv-org", "epg", "epg.xml.gz", "EPG data"),
        ];

        repos
            .into_iter()
            .map(|(owner, repo, path, desc)| Self::new(owner, repo, path, desc))
            .collect()
    }

    /// Get the raw M3U URL
    pub fn raw_url(&self) -> &str {
        &self.meta.url
    }

    /// Check if this is an M3U playlist
    fn is_m3u(content: &str) -> bool {
        content.contains("#EXTM3U") || content.contains("#EXTINF")
    }

    /// Check if this is a GZ compressed EPG file
    #[allow(dead_code)]
    fn is_gz_epg(content: &[u8]) -> bool {
        // GZ files start with 1f 8b
        content.len() > 2 && content[0] == 0x1f && content[1] == 0x8b
    }
}

#[async_trait]
impl ChannelSource for GithubSource {
    fn id(&self) -> &str {
        &self.meta.id
    }

    fn name(&self) -> &str {
        &self.meta.name
    }

    async fn fetch_channels(&self) -> Result<Vec<Channel>> {
        // Fetch the raw content
        let resp = self.client.get(&self.meta.url).send().await?;

        if !resp.status().is_success() {
            return Err(anyhow!(
                "GitHub {} returned HTTP {}",
                self.meta.url,
                resp.status()
            ));
        }

        let content = resp.text().await?;

        if !Self::is_m3u(&content) {
            debug!("Skipping non-M3U file: {}", self.meta.url);
            return Ok(Vec::new());
        }

        // Parse M3U content
        let channels = self.parse_m3u(&content)?;

        info!("Fetched {} channels from {}", channels.len(), self.meta.url);

        Ok(channels)
    }

    fn metadata(&self) -> SourceMeta {
        self.meta.clone()
    }

    async fn is_alive(&self) -> bool {
        match self.client.get(&self.meta.url).send().await {
            Ok(resp) => resp.status().is_success(),
            Err(_) => false,
        }
    }
}

impl GithubSource {
    /// Parse M3U playlist content
    fn parse_m3u(&self, content: &str) -> Result<Vec<Channel>> {
        let mut channels = Vec::new();

        // Match #EXTINF lines and capture attributes
        let re = Regex::new(r"#EXTINF:([^,]+),([^#\n]+)([^#\r\n]*)#\s*(.+)$").unwrap();
        // Pre-compile attribute regex outside the loop
        let attr_re = Regex::new(r#"tvg-logo="([^"]+)"|group-title="([^"]+)""#).unwrap();

        for line in content.lines() {
            let line = line.trim();
            if line.starts_with("#EXTINF:") {
                if let Some(caps) = re.captures(line) {
                    let _duration: f64 = caps
                        .get(1)
                        .and_then(|m| m.as_str().parse().ok())
                        .unwrap_or(0.0);
                    let name = caps
                        .get(2)
                        .map(|m| m.as_str().trim())
                        .unwrap_or("Unknown")
                        .to_string();

                    // Look for tvg-logo and group in the rest of the attributes
                    let mut logo_url = String::new();
                    let mut group = "Unknown".to_string();

                    for attr_match in attr_re.captures_iter(line) {
                        if let Some(logo) = attr_match.get(1) {
                            logo_url = logo.as_str().to_string();
                        }
                        if let Some(grp) = attr_match.get(2) {
                            group = grp.as_str().to_string();
                        }
                    }

                    // Next line should be the URL
                    channels.push(Channel {
                        stream_url: String::new(), // Will be filled from next URL line
                        name,
                        tvg_id: String::new(),
                        logo: logo_url.clone(),
                        country: self.meta.region.clone().unwrap_or_default(),
                        language: "Unknown".to_string(),
                        category: group.clone(),
                        extra_headers: Default::default(),
                        source_name: self.meta.id.clone(),
                        is_external: true,
                        is_fast: true,
                        explicit_telugu: false,
                        height: 0,
                        quality_tag: String::new(),
                        latency_ms: 0,
                        watch_url: String::new(),
                        is_master_m3u8: false,
                        audio_tracks: Vec::new(),
                        logo_url: Some(logo_url),
                        group: Some(group),
                        is_drm: None,
                        channel_number: None,
                        epg_id: None,
                        categories: None,
                        quality: None,
                        last_updated: None,
                        verified_at: None,
                        verification_error: None,
                    });
                }
            } else if !line.starts_with('#') && !line.is_empty() {
                // This is a URL - associate it with the last channel
                if let Some(last) = channels.last_mut() {
                    last.stream_url = line.to_string();
                }
            }
        }

        Ok(channels)
    }
}

/// Source for dynamic M3U discovery via HTTP
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct HttpSource {
    meta: SourceMeta,
    client: reqwest::Client,
}

impl HttpSource {
    pub fn new(url: &str, name: &str, desc: &str) -> Self {
        let meta = SourceMeta {
            id: name.to_string().replace([' ', '-'], "").to_lowercase(),
            name: name.to_string(),
            description: desc.to_string(),
            url: url.to_string(),
            category: SourceCategory::Public,
            region: None,
            language: None,
            is_auth_required: false,
            update_interval_hours: 6,
            last_checked: None,
            channel_count: None,
            rating: None,
            tags: vec!["public".to_string()],
        };

        Self {
            meta,
            client: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(30))
                .build()
                .unwrap(),
        }
    }
}

#[async_trait]
impl ChannelSource for HttpSource {
    fn id(&self) -> &str {
        &self.meta.id
    }

    fn name(&self) -> &str {
        &self.meta.name
    }

    async fn fetch_channels(&self) -> Result<Vec<Channel>> {
        let resp = self.client.get(&self.meta.url).send().await?;

        if !resp.status().is_success() {
            return Err(anyhow!("HTTP {} returned {}", self.meta.url, resp.status()));
        }

        let content = resp.text().await?;

        // Parse based on content type
        if content.contains("#EXTM3U") || content.contains("#EXTINF") {
            self.parse_m3u(&content)
        } else {
            Err(anyhow!("Unknown content type"))
        }
    }

    fn metadata(&self) -> SourceMeta {
        self.meta.clone()
    }

    async fn is_alive(&self) -> bool {
        self.client.head(&self.meta.url).send().await.is_ok()
    }
}

impl HttpSource {
    fn parse_m3u(&self, content: &str) -> Result<Vec<Channel>> {
        // Simplified M3U parsing
        let lines: Vec<&str> = content.lines().collect();
        let mut channels: Vec<Channel> = Vec::new();

        for line in lines {
            if line.starts_with("#EXTINF:") {
                // Extract channel info
                let parts: Vec<&str> = line.split(',').collect();
                if parts.len() >= 2 {
                    let name = parts[1].trim();
                    channels.push(Channel {
                        name: name.to_string(),
                        stream_url: String::new(),
                        tvg_id: String::new(),
                        logo: String::new(),
                        country: self.meta.region.clone().unwrap_or_default(),
                        language: "Unknown".to_string(),
                        category: "Unknown".to_string(),
                        extra_headers: Default::default(),
                        source_name: self.meta.id.clone(),
                        is_external: true,
                        is_fast: true,
                        explicit_telugu: false,
                        height: 0,
                        quality_tag: String::new(),
                        latency_ms: 0,
                        watch_url: String::new(),
                        is_master_m3u8: false,
                        audio_tracks: Vec::new(),
                        logo_url: None,
                        group: None,
                        is_drm: None,
                        channel_number: None,
                        epg_id: None,
                        categories: None,
                        quality: None,
                        last_updated: None,
                        verified_at: None,
                        verification_error: None,
                    });
                }
            } else if !line.starts_with('#') && !line.is_empty() {
                // URL line
                let url = line.trim();
                if url.starts_with("http") {
                    // Associate with last channel
                    if let Some(ch) = channels.last_mut() {
                        ch.stream_url = url.to_string();
                    }
                }
            }
        }

        Ok(channels)
    }
}
