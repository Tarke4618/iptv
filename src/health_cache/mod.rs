use anyhow::Result;
use dashmap::DashMap;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::fs;

#[derive(Debug, Clone, Serialize, Deserialize)]
struct HealthRecord {
    fails: u32,
    ts: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct HealthCacheData {
    data: HashMap<String, HealthRecord>,
}

pub struct HealthCache {
    path: String,
    data: Arc<DashMap<String, HealthRecord>>,
    now: u64,
}

impl HealthCache {
    pub async fn new(path: &str) -> Result<Self> {
        let path = path.to_string();
        let data = Arc::new(DashMap::new());
        let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();

        // Load existing cache
        if let Ok(content) = fs::read_to_string(&path).await
            && let Ok(cache_data) = serde_json::from_str::<HealthCacheData>(&content)
        {
            let cutoff = now.saturating_sub(14 * 86400); // 14 days
            for (url, record) in cache_data.data {
                if record.ts >= cutoff {
                    data.insert(url, record);
                }
            }
        }

        Ok(Self { path, data, now })
    }

    pub fn should_skip(&self, url: &str) -> bool {
        if let Some(record) = self.data.get(url) {
            record.fails >= 3 && (self.now.saturating_sub(record.ts)) < (72 * 3600)
        } else {
            false
        }
    }

    pub fn mark_alive(&self, url: &str) {
        self.data.remove(url);
    }

    #[allow(dead_code)]
    pub fn mark_dead(&self, url: &str) {
        self.data
            .entry(url.to_string())
            .and_modify(|r| {
                r.fails = (r.fails + 1).min(10);
                r.ts = self.now;
            })
            .or_insert(HealthRecord {
                fails: 1,
                ts: self.now,
            });
    }

    pub fn mark_geo_blocked(&self, url: &str) {
        // Don't add to dead cache - geo-blocked streams are valid but region-locked
        // Just ensure we don't retry them in this run
        self.data
            .entry(url.to_string())
            .and_modify(|r| {
                // Reset fails for geo-blocked so they don't get permanently cached as dead
                r.fails = 0;
                r.ts = self.now;
            })
            .or_insert(HealthRecord {
                fails: 0,
                ts: self.now,
            });
    }

    pub async fn save(&self) -> Result<()> {
        let mut map = HashMap::new();
        for entry in self.data.iter() {
            map.insert(entry.key().clone(), entry.value().clone());
        }
        let cache_data = HealthCacheData { data: map };
        let content = serde_json::to_string(&cache_data)?;
        if let Some(parent) = Path::new(&self.path).parent() {
            fs::create_dir_all(parent).await?;
        }
        fs::write(&self.path, content).await?;
        Ok(())
    }

    pub fn len(&self) -> usize {
        self.data.len()
    }
}
