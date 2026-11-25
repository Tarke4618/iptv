//! Tauri Application Entry Point
//!
//! Cross-platform IPTV Aggregator App for Android, iOS, Windows, macOS, Linux

#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use iptv_aggregator::{Config, build_playlist};
use std::time::Duration;
use tauri::Manager;

#[tauri::command]
async fn build_playlist_cmd(config_path: Option<String>) -> Result<String, String> {
    let config = match config_path {
        Some(path) => Config::load(std::path::Path::new(&path))
            .await
            .map_err(|e| e.to_string())?,
        None => Config::load(std::path::Path::new("config.json"))
            .await
            .map_err(|e| e.to_string())?,
    };

    let result = build_playlist(config, None, true, 4, Duration::from_secs(30))
        .await
        .map_err(|e| e.to_string())?;

    Ok(format!(
        "Build complete: {} channels in {:?}",
        result.channels.len(),
        result.elapsed
    ))
}

#[tauri::command]
async fn refresh_sources() -> Result<String, String> {
    // Discover and return available sources
    let sources = iptv_aggregator::sources::GithubSource::discover_all();
    Ok(format!("Discovered {} GitHub sources", sources.len()))
}

#[tauri::command]
fn get_app_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            build_playlist_cmd,
            refresh_sources,
            get_app_version
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
