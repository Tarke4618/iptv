use anyhow::Result;
use reqwest::Client;
use std::collections::HashMap;
use tracing::info;

use crate::models::{CatalogEntry, RawIPTVChannel, RawIPTVLogo};

#[derive(Debug, Clone)]
pub struct IPTVOrgCatalog {
    pub channels: HashMap<String, CatalogEntry>,
    pub logos: HashMap<String, String>,
}

impl IPTVOrgCatalog {
    pub async fn fetch() -> Result<Self> {
        let client = Client::builder()
            .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            .timeout(std::time::Duration::from_secs(30))
            .build()?;

        // Fetch channels and logos in parallel
        let (channels_resp, logos_resp) = tokio::join!(
            client
                .get("https://iptv-org.github.io/api/channels.json")
                .send(),
            client
                .get("https://iptv-org.github.io/api/logos.json")
                .send()
        );

        let channels_bytes = channels_resp?.bytes().await?;
        let logos_bytes = logos_resp?.bytes().await?;

        let mut logos_map: HashMap<String, String> = HashMap::new();
        if let Ok(logos) = serde_json::from_slice::<Vec<RawIPTVLogo>>(&logos_bytes) {
            for logo in logos {
                if logo.channel.is_empty() || logo.url.is_empty() {
                    continue;
                }
                let fmt = logo.format.to_uppercase();
                let existing = logos_map.get(&logo.channel);
                let should_replace = match (existing, fmt.as_str()) {
                    (Some(ex), "PNG" | "WEBP" | "JPEG") if ex.ends_with(".svg") => true,
                    (None, _) => true,
                    _ => false,
                };
                if should_replace {
                    logos_map.insert(logo.channel, logo.url);
                }
            }
        }

        let mut channels_map: HashMap<String, CatalogEntry> = HashMap::new();
        if let Ok(channels) = serde_json::from_slice::<Vec<RawIPTVChannel>>(&channels_bytes) {
            for ch in channels {
                if ch.id.is_empty() || ch.name.is_empty() || ch.closed.is_some() {
                    continue;
                }
                let country_code = ch.country.to_uppercase();
                let category = ch
                    .categories
                    .first()
                    .map(|c| title_case(c))
                    .unwrap_or_default();
                let logo = logos_map.get(&ch.id).cloned().unwrap_or_default();

                let entry = CatalogEntry {
                    id: ch.id.clone(),
                    name: ch.name.clone(),
                    logo,
                    country_code: country_code.clone(),
                    category,
                };

                // Index by normalized name and alt_names
                let names = std::iter::once(ch.name).chain(ch.alt_names);
                for name in names {
                    let key = crate::models::normalize_channel_key(&name);
                    if key.is_empty() {
                        continue;
                    }
                    // Prefer Indian entries for Indian channels
                    let should_insert = match channels_map.get(&key) {
                        Some(existing) => country_code == "IN" && existing.country_code != "IN",
                        None => true,
                    };
                    if should_insert {
                        channels_map.insert(key, entry.clone());
                    }
                }
            }
        }

        info!(
            "IPTV-org catalog: {} channels indexed, {} logos",
            channels_map.len(),
            logos_map.len()
        );

        Ok(Self {
            channels: channels_map,
            logos: logos_map,
        })
    }

    pub fn enrich_channel(&self, channel: &mut crate::models::Channel) {
        let key = crate::models::normalize_channel_key(&channel.name);
        if let Some(entry) = self.channels.get(&key) {
            if channel.tvg_id.is_empty() {
                channel.tvg_id = entry.id.clone();
            }
            if channel.logo.is_empty() && !entry.logo.is_empty() {
                channel.logo = entry.logo.clone();
            }
            if channel.category.is_empty() && !entry.category.is_empty() {
                channel.category = entry.category.clone();
            }
            if channel.country.is_empty() && entry.country_code != "XX" {
                channel.country = entry.country_code.clone();
            }
        }
    }
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
