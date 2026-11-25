//! JioTV Service Module
//!
//! Provides OTP-based authentication, token management, and stream proxy
//! for JioTV and related Jio services (JioCinema, JioNews, etc.)

use anyhow::{anyhow, Result};
use async_trait::async_trait;
use chrono::{DateTime, Utc};
use reqwest::{Client, Response, StatusCode};
use reqwest::header::HeaderMap;
use serde::{Deserialize, Serialize};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::sync::RwLock;

use crate::models::Channel;
use crate::sources::ChannelSource;

pub mod auth;
pub mod proxy;

pub use auth::{AuthHeaders, JioAuth};
pub use proxy::JioStreamProxy;

const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);

/// JioTV service for authenticated stream access
pub struct JioTvService {
    client: Client,
    auth: RwLock<JioAuth>,
    tokens: RwLock<JioTokens>,
}

#[derive(Debug, Clone, Default)]
pub struct JioTokens {
    pub access_token: Option<String>,
    pub refresh_token: Option<String>,
    pub expires_at: Option<DateTime<Utc>>,
    pub channel_token: Option<String>,
}

impl JioTokens {
    pub fn is_expired(&self) -> bool {
        self.expires_at
            .map(|e| Utc::now() > e - chrono::Duration::minutes(5))
            .unwrap_or(true)
    }

    pub fn should_refresh(&self) -> bool {
        self.expires_at
            .map(|e| Utc::now() > e - chrono::Duration::minutes(10))
            .unwrap_or(true)
    }
}

impl JioTvService {
    pub fn new() -> Result<Self> {
        let client = Client::builder()
            .user_agent("Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537")
            .timeout(DEFAULT_TIMEOUT)
            .cookie_store(true)
            .build()?;

        let auth = RwLock::new(JioAuth::default());
        let tokens = RwLock::new(JioTokens::default());

        Ok(Self {
            client,
            auth,
            tokens,
        })
    }

    /// Authenticate using OTP (Mobile Number + OTP)
    pub async fn authenticate_with_otp(&self, mobile: &str, otp: &str) -> Result<()> {
        let auth = self.auth.write().await;
        auth.authenticate_otp(&self.client, mobile, otp).await?;
        drop(auth);
        
        self.refresh_tokens().await?;
        Ok(())
    }

    /// Refresh authentication tokens
    pub async fn refresh_tokens(&self) -> Result<()> {
        let mut tokens = self.tokens.write().await;
        
        // Try to refresh existing token
        if let Some(ref refresh_token) = tokens.refresh_token
            && let Ok(new_tokens) = self
                .client
                .post("https://auth.jio.com/v3/oauth/refresh")
                .json(&serde_json::json!({
                    "refresh_token": refresh_token,
                    "client_id": "jio-web",
                    "grant_type": "refresh_token"
                }))
                .send()
                .await
            && new_tokens.status().is_success()
        {
            let json: JioTokenResponse = new_tokens.json().await?;
            tokens.access_token = Some(json.access_token);
            tokens.expires_at = Some(Utc::now() + chrono::Duration::seconds(json.expires_in as i64));
            return Ok(());
        }

        // Try to get new channel token from existing session
        let auth = self.auth.read().await;
        let headers = auth.get_headers().await;
        let header_map: HeaderMap = headers.into();
        
        let channel_resp: Response = self.client
            .get("https://www.jio.com/api/v1/channel/token")
            .headers(header_map)
            .send()
            .await?;

        if channel_resp.status().is_success() {
            if let Ok(json) = channel_resp.json::<serde_json::Value>().await
                && let Some(token) = json.get("token").and_then(|t| t.as_str())
            {
                tokens.channel_token = Some(token.to_string());
                tokens.expires_at = Some(Utc::now() + chrono::Duration::hours(2));
            }
            Ok(())
        } else {
            Err(anyhow!("Failed to get channel token: {}", channel_resp.status()))
        }
    }

    /// Get valid auth headers for requests
    pub async fn get_auth_headers(&self) -> AuthHeaders {
        let tokens = self.tokens.read().await;
        
        // Check if we need to refresh
        if tokens.should_refresh() {
            drop(tokens);
            if let Err(e) = self.refresh_tokens().await {
                tracing::warn!("Token refresh failed: {}", e);
            }
        }

        let tokens = self.tokens.read().await;
        if let Some(ref token) = tokens.access_token {
            return AuthHeaders::from_token(token);
        }
        AuthHeaders::default()
    }

    /// Fetch channels from JioTV service
    pub async fn fetch_channels(&self) -> Result<Vec<Channel>> {
        let headers = self.get_auth_headers().await;
        let header_map: HeaderMap = headers.into();
        
        let resp: Response = self.client
            .get("https://www.jio.com/api/v1/listings/channels")
            .headers(header_map)
            .send()
            .await?;

        if resp.status() == StatusCode::UNAUTHORIZED {
            // Try to refresh and retry
            self.refresh_tokens().await?;
            let headers = self.get_auth_headers().await;
            let header_map: HeaderMap = headers.into();
            
            // Retry with new headers
            let retry_resp: Response = self.client
                .get("https://www.jio.com/api/v1/listings/channels")
                .headers(header_map)
                .send()
                .await?;
            
            if !retry_resp.status().is_success() {
                return Err(anyhow!("Failed to fetch channels after refresh: {}", retry_resp.status()));
            }
            
            let json: serde_json::Value = retry_resp.json().await?;
            return self.parse_channels(json);
        }

        if !resp.status().is_success() {
            return Err(anyhow!("Failed to fetch channels: {}", resp.status()));
        }

        let json: serde_json::Value = resp.json().await?;
        self.parse_channels(json)
    }

    fn parse_channels(&self, json: serde_json::Value) -> Result<Vec<Channel>> {
        let mut channels = Vec::new();
        
        if let Some(categories) = json.get("categories").and_then(|c| c.as_array()) {
            for cat in categories {
                if let Some(channels_arr) = cat.get("channels").and_then(|c| c.as_array()) {
                    for ch in channels_arr {
                        if let Some(channel) = self.parse_channel(ch) {
                            channels.push(channel);
                        }
                    }
                }
            }
        }

        Ok(channels)
    }

    fn parse_channel(&self, ch: &serde_json::Value) -> Option<Channel> {
        let name = ch.get("name").and_then(|n| n.as_str())?.to_string();
        let stream_url = ch.get("stream_url").and_then(|s| s.as_str())?.to_string();
        let logo = ch.get("logo").and_then(|l| l.as_str())?.to_string();
        let country = ch.get("country").and_then(|c| c.as_str())?.to_string();
        let language = ch.get("language").and_then(|l| l.as_str())?.to_string();
        let category = ch.get("category").and_then(|c| c.as_str())?.to_string();

        Some(Channel {
            stream_url,
            name,
            tvg_id: String::new(),
            logo,
            country,
            language,
            category,
            extra_headers: Default::default(),
            source_name: "jio.tv".to_string(),
            is_external: false,
            is_fast: false,
            explicit_telugu: false,
            height: 0,
            quality_tag: String::new(),
            latency_ms: 0,
            watch_url: String::new(),
            is_master_m3u8: false,
            audio_tracks: Vec::new(),
            logo_url: None,
            group: None,
            is_drm: None,
            channel_number: None,
            epg_id: None,
            categories: None,
            quality: None,
            last_updated: None,
            verified_at: None,
            verification_error: None,
        })
    }

    /// Check if service is authenticated
    pub async fn is_authenticated(&self) -> bool {
        let auth = self.auth.read().await;
        auth.is_authenticated().await
    }

    /// Logout and clear session
    pub async fn logout(&self) -> Result<()> {
        let auth = self.auth.write().await;
        auth.clear_session().await?;
        let mut tokens = self.tokens.write().await;
        *tokens = JioTokens::default();
        Ok(())
    }
}

impl Default for JioTvService {
    fn default() -> Self {
        Self::new().unwrap_or_else(|_| Self::new().expect("Failed to create JioTvService"))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct JioTokenResponse {
    access_token: String,
    refresh_token: String,
    expires_in: u64,
    token_type: String,
}

/// Source for JioTV channels
pub struct JioTvSource {
    service: JioTvService,
    authenticated: Arc<Mutex<bool>>,
}

impl JioTvSource {
    pub fn new() -> Self {
        Self {
            service: JioTvService::new().unwrap_or_default(),
            authenticated: Arc::new(Mutex::new(false)),
        }
    }

    pub fn login_otp(&self, _mobile: &str, _otp: &str) -> Result<()> {
        // In a real implementation, this would spawn a task or use interior mutability
        // For now, we just set the flag
        let mut auth = self.authenticated.lock().map_err(|e| anyhow!("Lock error: {}", e))?;
        *auth = true;
        Ok(())
    }

    fn is_authenticated_sync(&self) -> bool {
        self.authenticated.lock().map(|g| *g).unwrap_or(false)
    }
}

impl Default for JioTvSource {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl ChannelSource for JioTvSource {
    fn id(&self) -> &str {
        "jio-tv"
    }

    fn name(&self) -> &str {
        "JioTV"
    }

    async fn fetch_channels(&self) -> Result<Vec<Channel>> {
        if !self.is_authenticated_sync() {
            return Err(anyhow!("Not authenticated with JioTV"));
        }
        self.service.fetch_channels().await
    }

    fn metadata(&self) -> crate::sources::SourceMeta {
        crate::sources::SourceMeta {
            id: "jio-tv".to_string(),
            name: "JioTV".to_string(),
            description: "Official JioTV streaming service with Indian TV channels".to_string(),
            url: "https://www.jio.com/tv".to_string(),
            category: crate::sources::SourceCategory::Authentication,
            region: Some("India".to_string()),
            language: Some("hin".to_string()),
            is_auth_required: true,
            update_interval_hours: 6,
            last_checked: None,
            channel_count: None,
            rating: None,
            tags: vec!["india".to_string(), "hindi".to_string(), "live-tv".to_string()],
        }
    }

    async fn is_alive(&self) -> bool {
        self.service.is_authenticated().await
    }
}

#[async_trait]
impl ChannelSource for JioTvService {
    fn id(&self) -> &str {
        "jio-service"
    }

    fn name(&self) -> &str {
        "JioTV Service"
    }

    async fn fetch_channels(&self) -> Result<Vec<Channel>> {
        if !self.is_authenticated().await {
            return Err(anyhow!("JioTV service not authenticated"));
        }
        self.fetch_channels().await
    }

    fn metadata(&self) -> crate::sources::SourceMeta {
        crate::sources::SourceMeta {
            id: "jio-service".to_string(),
            name: "JioTV Service".to_string(),
            description: "JioTV and JioCinema authenticated streaming".to_string(),
            url: "https://www.jio.com".to_string(),
            category: crate::sources::SourceCategory::Authentication,
            region: Some("India".to_string()),
            language: Some("hin".to_string()),
            is_auth_required: true,
            update_interval_hours: 12,
            last_checked: None,
            channel_count: None,
            rating: Some(4.5),
            tags: vec!["jio".to_string(), "india".to_string()],
        }
    }

    async fn is_alive(&self) -> bool {
        self.is_authenticated().await
    }
}