//! IPTV Aggregator Library
//!
//! This library provides the core functionality for the IPTV Aggregator,
//! including M3U parsing, channel discovery, and stream verification.

// Public modules for library consumers
pub mod config;
pub mod deduplicator;
pub mod eja_tv;
pub mod geo_blocked;
pub mod health_cache;
pub mod iptv_org;
pub mod jio_service;
pub mod language_classifier;
pub mod m3u_parser;
pub mod models;
pub mod playlist_writer;
pub mod sources;
pub mod stream_verifier;

// Re-export commonly used types
pub use config::Config;
pub use deduplicator::Deduplicator;
pub use eja_tv::EjaTvCrawler;
pub use geo_blocked::GeoBlockedManager;
pub use health_cache::HealthCache;
pub use iptv_org::IPTVOrgCatalog;
pub use language_classifier::LanguageClassifier;
pub use m3u_parser::M3UParser;
pub use models::{Channel, AudioTrack, SourceStat};
pub use playlist_writer::PlaylistWriter;
pub use sources::{GithubSource, HttpSource, SourceRegistry, SourceCategory, SourceQuery, ChannelSource};
pub use stream_verifier::StreamVerifier;

use anyhow::Result;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;
use tracing::info;

/// Build a playlist from all configured sources (library entry point for Tauri app)
pub async fn build_playlist(
    config: Config,
    health_cache: Option<HealthCache>,
    verify_streams: bool,
    max_workers: usize,
    max_verify_seconds: Duration,
) -> Result<PlaylistBuildResult> {
    let start_time = std::time::Instant::now();
    info!("Building playlist via library API...");

    // Initialize health cache
    let health_cache = match health_cache {
        Some(c) => Arc::new(c),
        None => Arc::new(HealthCache::new("cache/health_cache.json").await?),
    };
    info!("Health cache loaded: {} entries", health_cache.len());

    // Load IPTV-org catalog for EPG/logo enrichment
    info!("Loading IPTV-org catalog...");
    let catalog = match IPTVOrgCatalog::fetch().await {
        Ok(c) => {
            info!("Catalog loaded: {} channels, {} logos", c.channels.len(), c.logos.len());
            c
        }
        Err(e) => {
            tracing::warn!("Failed to load IPTV-org catalog: {}", e);
            IPTVOrgCatalog { 
                channels: HashMap::new(), 
                logos: HashMap::new() 
            }
        }
    };

    // Initialize language classifier
    let lang_classifier = LanguageClassifier::new(&catalog);

    // Initialize geo-blocked manager
    let geo_manager = Arc::new(GeoBlockedManager::new("playlists/geoblocked").await?);

    // Initialize crawlers
    let eja_crawler = EjaTvCrawler::new(config.clone(), Arc::new(lang_classifier.clone()));
    let m3u_parser = M3UParser::new(config.clone());
    let verifier = StreamVerifier::new(max_workers, max_verify_seconds.as_secs()).await?;
    let deduplicator = Deduplicator::new(config.deduplicate_channels);
    let playlist_writer = PlaylistWriter::new(config.clone());

    // Step 1: Crawl EJA.tv
    info!("Crawling EJA.tv...");
    let eja_channels = eja_crawler.crawl_all().await?;
    info!("EJA.tv: {} streams", eja_channels.len());

    // Step 2: Parse external M3U sources
    let mut external_channels = Vec::new();
    let mut source_stats = HashMap::new();

    for source in &config.external_m3u_sources {
        let channels = m3u_parser.parse_source(source, &lang_classifier).await?;
        let count = channels.len();
        source_stats.insert(
            source.name.clone(),
            SourceStat {
                source: source.name.clone(),
                alive: 0,
                imported: count,
            },
        );
        info!("  [{}] -> {} candidates", source.name, count);
        external_channels.extend(channels);
    }

    // Step 3: Merge channels
    info!("Merging channels...");
    let mut all_channels: HashMap<String, Channel> = HashMap::new();

    for ch in eja_channels {
        all_channels.insert(ch.stream_url.clone(), ch);
    }

    for ch in external_channels {
        all_channels
            .entry(ch.stream_url.clone())
            .and_modify(|existing| existing.merge(&ch))
            .or_insert(ch);
    }

    // Filter by health cache and geo-blocked
    let mut filtered_channels = Vec::new();
    let mut skipped_cached = 0;
    for ch in all_channels.values() {
        if health_cache.should_skip(&ch.stream_url) && !geo_manager.is_geo_blocked(&ch.stream_url) {
            skipped_cached += 1;
            continue;
        }
        filtered_channels.push(ch.clone());
    }
    info!("Filtered {} channels ({} skipped from health cache)", filtered_channels.len(), skipped_cached);

    // Step 4: Verify streams if requested
    let verified_channels = if verify_streams {
        info!("Verifying {} streams...", filtered_channels.len());
        verifier
            .verify_all(filtered_channels, health_cache.clone(), geo_manager.clone())
            .await?
    } else {
        filtered_channels
    };
    info!("Verification complete: {} live channels", verified_channels.len());

    // Update stats
    for ch in &verified_channels {
        if let Some(stat) = source_stats.get_mut(&ch.source_name) {
            stat.alive += 1;
        }
    }

    // Save health cache
    health_cache.save().await?;

    // Step 5: Deduplicate
    info!("Deduplicating channels...");
    let deduplicated = deduplicator.deduplicate(verified_channels);

    // Step 6: Write playlists
    info!("Writing playlists...");
    playlist_writer
        .write_all(
            &deduplicated,
            &source_stats,
            &lang_classifier,
            &geo_manager,
            &config,
        )
        .await?;

    let elapsed = start_time.elapsed();
    info!("Build completed in {:.2}s", elapsed.as_secs_f64());

    Ok(PlaylistBuildResult {
        channels: deduplicated,
        source_stats,
        elapsed,
    })
}

/// Result of building a playlist
pub struct PlaylistBuildResult {
    /// Unique live channels
    pub channels: Vec<Channel>,
    /// Source statistics
    pub source_stats: HashMap<String, SourceStat>,
    /// Build duration
    pub elapsed: Duration,
}