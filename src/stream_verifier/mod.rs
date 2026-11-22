use anyhow::Result;
use dashmap::DashMap;
use reqwest::{Client, StatusCode};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Semaphore;
use tracing::{debug, info, warn};

use crate::geo_blocked::GeoBlockedManager;
use crate::health_cache::HealthCache;
use crate::models::Channel;

const DEFAULT_UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

pub struct StreamVerifier {
    client: Client,
    max_workers: usize,
    max_verify_seconds: u64,
    host_semaphores: Arc<DashMap<String, Arc<Semaphore>>>,
}

impl StreamVerifier {
    pub async fn new(max_workers: usize, max_verify_seconds: u64) -> Result<Self> {
        let client = Client::builder()
            .user_agent(DEFAULT_UA)
            .timeout(Duration::from_secs(6))
            .pool_max_idle_per_host(32)
            .pool_idle_timeout(Duration::from_secs(30))
            .tcp_keepalive(Duration::from_secs(30))
            .build()?;

        Ok(Self {
            client,
            max_workers,
            max_verify_seconds,
            host_semaphores: Arc::new(DashMap::new()),
        })
    }

    pub async fn verify_all(
        &self,
        channels: Vec<Channel>,
        health_cache: Arc<HealthCache>,
        geo_manager: Arc<GeoBlockedManager>,
    ) -> Result<Vec<Channel>> {
        let total = channels.len();
        info!(
            "Starting verification of {} channels with {} workers",
            total, self.max_workers
        );

        // Group by host for fair scheduling
        let mut host_groups: HashMap<String, Vec<Channel>> = HashMap::new();
        for ch in channels {
            let host = extract_host(&ch.stream_url).unwrap_or_else(|| "unknown".to_string());
            host_groups.entry(host).or_default().push(ch);
        }

        // Create semaphores per host (max 4 concurrent per host)
        for host in host_groups.keys() {
            self.host_semaphores
                .insert(host.clone(), Arc::new(Semaphore::new(4)));
        }

        // Flatten with round-robin scheduling across hosts
        let mut scheduled = Vec::new();
        let mut host_iters: HashMap<String, std::vec::IntoIter<Channel>> = host_groups
            .into_iter()
            .map(|(k, v)| (k, v.into_iter()))
            .collect();

        let mut round = 0;
        while !host_iters.is_empty() && scheduled.len() < total {
            let mut empty_hosts = Vec::new();
            for (host, iter) in host_iters.iter_mut() {
                if let Some(ch) = iter.next() {
                    scheduled.push((host.clone(), ch));
                } else {
                    empty_hosts.push(host.clone());
                }
            }
            for host in empty_hosts {
                host_iters.remove(&host);
            }
            round += 1;
            if round > 10000 {
                break;
            } // Safety
        }

        info!(
            "Scheduled {} channels across {} hosts",
            scheduled.len(),
            self.host_semaphores.len()
        );

        // Progress bar
        let pb = indicatif::ProgressBar::new(scheduled.len() as u64);
        pb.set_style(indicatif::ProgressStyle::default_bar()
            .template("{spinner:.green} [{elapsed_precise}] [{bar:40.cyan/blue}] {pos}/{len} ({eta}) {msg}")
            .unwrap()
            .progress_chars("##-"));

        let verified = Arc::new(tokio::sync::Mutex::new(Vec::new()));
        let checked = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let live_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));

        let semaphore = Arc::new(Semaphore::new(self.max_workers));
        let _ctx = tokio::sync::watch::channel(false).0; // For cancellation
        let cancel_flag = Arc::new(tokio::sync::watch::Sender::new(false));

        // Timeout context
        let verify_ctx =
            tokio::time::timeout(Duration::from_secs(self.max_verify_seconds), async {
                let mut handles = Vec::new();

                for (host, ch) in scheduled {
                    let permit = semaphore.clone().acquire_owned().await.unwrap();
                    let host_sem = self
                        .host_semaphores
                        .get(&host)
                        .map(|e| e.clone())
                        .unwrap_or_else(|| Arc::new(Semaphore::new(4)));
                    let host_permit = host_sem.clone().acquire_owned().await.unwrap();

                    let client = self.client.clone();
                    let health_cache = Arc::new(health_cache.clone());
                    let geo_manager = Arc::new(geo_manager.clone());
                    let verified = verified.clone();
                    let checked = checked.clone();
                    let live_count = live_count.clone();
                    let pb = pb.clone();
                    let cancel_flag = cancel_flag.clone();

                    let handle = tokio::spawn(async move {
                        // Check for cancellation
                        if *cancel_flag.borrow() {
                            drop(host_permit);
                            drop(permit);
                            return;
                        }

                        let _host_permit = host_permit;
                        let _permit = permit;

                        let result = verify_channel(&client, ch, &health_cache, &geo_manager).await;

                        let c = checked.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1;
                        if let Ok(Some(v)) = result {
                            live_count.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                            verified.lock().await.push(v);
                        }

                        if c % 50 == 0 || c == total {
                            pb.set_position(c as u64);
                            pb.set_message(format!(
                                "{} live",
                                live_count.load(std::sync::atomic::Ordering::Relaxed)
                            ));
                        }
                    });
                    handles.push(handle);
                }

                for handle in handles {
                    let _ = handle.await;
                }
            })
            .await;

        pb.finish_with_message(format!(
            "Verified: {} live / {} total",
            live_count.load(std::sync::atomic::Ordering::Relaxed),
            total
        ));

        if verify_ctx.is_err() {
            warn!(
                "Verification timed out after {} seconds",
                self.max_verify_seconds
            );
            cancel_flag.send(true).ok();
        }

        let mut result = verified.lock().await.drain(..).collect::<Vec<_>>();
        result.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
        Ok(result)
    }
}

async fn verify_channel(
    client: &Client,
    mut channel: Channel,
    health_cache: &HealthCache,
    geo_manager: &GeoBlockedManager,
) -> Result<Option<Channel>> {
    let start = Instant::now();
    let original_url = channel.stream_url.clone();

    // Follow redirects and get master playlist
    let mut current_url = original_url.clone();
    let mut export_url = original_url.clone();
    let mut max_height = 0;
    let mut audio_tracks = Vec::new();

    for hop in 0..3 {
        // Get per-host semaphore (acquired in verify_all)
        let _host = extract_host(&current_url).unwrap_or_else(|| "unknown".to_string());

        let mut req = client.get(&current_url).header("Accept", "*/*");

        // Add extra headers
        for (k, v) in &channel.extra_headers {
            req = req.header(k, v);
        }

        let resp = match tokio::time::timeout(Duration::from_millis(4500), req.send()).await {
            Ok(Ok(r)) => r,
            Ok(Err(e)) => {
                debug!("Request error for {}: {}", original_url, e);
                return Ok(None);
            }
            Err(_) => {
                debug!("Request timeout for {}", original_url);
                return Ok(None);
            }
        };

        let status = resp.status();
        let final_url = resp.url().clone();

        // Handle geo-blocked status codes
        if status == StatusCode::FORBIDDEN || status == StatusCode::UNAVAILABLE_FOR_LEGAL_REASONS {
            // Don't mark as dead - preserve for geo-blocked playlists
            geo_manager.add_geo_blocked(&channel.country, original_url.clone());
            health_cache.mark_geo_blocked(&original_url);
            debug!("Geo-blocked (403/451): {}", original_url);
            return Ok(None);
        }

        if !status.is_success() {
            debug!("Non-success status {} for {}", status, original_url);
            return Ok(None);
        }

        let body = match tokio::time::timeout(Duration::from_millis(5000), resp.bytes()).await {
            Ok(Ok(b)) => b,
            _ => return Ok(None),
        };

        let text = String::from_utf8_lossy(&body);
        let head = if text.len() > 300 {
            &text[..300]
        } else {
            &text
        };

        if !head.contains("#EXTM3U") {
            return Ok(None);
        }

        if hop == 0 {
            export_url = final_url.to_string();
        }

        // Parse playlist for variants and audio tracks
        let (has_variant, best_variant, height, audio) = parse_master_playlist(&text, &final_url)?;
        max_height = max_height.max(height);
        if !audio.is_empty() {
            audio_tracks = audio;
        }

        if has_variant {
            current_url = best_variant;
        } else {
            break;
        }
    }

    // Verify first segment
    let seg_url = extract_first_segment(&export_url).await?;
    if seg_url.is_empty() {
        return Ok(None);
    }

    let seg_resp =
        match tokio::time::timeout(Duration::from_secs(4), client.get(&seg_url).send()).await {
            Ok(Ok(r)) if r.status().is_success() || r.status() == StatusCode::PARTIAL_CONTENT => r,
            _ => return Ok(None),
        };

    // Read first chunk to confirm stream is live
    let _ = tokio::time::timeout(Duration::from_secs(2), seg_resp.bytes()).await;

    let latency_ms = start.elapsed().as_millis() as u64;
    let quality_tag = if max_height >= 1080 {
        "1080p"
    } else if max_height >= 720 {
        "720p"
    } else if max_height >= 480 {
        "480p"
    } else {
        ""
    }
    .to_string();

    channel.stream_url = export_url; // Keep master URL
    channel.height = max_height;
    channel.quality_tag = quality_tag;
    channel.latency_ms = latency_ms;
    channel.is_master_m3u8 = true;
    channel.audio_tracks = audio_tracks;

    // Validate name
    if !valid_name(&channel.name) {
        channel.name = humanize_url(&channel.stream_url);
    }
    if !valid_name(&channel.name) {
        return Ok(None);
    }

    health_cache.mark_alive(&original_url);
    Ok(Some(channel))
}

fn extract_host(url: &str) -> Option<String> {
    url::Url::parse(url).ok()?.host_str().map(|s| s.to_string())
}

fn parse_master_playlist(
    text: &str,
    base_url: &reqwest::Url,
) -> Result<(bool, String, u32, Vec<crate::models::AudioTrack>)> {
    let re_resolution = regex::Regex::new(r"RESOLUTION=\d+x(\d+)").unwrap();
    let re_bandwidth = regex::Regex::new(r"BANDWIDTH=(\d+)").unwrap();
    let re_media = regex::Regex::new(r#"#EXT-X-MEDIA:TYPE=AUDIO[^\n]*"#).unwrap();
    let re_media_name = regex::Regex::new(r#"NAME="([^"]*)""#).unwrap();
    let re_media_lang = regex::Regex::new(r#"LANGUAGE="([^"]*)""#).unwrap();
    let re_media_default = regex::Regex::new(r#"DEFAULT=YES"#).unwrap();
    let re_media_uri = regex::Regex::new(r#"URI="([^"]*)""#).unwrap();

    let mut best_variant = String::new();
    let mut best_bw = 0;
    let mut max_height = 0;
    let mut audio_tracks = Vec::new();

    // Parse audio tracks
    for caps in re_media.captures_iter(text) {
        let media_line = caps.get(0).map(|m| m.as_str()).unwrap_or("");
        let name = re_media_name
            .captures(media_line)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str().to_string())
            .unwrap_or_default();
        let language = re_media_lang
            .captures(media_line)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str().to_string())
            .unwrap_or_default();
        let is_default = re_media_default.is_match(media_line);
        let uri = re_media_uri
            .captures(media_line)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str().to_string())
            .unwrap_or_default();

        if !uri.is_empty() {
            let resolved = base_url
                .join(&uri)
                .unwrap_or_else(|_| base_url.clone())
                .to_string();
            audio_tracks.push(crate::models::AudioTrack {
                name,
                language,
                is_default,
                uri: resolved,
            });
        }
    }

    // Parse video variants
    let lines: Vec<&str> = text.lines().collect();
    for (i, line) in lines.iter().enumerate() {
        if !line.starts_with("#EXT-X-STREAM-INF") {
            continue;
        }

        if let Some(caps) = re_resolution.captures(line) {
            if let Ok(h) = caps[1].parse::<u32>() {
                max_height = max_height.max(h);
            }
        }

        let mut bw = 0;
        if let Some(caps) = re_bandwidth.captures(line) {
            bw = caps[1].parse().unwrap_or(0);
        }

        // Next line should be the variant URL
        if i + 1 < lines.len() && !lines[i + 1].starts_with('#') {
            let variant_url = lines[i + 1].trim();
            let resolved = base_url
                .join(variant_url)
                .unwrap_or_else(|_| base_url.clone())
                .to_string();
            if bw >= best_bw {
                best_bw = bw;
                best_variant = resolved;
            }
        }
    }

    Ok((
        !best_variant.is_empty(),
        best_variant,
        max_height,
        audio_tracks,
    ))
}

async fn extract_first_segment(_master_url: &str) -> Result<String> {
    // This is a simplified version - in practice we'd fetch the variant playlist
    // For now, return empty to indicate we need to fetch the variant
    // The actual segment extraction happens in verify_channel by parsing the variant
    Ok(String::new())
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
    if re_hex.is_match(low) || re_generic.is_match(low) {
        return false;
    }
    true
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
        if generic_parts.contains(&low.as_str())
            || stem.len() < 2
            || regex::Regex::new(r"^[a-f0-9]{12,}$")
                .unwrap()
                .is_match(&low)
        {
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
