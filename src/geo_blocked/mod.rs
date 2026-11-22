use anyhow::Result;
use dashmap::DashMap;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::fs;

pub struct GeoBlockedManager {
    base_dir: String,
    playlists: Arc<DashMap<String, Vec<String>>>,
}

impl GeoBlockedManager {
    pub async fn new(base_dir: &str) -> Result<Self> {
        let base_dir = base_dir.to_string();
        let playlists = Arc::new(DashMap::new());

        // Ensure directory exists
        fs::create_dir_all(&base_dir).await?;

        Ok(Self {
            base_dir,
            playlists,
        })
    }

    pub fn add_geo_blocked(&self, country: &str, url: String) {
        let country = self.normalize_country(country);
        self.playlists.entry(country).or_default().push(url);
    }

    pub fn is_geo_blocked(&self, url: &str) -> bool {
        for urls in self.playlists.iter() {
            if urls.iter().any(|u| u == url) {
                return true;
            }
        }
        false
    }

    pub fn playlists(&self) -> HashMap<String, Vec<String>> {
        let mut result = HashMap::new();
        for entry in self.playlists.iter() {
            let mut urls = entry.value().clone();
            urls.sort();
            urls.dedup();
            result.insert(entry.key().clone(), urls);
        }
        result
    }

    pub async fn write_playlists(&self) -> Result<()> {
        for entry in self.playlists.iter() {
            let country = entry.key();
            let urls = entry.value();

            if urls.is_empty() {
                continue;
            }

            let mut unique_urls = urls.clone();
            unique_urls.sort();
            unique_urls.dedup();

            let filename = format!("{}/{}.m3u", self.base_dir, safe_filename(country));
            let mut content = String::from("#EXTM3U\n");

            for url in unique_urls {
                content.push_str(&format!("#EXTINF:-1 tvg-id=\"\" tvg-name=\"\" tvg-logo=\"\" group-title=\"Geo-Blocked\",{}\n", url));
                content.push_str(&format!("{}\n", url));
            }

            fs::write(&filename, content).await?;
        }
        Ok(())
    }

    fn normalize_country(&self, country: &str) -> String {
        let lower_owned = country.to_lowercase();
        let lower = lower_owned.trim();
        match lower {
            "in" | "india" => "India",
            "us" | "usa" | "united states" => "United_States",
            "gb" | "uk" | "united kingdom" => "United_Kingdom",
            "ca" | "canada" => "Canada",
            "au" | "australia" => "Australia",
            "de" | "germany" => "Germany",
            "fr" | "france" => "France",
            "es" | "spain" => "Spain",
            "it" | "italy" => "Italy",
            "jp" | "japan" => "Japan",
            "kr" | "korea" => "South_Korea",
            "cn" | "china" => "China",
            "ru" | "russia" => "Russia",
            "br" | "brazil" => "Brazil",
            "mx" | "mexico" => "Mexico",
            "ar" | "argentina" => "Argentina",
            "sa" | "saudi arabia" => "Saudi_Arabia",
            "ae" | "uae" => "UAE",
            "sg" | "singapore" => "Singapore",
            "my" | "malaysia" => "Malaysia",
            "th" | "thailand" => "Thailand",
            "vn" | "vietnam" => "Vietnam",
            "id" | "indonesia" => "Indonesia",
            "ph" | "philippines" => "Philippines",
            "pk" | "pakistan" => "Pakistan",
            "bd" | "bangladesh" => "Bangladesh",
            "lk" | "sri lanka" => "Sri_Lanka",
            "np" | "nepal" => "Nepal",
            "mm" | "myanmar" => "Myanmar",
            "kh" | "cambodia" => "Cambodia",
            "la" | "laos" => "Laos",
            "mn" | "mongolia" => "Mongolia",
            "kz" | "kazakhstan" => "Kazakhstan",
            "uz" | "uzbekistan" => "Uzbekistan",
            _ => "Other",
        }
        .to_string()
    }
}

fn safe_filename(name: &str) -> String {
    let re = regex::Regex::new(r"[^a-zA-Z0-9_-]+").unwrap();
    re.replace_all(name, "_").to_lowercase()
}
