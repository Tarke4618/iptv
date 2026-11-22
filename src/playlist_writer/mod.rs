use anyhow::Result;
use std::collections::{HashMap, HashSet};
use tokio::fs;

use crate::config::Config;
use crate::geo_blocked::GeoBlockedManager;
use crate::language_classifier::LanguageClassifier;
use crate::models::{Channel, SourceStat};

pub struct PlaylistWriter {
    #[allow(dead_code)]
    config: Config,
}

impl PlaylistWriter {
    pub fn new(config: Config) -> Self {
        Self { config }
    }

    pub async fn write_all(
        &self,
        channels: &[Channel],
        source_stats: &HashMap<String, SourceStat>,
        _lang_classifier: &LanguageClassifier,
        geo_manager: &GeoBlockedManager,
        config: &Config,
    ) -> Result<()> {
        // Create directories
        fs::create_dir_all("playlists/languages").await?;
        fs::create_dir_all("playlists/countries").await?;
        fs::create_dir_all("playlists/categories").await?;
        fs::create_dir_all("playlists/searches").await?;
        fs::create_dir_all("playlists/special").await?;
        fs::create_dir_all("playlists/geoblocked").await?;

        // Group channels by language
        let mut by_language: HashMap<String, Vec<&Channel>> = HashMap::new();
        let mut by_country: HashMap<String, Vec<&Channel>> = HashMap::new();
        let mut by_category: HashMap<String, Vec<&Channel>> = HashMap::new();
        let mut fast_channels = Vec::new();
        let mut sports_channels = Vec::new();
        let mut hd_channels = Vec::new();
        let mut multi_audio_channels = Vec::new();

        for ch in channels {
            // By language
            if !ch.language.is_empty() {
                by_language.entry(ch.language.clone()).or_default().push(ch);
            }

            // By country
            if !ch.country.is_empty() {
                by_country.entry(ch.country.clone()).or_default().push(ch);
            }

            // By category
            if !ch.category.is_empty() {
                by_category.entry(ch.category.clone()).or_default().push(ch);
            }

            // Special collections
            if ch.is_fast {
                fast_channels.push(ch);
            }
            if is_sports_channel(ch) {
                sports_channels.push(ch);
            }
            if ch.height >= 720 {
                hd_channels.push(ch);
            }
            if !ch.audio_tracks.is_empty() {
                multi_audio_channels.push(ch);
            }
        }

        // Write language playlists
        for (lang, chs) in &by_language {
            self.write_language_playlist(lang, chs, config).await?;
            self.write_language_backups_playlist(lang, chs, config)
                .await?;
        }

        // Write country playlists
        for (country, chs) in &by_country {
            self.write_country_playlist(country, chs, config).await?;
        }

        // Write category playlists
        for (cat, chs) in &by_category {
            self.write_category_playlist(cat, chs, config).await?;
        }

        // Write special playlists
        self.write_playlist(
            "playlists/special/fast_movies_tv.m3u",
            &fast_channels,
            config,
            "FAST Movies & Live TV",
        )
        .await?;
        self.write_playlist(
            "playlists/special/sports_live.m3u",
            &sports_channels,
            config,
            "Live Sports",
        )
        .await?;
        self.write_playlist(
            "playlists/special/hd_1080p.m3u",
            &hd_channels,
            config,
            "HD 720p/1080p",
        )
        .await?;
        self.write_playlist(
            "playlists/special/multi_audio.m3u",
            &multi_audio_channels,
            config,
            "Multi-Audio Tracks",
        )
        .await?;

        // Write all channels - need to convert &[Channel] to Vec<&Channel>
        let all_channels_ref: Vec<&Channel> = channels.iter().collect();
        self.write_playlist(
            "playlists/all.m3u",
            &all_channels_ref,
            config,
            "All Verified Channels",
        )
        .await?;

        // Write with backups (all channels with mirrors)
        self.write_playlist(
            "playlists/special/with_backups.m3u",
            &all_channels_ref,
            config,
            "All Channels + Backups",
        )
        .await?;

        // Write geo-blocked playlists
        geo_manager.write_playlists().await?;

        // Write index.json for easy access
        self.write_index_json(
            &by_language,
            &by_country,
            &by_category,
            channels,
            source_stats,
        )
        .await?;

        // Update README
        self.write_readme(&by_language, &by_country, channels, source_stats)
            .await?;

        Ok(())
    }

    async fn write_language_playlist(
        &self,
        lang: &str,
        channels: &[&Channel],
        config: &Config,
    ) -> Result<()> {
        let filename = format!("playlists/languages/{}.m3u", safe_filename(lang));
        self.write_playlist(
            &filename,
            channels,
            config,
            &format!("{} Language Channels", lang),
        )
        .await
    }

    async fn write_language_backups_playlist(
        &self,
        lang: &str,
        channels: &[&Channel],
        config: &Config,
    ) -> Result<()> {
        let filename = format!("playlists/languages/{}_backups.m3u", safe_filename(lang));
        // For backups, we include all mirrors (handled by deduplicator)
        self.write_playlist(
            &filename,
            channels,
            config,
            &format!("{} Language Channels + Backups", lang),
        )
        .await
    }

    async fn write_country_playlist(
        &self,
        country: &str,
        channels: &[&Channel],
        config: &Config,
    ) -> Result<()> {
        let filename = format!("playlists/countries/{}.m3u", safe_filename(country));
        self.write_playlist(
            &filename,
            channels,
            config,
            &format!("{} Channels", country),
        )
        .await
    }

    async fn write_category_playlist(
        &self,
        category: &str,
        channels: &[&Channel],
        config: &Config,
    ) -> Result<()> {
        let filename = format!("playlists/categories/{}.m3u", safe_filename(category));
        self.write_playlist(
            &filename,
            channels,
            config,
            &format!("{} Channels", category),
        )
        .await
    }

    async fn write_playlist(
        &self,
        path: &str,
        channels: &[&Channel],
        config: &Config,
        _title: &str,
    ) -> Result<()> {
        if channels.is_empty() {
            return Ok(());
        }

        // Sort by name
        let mut sorted: Vec<_> = channels.to_vec();
        sorted.sort_by_key(|a| a.name.to_lowercase());

        // Deduplicate by stream URL
        let mut seen = HashSet::new();
        let mut unique = Vec::new();
        for ch in sorted {
            if seen.insert(ch.stream_url.clone()) {
                unique.push(ch);
            }
        }

        let mut content = String::new();
        content.push_str("#EXTM3U\n");

        // Add EPG URLs
        if !config.epg_urls.is_empty() {
            let epg_attr = config.epg_urls.join(",");
            content.push_str(&format!(
                "#EXTM3U url-tvg=\"{}\" x-tvg-url=\"{}\"\n",
                epg_attr, epg_attr
            ));
        }

        for ch in unique {
            let display_name = if config.tag_stream_info && !ch.quality_tag.is_empty() {
                format!("{} [{}]", ch.name, ch.quality_tag)
            } else {
                ch.name.clone()
            };

            let tvg_id = if ch.tvg_id.is_empty() {
                generate_tvg_id(&ch.name)
            } else {
                ch.tvg_id.clone()
            };

            let group = if ch.category.is_empty() {
                ch.country.clone()
            } else {
                ch.category.clone()
            };

            content.push_str(&format!(
                "#EXTINF:-1 tvg-id=\"{}\" tvg-name=\"{}\" tvg-logo=\"{}\" tvg-country=\"{}\" tvg-language=\"{}\" group-title=\"{}\",{}\n",
                escape_m3u(&tvg_id),
                escape_m3u(&ch.name),
                escape_m3u(&ch.logo),
                escape_m3u(&ch.country),
                escape_m3u(&ch.language),
                escape_m3u(&group),
                escape_m3u(&display_name)
            ));

            // Add VLC options for headers
            if let Some(ua) = ch.extra_headers.get("User-Agent") {
                content.push_str(&format!("#EXTVLCOPT:http-user-agent={}\n", ua));
            }
            if let Some(ref_) = ch.extra_headers.get("Referer") {
                content.push_str(&format!("#EXTVLCOPT:http-referrer={}\n", ref_));
            }
            if let Some(origin) = ch.extra_headers.get("Origin") {
                content.push_str(&format!("#EXTVLCOPT:http-origin={}\n", origin));
            }
            if let Some(cookie) = ch.extra_headers.get("Cookie") {
                content.push_str(&format!("#EXTVLCOPT:http-cookie={}\n", cookie));
            }

            // Add Kodi pipe headers for compatibility
            if !ch.extra_headers.is_empty() {
                let mut pipes = Vec::new();
                if let Some(ua) = ch.extra_headers.get("User-Agent") {
                    pipes.push(format!("User-Agent={}", urlencoding::encode(ua)));
                }
                if let Some(ref_) = ch.extra_headers.get("Referer") {
                    pipes.push(format!("Referer={}", urlencoding::encode(ref_)));
                }
                if let Some(origin) = ch.extra_headers.get("Origin") {
                    pipes.push(format!("Origin={}", urlencoding::encode(origin)));
                }
                if !pipes.is_empty() {
                    content.push_str(&format!(
                        "#EXTKODIPROP:{}={}\n",
                        ch.stream_url,
                        pipes.join("|")
                    ));
                }
            }

            content.push_str(&format!("{}\n", ch.stream_url));
        }

        fs::write(path, content).await?;
        Ok(())
    }

    async fn write_index_json(
        &self,
        by_language: &HashMap<String, Vec<&Channel>>,
        by_country: &HashMap<String, Vec<&Channel>>,
        by_category: &HashMap<String, Vec<&Channel>>,
        all_channels: &[Channel],
        source_stats: &HashMap<String, SourceStat>,
    ) -> Result<()> {
        use serde_json::json;

        let mut index = json!({
            "version": "5.0.0",
            "total_channels": all_channels.len(),
            "languages": {},
            "countries": {},
            "categories": {},
            "special": {},
            "sources": {}
        });

        for (lang, chs) in by_language {
            index["languages"][lang] = json!({
                "count": chs.len(),
                "url": format!("https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/languages/{}.m3u", safe_filename(lang)),
                "backups_url": format!("https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/languages/{}_backups.m3u", safe_filename(lang)),
            });
        }

        for (country, chs) in by_country {
            index["countries"][country] = json!({
                "count": chs.len(),
                "url": format!("https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/countries/{}.m3u", safe_filename(country)),
            });
        }

        for (cat, chs) in by_category {
            index["categories"][cat] = json!({
                "count": chs.len(),
                "url": format!("https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/categories/{}.m3u", safe_filename(cat)),
            });
        }

        index["special"]["all"] = json!({
            "count": all_channels.len(),
            "url": "https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/all.m3u",
        });
        index["special"]["with_backups"] = json!({
            "url": "https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/special/with_backups.m3u",
        });
        index["special"]["fast_movies_tv"] = json!({
            "url": "https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/special/fast_movies_tv.m3u",
        });
        index["special"]["sports_live"] = json!({
            "url": "https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/special/sports_live.m3u",
        });
        index["special"]["hd_1080p"] = json!({
            "url": "https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/special/hd_1080p.m3u",
        });
        index["special"]["multi_audio"] = json!({
            "url": "https://raw.githubusercontent.com/Tarke4618/iptv/main/playlists/special/multi_audio.m3u",
        });

        for (name, stat) in source_stats {
            index["sources"][name] = json!({
                "alive": stat.alive,
                "imported": stat.imported,
            });
        }

        fs::write(
            "playlists/index.json",
            serde_json::to_string_pretty(&index)?,
        )
        .await?;
        Ok(())
    }

    async fn write_readme(
        &self,
        by_language: &HashMap<String, Vec<&Channel>>,
        by_country: &HashMap<String, Vec<&Channel>>,
        all_channels: &[Channel],
        source_stats: &HashMap<String, SourceStat>,
    ) -> Result<()> {
        let mut readme = String::new();
        readme.push_str("# Verified Multi-Source Global IPTV Playlists (Rust v5.0.0)\n\n");
        readme.push_str(&format!(
            "**Last Synced:** `{}` | **Total Channels:** `{}`\n\n",
            chrono::Utc::now().format("%d %b %Y, %H:%M UTC"),
            all_channels.len()
        ));

        readme.push_str("## By Language\n\n");
        readme.push_str("| Language | Verified Channels | Raw M3U URL | Backups |\n");
        readme.push_str("| :--- | :---: | :--- | :--- |\n");
        for (lang, chs) in by_language {
            let lang_safe = safe_filename(lang);
            readme.push_str(&format!(
                "| **{}** | {} | `playlists/languages/{}.m3u` | `playlists/languages/{}_backups.m3u` |\n",
                lang, chs.len(), lang_safe, lang_safe
            ));
        }

        readme.push_str("\n## By Country\n\n");
        readme.push_str("| Country | Verified Channels | Raw M3U URL |\n");
        readme.push_str("| :--- | :---: | :--- |\n");
        for (country, chs) in by_country {
            let country_safe = safe_filename(country);
            readme.push_str(&format!(
                "| **{}** | {} | `playlists/countries/{}.m3u` |\n",
                country,
                chs.len(),
                country_safe
            ));
        }

        readme.push_str("\n## Special Playlists\n\n");
        readme.push_str("| Playlist | Verified Channels | Raw M3U URL |\n");
        readme.push_str("| :--- | :---: | :--- |\n");
        readme.push_str(&format!(
            "| **All Verified Channels** | {} | `playlists/all.m3u` |\n",
            all_channels.len()
        ));
        readme.push_str(&format!(
            "| **FAST Movies & Live TV** | {} | `playlists/special/fast_movies_tv.m3u` |\n",
            0
        )); // Will be updated
        readme.push_str(&format!(
            "| **Live Sports Hub** | {} | `playlists/special/sports_live.m3u` |\n",
            0
        ));
        readme.push_str(&format!(
            "| **HD 720p/1080p** | {} | `playlists/special/hd_1080p.m3u` |\n",
            0
        ));
        readme.push_str(&format!(
            "| **Multi-Audio Tracks** | {} | `playlists/special/multi_audio.m3u` |\n",
            0
        ));
        readme.push_str(&format!(
            "| **All + Backup Mirrors** | {} | `playlists/special/with_backups.m3u` |\n",
            0
        ));

        readme.push_str("\n## Source Statistics\n\n");
        readme.push_str("| Source | Alive / Imported |\n");
        readme.push_str("| :--- | :---: |\n");
        for (name, stat) in source_stats {
            readme.push_str(&format!(
                "| {} | {} / {} |\n",
                name, stat.alive, stat.imported
            ));
        }

        readme.push_str("\n---\n*Auto-generated by [Tarke4618/iptv](https://github.com/Tarke4618/iptv) via GitHub Actions (every 12 hours)*\n");

        fs::write("README.md", readme).await?;
        Ok(())
    }
}

fn safe_filename(name: &str) -> String {
    let re = regex::Regex::new(r"[^a-zA-Z0-9_-]+").unwrap();
    re.replace_all(name, "_").to_lowercase()
}

fn escape_m3u(s: &str) -> String {
    s.replace(',', "\\,").replace(['\n', '\r'], " ")
}

fn generate_tvg_id(name: &str) -> String {
    let re = regex::Regex::new(r"[^a-zA-Z0-9]+").unwrap();
    let lower = name.to_lowercase();
    let id = re.replace_all(&lower, ".");
    id.trim_matches('.').to_string()
}

fn is_sports_channel(ch: &Channel) -> bool {
    let blob = format!("{} {} {}", ch.name, ch.category, ch.source_name).to_lowercase();
    for kw in [
        "sport",
        "cricket",
        "football",
        "soccer",
        "tennis",
        "f1",
        "racing",
        "boxing",
        "ufc",
        "wwe",
        "espn",
        "dazn",
        "beinsport",
    ] {
        if blob.contains(kw) {
            return true;
        }
    }
    false
}
