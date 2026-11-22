mod config;
mod deduplicator;
mod eja_tv;
mod geo_blocked;
mod health_cache;
mod iptv_org;
mod language_classifier;
mod m3u_parser;
mod models;
mod playlist_writer;
mod stream_verifier;

use config::Config;
use deduplicator::Deduplicator;
use eja_tv::EjaTvCrawler;
use geo_blocked::GeoBlockedManager;
use health_cache::HealthCache;
use iptv_org::IPTVOrgCatalog;
use language_classifier::LanguageClassifier;
use m3u_parser::M3UParser;
use models::{Channel, SourceStat};
use playlist_writer::PlaylistWriter;
use stream_verifier::StreamVerifier;

#[global_allocator]
static GLOBAL: mimalloc::MiMalloc = mimalloc::MiMalloc;

use anyhow::Result;
use clap::Parser;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tracing::info;

#[derive(Parser, Debug)]
#[command(
    name = "iptv-aggregator",
    version,
    about = "Automated Multi-Source Global IPTV Aggregator"
)]
struct Args {
    /// Config file path
    #[arg(short, long, default_value = "config.json")]
    config: PathBuf,

    /// Dry run (don't write playlists)
    #[arg(long)]
    dry_run: bool,

    /// Skip stream verification
    #[arg(long)]
    no_verify: bool,

    /// Max verification time in seconds
    #[arg(long)]
    max_verify_seconds: Option<u64>,

    /// Max concurrent workers
    #[arg(long)]
    max_workers: Option<usize>,

    /// Verbose logging
    #[arg(short, long)]
    verbose: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();

    // Initialize tracing
    let filter = if args.verbose { "debug" } else { "info" };
    tracing_subscriber::fmt().with_env_filter(filter).init();

    let start_time = Instant::now();
    info!("Starting IPTV Aggregator v5.0.0 (Rust Edition)");

    // Load configuration
    let config = Config::load(&args.config).await?;
    let max_verify_seconds = args.max_verify_seconds.unwrap_or(config.max_verify_seconds);
    let max_workers = args.max_workers.unwrap_or(config.max_workers);
    let _verify_streams = !args.no_verify && config.verify_streams;

    // Initialize health cache
    let health_cache = Arc::new(HealthCache::new("cache/health_cache.json").await?);
    info!("Health cache loaded: {} entries", health_cache.len());

    // Load IPTV-org catalog for EPG/logo enrichment
    info!("Loading IPTV-org catalog (channels + logos)...");
    let catalog = IPTVOrgCatalog::fetch().await?;
    info!(
        "IPTV-org catalog loaded: {} channels, {} logos",
        catalog.channels.len(),
        catalog.logos.len()
    );

    // Initialize language classifier
    let lang_classifier = LanguageClassifier::new(&catalog);
    info!(
        "Language classifier initialized with {} languages",
        lang_classifier.supported_languages().len()
    );

    // Initialize geo-blocked manager
    let geo_manager = Arc::new(GeoBlockedManager::new("playlists/geoblocked").await?);
    info!("Geo-blocked manager initialized");

    // Initialize EJA.tv crawler
    let eja_crawler = EjaTvCrawler::new(config.clone(), Arc::new(lang_classifier.clone()));

    // Initialize M3U parser
    let m3u_parser = M3UParser::new(config.clone());

    // Initialize stream verifier
    let verifier = StreamVerifier::new(max_workers, max_verify_seconds).await?;

    // Initialize deduplicator
    let deduplicator = Deduplicator::new(config.deduplicate_channels);

    // Initialize playlist writer
    let playlist_writer = PlaylistWriter::new(config.clone());

    // =====================================================================
    // STEP 1: Crawl EJA.tv for streams
    // =====================================================================
    info!("Step 1/6: Crawling EJA.tv queries...");
    let eja_channels = eja_crawler.crawl_all(&lang_classifier).await?;
    info!(
        "EJA.tv crawl completed: {} unique streams found",
        eja_channels.len()
    );

    // =====================================================================
    // STEP 2: Parse external M3U sources
    // =====================================================================
    info!(
        "Step 2/6: Parsing {} external M3U sources...",
        config.external_m3u_sources.len()
    );
    let mut external_channels = Vec::new();
    let mut source_stats = HashMap::new();

    for source in &config.external_m3u_sources {
        let channels = m3u_parser.parse_source(source, &lang_classifier).await?;
        let count = channels.len();
        source_stats.insert(
            source.name.clone(),
            SourceStat {
                source: source.name.clone(),
                alive: 0, // Will be updated after verification
                imported: count,
            },
        );
        info!("  [{}] -> {} candidates", source.name, count);
        external_channels.extend(channels);
    }

    // =====================================================================
    // STEP 3: Merge all channels, apply health cache filtering
    // =====================================================================
    info!("Step 3/6: Merging channels and applying health cache...");

    let mut all_channels: HashMap<String, Channel> = HashMap::new();

    // Add EJA.tv channels
    for ch in eja_channels {
        all_channels.insert(ch.stream_url.clone(), ch);
    }

    // Add external channels (merge with existing)
    for ch in external_channels {
        all_channels
            .entry(ch.stream_url.clone())
            .and_modify(|existing| existing.merge(&ch))
            .or_insert(ch);
    }

    // Filter out dead-cached URLs (but preserve geo-blocked 403/451)
    let mut filtered_channels = Vec::new();
    let mut skipped_cached = 0;
    for ch in all_channels.values() {
        if health_cache.should_skip(&ch.stream_url) && !geo_manager.is_geo_blocked(&ch.stream_url) {
            skipped_cached += 1;
            continue;
        }
        filtered_channels.push(ch.clone());
    }
    info!(
        "  Filtered {} channels ({} skipped from health cache)",
        filtered_channels.len(),
        skipped_cached
    );

    // =====================================================================
    // STEP 4: Fair round-robin verification with per-host limits
    // =====================================================================
    info!(
        "Step 4/6: Verifying {} streams with fair scheduling...",
        filtered_channels.len()
    );
    let verified_channels = verifier
        .verify_all(filtered_channels, health_cache.clone(), geo_manager.clone())
        .await?;
    info!(
        "Verification complete: {} live channels",
        verified_channels.len()
    );

    // Update source stats with alive counts
    for ch in &verified_channels {
        if let Some(stat) = source_stats.get_mut(&ch.source_name) {
            stat.alive += 1;
        }
    }

    // Save health cache
    health_cache.save().await?;
    info!("Health cache saved");

    // =====================================================================
    // STEP 5: Deduplicate channels (preserving distinguishing tokens)
    // =====================================================================
    info!("Step 5/6: Deduplicating channels...");
    let deduplicated = deduplicator.deduplicate(verified_channels);
    info!(
        "Deduplication complete: {} unique channels",
        deduplicated.len()
    );

    // =====================================================================
    // STEP 6: Write all playlists (per-language, per-country, geo-blocked, special)
    // =====================================================================
    info!("Step 6/6: Writing playlists...");

    if !args.dry_run {
        playlist_writer
            .write_all(
                &deduplicated,
                &source_stats,
                &lang_classifier,
                &geo_manager,
                &config,
            )
            .await?;
        info!("All playlists written successfully");
    } else {
        info!("Dry run: skipping playlist write");
    }

    // Print summary
    let elapsed = start_time.elapsed();
    info!("Build completed in {:.2}s", elapsed.as_secs_f64());
    print_summary(
        &deduplicated,
        &source_stats,
        &lang_classifier,
        &geo_manager,
        elapsed,
    );

    Ok(())
}

fn print_summary(
    channels: &[Channel],
    source_stats: &HashMap<String, SourceStat>,
    lang_classifier: &LanguageClassifier,
    geo_manager: &GeoBlockedManager,
    elapsed: Duration,
) {
    println!("\n=== IPTV Aggregator v5.0.0 Build Summary ===");
    println!("Build time: {:.2}s", elapsed.as_secs_f64());
    println!("Total unique live channels: {}", channels.len());
    println!();

    println!("By Language:");
    for lang in lang_classifier.supported_languages() {
        let count = channels.iter().filter(|c| c.language == lang).count();
        if count > 0 {
            println!("  {}: {}", lang, count);
        }
    }
    println!();

    println!("By Country:");
    let mut countries: HashMap<String, usize> = HashMap::new();
    for ch in channels {
        *countries.entry(ch.country.clone()).or_insert(0) += 1;
    }
    let mut sorted_countries: Vec<_> = countries.into_iter().collect();
    sorted_countries.sort_by_key(|a| std::cmp::Reverse(a.1));
    for (country, count) in sorted_countries.iter().take(20) {
        println!("  {}: {}", country, count);
    }
    println!();

    println!("Geo-blocked playlists:");
    for (country, urls) in geo_manager.playlists() {
        println!("  {}: {} streams", country, urls.len());
    }
    println!();

    println!("Source Statistics:");
    for stat in source_stats.values() {
        println!("  {}: {}/{} alive", stat.source, stat.alive, stat.imported);
    }
}
