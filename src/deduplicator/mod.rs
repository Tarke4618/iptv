use crate::models::{Channel, name_quality_score, normalize_channel_key};
use std::collections::HashMap;

pub struct Deduplicator {
    enabled: bool,
    max_mirrors: usize,
}

impl Deduplicator {
    pub fn new(enabled: bool) -> Self {
        Self {
            enabled,
            max_mirrors: 3,
        }
    }

    pub fn deduplicate(&self, channels: Vec<Channel>) -> Vec<Channel> {
        if !self.enabled {
            return channels;
        }

        // Group by normalized key
        let mut grouped: HashMap<String, Vec<Channel>> = HashMap::new();
        for ch in channels {
            let key = normalize_channel_key(&ch.name);
            if key.is_empty() {
                continue;
            }
            grouped.entry(key).or_default().push(ch);
        }

        let mut output = Vec::new();

        for (_, mut mirrors) in grouped {
            if mirrors.is_empty() {
                continue;
            }

            // Sort mirrors by quality criteria
            mirrors.sort_by(|a, b| {
                // 1. Higher resolution first
                if a.height != b.height {
                    return b.height.cmp(&a.height);
                }
                // 2. HTTPS preferred
                let a_https = a.stream_url.starts_with("https://");
                let b_https = b.stream_url.starts_with("https://");
                if a_https != b_https {
                    return b_https.cmp(&a_https);
                }
                // 3. Has tvg-id preferred
                let a_has_id = !a.tvg_id.is_empty();
                let b_has_id = !b.tvg_id.is_empty();
                if a_has_id != b_has_id {
                    return b_has_id.cmp(&a_has_id);
                }
                // 4. Better name quality
                let a_score = name_quality_score(&a.name);
                let b_score = name_quality_score(&b.name);
                if a_score != b_score {
                    return b_score.cmp(&a_score);
                }
                // 5. Lower latency
                a.latency_ms.cmp(&b.latency_ms)
            });

            // Pick best title
            let best_title = mirrors
                .iter()
                .max_by_key(|m| name_quality_score(&m.name))
                .map(|m| m.name.clone())
                .unwrap_or_else(|| mirrors[0].name.clone());

            // Merge metadata from all mirrors into the best one
            let mut best = mirrors[0].clone();
            best.name = best_title.clone();

            for mirror in &mirrors[1..] {
                if best.tvg_id.is_empty() && !mirror.tvg_id.is_empty() {
                    best.tvg_id = mirror.tvg_id.clone();
                }
                if best.logo.is_empty() && !mirror.logo.is_empty() {
                    best.logo = mirror.logo.clone();
                }
                if best.country.is_empty() && !mirror.country.is_empty() {
                    best.country = mirror.country.clone();
                }
                if best.language.is_empty() && !mirror.language.is_empty() {
                    best.language = mirror.language.clone();
                }
                if best.category.is_empty() && !mirror.category.is_empty() {
                    best.category = mirror.category.clone();
                }
                if best.height < mirror.height {
                    best.height = mirror.height;
                }
                if best.quality_tag.is_empty() && !mirror.quality_tag.is_empty() {
                    best.quality_tag = mirror.quality_tag.clone();
                }
                // Merge audio tracks
                for track in &mirror.audio_tracks {
                    if !best.audio_tracks.iter().any(|t| t.uri == track.uri) {
                        best.audio_tracks.push(track.clone());
                    }
                }
            }

            // Add primary
            output.push(best);

            // Add backup mirrors (unique URLs only)
            let mut seen_urls = HashMap::new();
            seen_urls.insert(mirrors[0].stream_url.clone(), true);

            for (i, mirror) in mirrors.iter().skip(1).enumerate() {
                if seen_urls.contains_key(&mirror.stream_url) {
                    continue;
                }
                if i >= self.max_mirrors - 1 {
                    break;
                }
                seen_urls.insert(mirror.stream_url.clone(), true);

                let mut mirror_ch = mirror.clone();
                mirror_ch.name = format!("{} [Mirror {}]", best_title, i + 2);
                output.push(mirror_ch);
            }
        }

        output
    }
}
