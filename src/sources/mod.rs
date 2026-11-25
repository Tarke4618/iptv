//! Dynamic Source Discovery Module
//! 
//! Provides auto-discovery, filtering and fetching of IPTV sources from
//! multiple categories including public M3U lists, GitHub repositories,
//! community collections, and authenticated geo-blocked channels.

#![allow(dead_code)] // Enabled for public API intended for Tauri frontend

pub mod github;
pub mod source;

// Public API for Tauri frontend
#[allow(unused_imports)]
pub use github::{GithubSource, HttpSource};
#[allow(unused_imports)]
pub use source::{ChannelSource, SourceCategory, SourceMeta, SourceQuery, SourceRegistry};