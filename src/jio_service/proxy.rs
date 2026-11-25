//! Stream Proxy Module for JioTV
//!
//! Handles stream proxying with per-request authentication headers

use anyhow::Result;
use reqwest::header::HeaderMap;
use reqwest::{Client, Response};
use std::time::Duration;

const STREAM_TIMEOUT: Duration = Duration::from_secs(10);

/// JioTV Stream Proxy for authenticated stream access
pub struct JioStreamProxy {
    client: Client,
}

impl JioStreamProxy {
    pub fn new(client: Client) -> Self {
        Self { client }
    }

    /// Fetch stream with authentication headers
    pub async fn fetch_stream(&self, url: &str, headers: HeaderMap) -> Result<Vec<u8>> {
        let bytes = self
            .client
            .get(url)
            .headers(headers)
            .timeout(STREAM_TIMEOUT)
            .send()
            .await?
            .bytes()
            .await?;

        Ok(bytes.to_vec())
    }

    /// Get stream response with authentication headers
    pub async fn stream_response(&self, url: &str, headers: HeaderMap) -> Result<Response> {
        let resp = self
            .client
            .get(url)
            .headers(headers)
            .timeout(STREAM_TIMEOUT)
            .send()
            .await?;

        Ok(resp)
    }

    /// Check if stream is accessible with given headers
    pub async fn is_stream_alive(&self, url: &str, headers: HeaderMap) -> bool {
        self.stream_response(url, headers)
            .await
            .map(|r| r.status().is_success())
            .unwrap_or(false)
    }
}
