//! Dynamic Source Discovery Module

use anyhow::Result;
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use tracing::{debug, info};

use crate::models::Channel;

/// Source metadata for discovery
#[derive(Debug, Clone, Serialize, Deserialize)]
#[allow(dead_code)]
pub struct SourceMeta {
    pub id: String,
    pub name: String,
    pub description: String,
    pub url: String,
    pub category: SourceCategory,
    pub region: Option<String>,
    pub language: Option<String>,
    pub is_auth_required: bool,
    pub update_interval_hours: u8,
    pub last_checked: Option<chrono::DateTime<chrono::Utc>>,
    pub channel_count: Option<usize>,
    pub rating: Option<f32>,
    pub tags: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
pub enum SourceCategory {
    #[default]
    Public,
    Community,
    GeoBlocked,
    Authentication,
}

impl SourceCategory {
    pub fn as_str(&self) -> &'static str {
        match self {
            SourceCategory::Public => "public",
            SourceCategory::Community => "community",
            SourceCategory::GeoBlocked => "geo-blocked",
            SourceCategory::Authentication => "authentication",
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct SourceQuery {
    pub region: Option<String>,
    pub language: Option<String>,
    pub categories: Vec<SourceCategory>,
    pub search_terms: Vec<String>,
    pub min_channels: Option<usize>,
    pub exclude_tags: Vec<String>,
}

impl SourceQuery {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_region(region: impl Into<String>) -> Self {
        Self {
            region: Some(region.into()),
            ..Default::default()
        }
    }

    pub fn with_language(language: impl Into<String>) -> Self {
        Self {
            language: Some(language.into()),
            ..Default::default()
        }
    }

    pub fn with_categories(mut self, categories: Vec<String>) -> Self {
        self.categories = categories
            .into_iter()
            .filter_map(|c| match c.to_lowercase().as_str() {
                "public" => Some(SourceCategory::Public),
                "community" => Some(SourceCategory::Community),
                "geo-blocked" => Some(SourceCategory::GeoBlocked),
                "authentication" => Some(SourceCategory::Authentication),
                _ => None,
            })
            .collect();
        self
    }

    pub fn only_available(mut self) -> Self {
        self.min_channels = Some(1);
        self
    }

    pub fn with_search(mut self, terms: Vec<String>) -> Self {
        self.search_terms = terms;
        self
    }

    pub fn excludes_tags(mut self, tags: Vec<String>) -> Self {
        self.exclude_tags = tags;
        self
    }
}

/// Trait for channel sources
#[async_trait]
pub trait ChannelSource: Send + Sync {
    fn id(&self) -> &str;
    fn name(&self) -> &str;
    async fn fetch_channels(&self) -> Result<Vec<Channel>>;
    fn metadata(&self) -> SourceMeta;
    async fn is_alive(&self) -> bool;
}

/// Registry for managing multiple sources
pub struct SourceRegistry {
    sources: Arc<Mutex<HashMap<String, Box<dyn ChannelSource>>>>,
    source_metadata: Arc<Mutex<Vec<SourceMeta>>>,
}

impl SourceRegistry {
    pub fn new() -> Self {
        Self {
            sources: Arc::new(Mutex::new(HashMap::new())),
            source_metadata: Arc::new(Mutex::new(Vec::new())),
        }
    }

    pub fn register(&self, source: Box<dyn ChannelSource>) {
        let id = source.id().to_string();
        let meta = source.metadata();
        info!("Registering source: {}", id);
        self.sources.lock().unwrap().insert(id, source);
        self.source_metadata.lock().unwrap().push(meta);
    }

    pub fn unregister(&self, id: &str) {
        self.sources.lock().unwrap().remove(id);
    }

    pub fn get_by_category(&self, category: SourceCategory) -> Vec<SourceMeta> {
        self.source_metadata
            .lock()
            .unwrap()
            .iter()
            .filter(|m| m.category == category)
            .cloned()
            .collect()
    }

    pub fn query(&self, filters: SourceQuery) -> Vec<SourceMeta> {
        let metadata = self.source_metadata.lock().unwrap();
        metadata
            .iter()
            .filter(|m| {
                // Check region
                if let Some(ref region) = filters.region
                    && m.region.as_ref().map(|r| r != region).unwrap_or(true) {
                    return false;
                }
                // Check language
                if let Some(ref language) = filters.language
                    && m.language.as_ref().map(|l| l != language).unwrap_or(true) {
                    return false;
                }
                // Check categories
                if !filters.categories.is_empty()
                    && !filters.categories.contains(&m.category) {
                    return false;
                }
                // Check min channels
                if let Some(min) = filters.min_channels
                    && m.channel_count.unwrap_or(0) < min {
                    return false;
                }
                // Check exclude tags - exclude if any matching tag is found
                !(filters.exclude_tags.is_empty() 
                    || !filters.exclude_tags.iter().any(|t| m.tags.contains(t)))
            })
            .cloned()
            .collect()
    }

    #[allow(clippy::await_holding_lock)]
    pub async fn fetch_all_channels(&self, _filters: Option<SourceQuery>) -> Result<Vec<Channel>> {
        let mut all_channels = Vec::new();
        
        let sources = self.sources.lock().unwrap();
        for (id, source) in sources.iter() {
            debug!("Fetching from source: {}", id);
            match source.fetch_channels().await {
                Ok(channels) => {
                    info!("  [{}] -> {} channels", id, channels.len());
                    all_channels.extend(channels);
                }
                Err(e) => {
                    tracing::warn!("Failed to fetch from {}: {}", id, e);
                }
            }
        }

        Ok(all_channels)
    }

    /// Refresh health of sources
    #[allow(clippy::await_holding_lock)]
    pub async fn refresh_health(&self) -> Result<()> {
        let sources = self.sources.lock().unwrap();
        
        for (id, source) in sources.iter() {
            let alive = source.is_alive().await;
            debug!("Source {} is {}", id, if alive { "alive" } else { "dead" });
        }

        Ok(())
    }

    /// Get metadata for all sources
    pub fn get_metadata(&self) -> Vec<SourceMeta> {
        self.source_metadata.lock().unwrap().clone()
    }
}

impl Default for SourceRegistry {
    fn default() -> Self {
        Self::new()
    }
}