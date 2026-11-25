//! Authentication module for JioTV services
//!
//! Handles OTP-based authentication and token management

use anyhow::{anyhow, Result};
use reqwest::{Client, Response};
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

const JIO_AUTH_URL: &str = "https://auth.jio.com";

/// Authentication headers wrapper for making authenticated requests
#[derive(Debug, Clone, Default)]
pub struct AuthHeaders {
    pub headers: HashMap<String, String>,
}

impl AuthHeaders {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn from_token(token: &str) -> Self {
        let mut headers = HashMap::new();
        headers.insert("Authorization".to_string(), format!("Bearer {}", token));
        Self { headers }
    }

    pub fn with_cookies(cookies: &str) -> Self {
        let mut headers = HashMap::new();
        headers.insert("Cookie".to_string(), cookies.to_string());
        Self { headers }
    }

    pub fn into_headers(self) -> HeaderMap {
        let mut headers = HeaderMap::new();
        for (key, value) in self.headers {
            if let Ok(header_name) = HeaderName::try_from(key.to_lowercase().as_str())
                && let Ok(header_value) = HeaderValue::try_from(value)
            {
                headers.insert(header_name, header_value);
            }
        }
        headers
    }

    pub fn insert(&mut self, key: &str, value: &str) {
        self.headers.insert(key.to_string(), value.to_string());
    }
}

impl From<AuthHeaders> for HeaderMap {
    fn from(auth: AuthHeaders) -> Self {
        auth.into_headers()
    }
}

impl From<&AuthHeaders> for HeaderMap {
    fn from(auth: &AuthHeaders) -> Self {
        auth.clone().into_headers()
    }
}

/// Jio authentication session state
#[derive(Debug, Clone)]
pub struct JioAuth {
    mobile: Arc<tokio::sync::Mutex<Option<String>>>,
    session_id: Arc<tokio::sync::Mutex<Option<String>>>,
    cookies: Arc<tokio::sync::Mutex<Option<String>>>,
    otp_verified: Arc<tokio::sync::Mutex<bool>>,
    login_time: Arc<tokio::sync::Mutex<Option<chrono::DateTime<chrono::Utc>>>>,
    client_state: Arc<tokio::sync::Mutex<Option<String>>>,
}

impl Default for JioAuth {
    fn default() -> Self {
        Self::new()
    }
}

impl JioAuth {
    pub fn new() -> Self {
        Self {
            mobile: Arc::new(tokio::sync::Mutex::new(None)),
            session_id: Arc::new(tokio::sync::Mutex::new(None)),
            cookies: Arc::new(tokio::sync::Mutex::new(None)),
            otp_verified: Arc::new(tokio::sync::Mutex::new(false)),
            login_time: Arc::new(tokio::sync::Mutex::new(None)),
            client_state: Arc::new(tokio::sync::Mutex::new(None)),
        }
    }

    /// Send OTP to mobile number
    pub async fn send_otp(&self, client: &Client, mobile: &str) -> Result<String> {
        let resp: Response = client
            .post(format!("{}/v2/mnp/otp/generate", JIO_AUTH_URL))
            .json(&serde_json::json!({
                "mobile": mobile,
                "client_id": "jio-web",
                "client_secret": "1b8c8f3d-4a5d-4e6a-8c9d-0e1f2a3b4c5d"
            }))
            .timeout(Duration::from_secs(15))
            .send()
            .await?;

        let status = resp.status();
        if status.is_success() {
            let json: serde_json::Value = resp.json().await?;
            if let Some(session_id) = json.get("sessionId").and_then(|s| s.as_str()) {
                return Ok(session_id.to_string());
            }
        }

        Err(anyhow!("Failed to send OTP: HTTP {}", status))
    }

    /// Verify OTP
    pub async fn verify_otp(
        &self,
        client: &Client,
        session_id: &str,
        otp: &str,
    ) -> Result<()> {
        let resp: Response = client
            .post(format!("{}/v2/mnp/otp/verify", JIO_AUTH_URL))
            .json(&serde_json::json!({
                "sessionId": session_id,
                "otp": otp,
                "client_id": "jio-web"
            }))
            .timeout(Duration::from_secs(15))
            .send()
            .await?;

        let status = resp.status();
        if !status.is_success() {
            return Err(anyhow!("OTP verification failed: HTTP {}", status));
        }

        let cookies: Vec<String> = resp.cookies().map(|c| format!("{}={}", c.name(), c.value())).collect();
        if !cookies.is_empty() {
            // Store session state
            let mut state = self.client_state.lock().await;
            *state = Some("authenticated".to_string());
        }

        Ok(())
    }

    /// Full OTP authentication flow
    pub async fn authenticate_otp(
        &self,
        client: &Client,
        mobile: &str,
        otp: &str,
    ) -> Result<()> {
        let session_id = self.send_otp(client, mobile).await?;
        self.verify_otp(client, &session_id, otp).await?;

        // Get auth headers with session cookies
        let resp: Response = client
            .get(format!("{}/v3/oauth/token", JIO_AUTH_URL))
            .header("Cookie", format!("sessionId={}; JioWebLogin=1", session_id))
            .header("Referer", "https://www.jio.com/")
            .timeout(Duration::from_secs(10))
            .send()
            .await?;

        if resp.status().is_success() {
            // Update session state
            let mut mob = self.mobile.lock().await;
            *mob = Some(mobile.to_string());
            let mut sid = self.session_id.lock().await;
            *sid = Some(session_id);
            let mut verified = self.otp_verified.lock().await;
            *verified = true;
            let mut login = self.login_time.lock().await;
            *login = Some(chrono::Utc::now());
        }

        Ok(())
    }

    /// Get current auth headers for requests
    pub async fn get_headers(&self) -> AuthHeaders {
        let mut headers = AuthHeaders::new();

        if let Some(ref sid) = *self.session_id.lock().await {
            headers.insert("Cookie", &format!("sessionId={}", sid));
        }

        if let Some(ref c) = *self.cookies.lock().await {
            headers.insert("Cookie", c);
        }

        headers.insert("User-Agent", "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36");
        headers
    }

    /// Check if currently authenticated
    pub async fn is_authenticated(&self) -> bool {
        let verified = *self.otp_verified.lock().await;
        let has_session = self.session_id.lock().await.is_some();
        verified && has_session
    }

    /// Clear session
    pub async fn clear_session(&self) -> Result<()> {
        let mut mob = self.mobile.lock().await;
        *mob = None;
        let mut sid = self.session_id.lock().await;
        *sid = None;
        let mut cookies = self.cookies.lock().await;
        *cookies = None;
        let mut verified = self.otp_verified.lock().await;
        *verified = false;
        let mut login = self.login_time.lock().await;
        *login = None;
        Ok(())
    }
}