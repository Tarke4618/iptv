package main

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode"
)

const (
	baseURL             = "https://eja.tv/"
	iptvOrgChannelsAPI  = "https://iptv-org.github.io/api/channels.json"
	iptvOrgLogosAPI     = "https://iptv-org.github.io/api/logos.json"
	healthCacheFilePath = "cache/health_cache.json"
	defaultUA           = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

type Config struct {
	MaxPagesPerQuery              int              `json:"max_pages_per_query"`
	MaxVerifySeconds              int              `json:"max_verify_seconds"`
	MaxWorkers                    int              `json:"max_workers"`
	VerifyStreams                 bool             `json:"verify_streams"`
	DeduplicateChannels           bool             `json:"deduplicate_channels"`
	TagStreamInfo                 bool             `json:"tag_stream_info"`
	SkipRawIPStreams              bool             `json:"skip_raw_ip_streams"`
	OnlyMatchingExternalLanguages bool             `json:"only_matching_external_languages"`
	EPGURLs                       []string         `json:"epg_urls"`
	ExternalM3USources            []ExternalSource `json:"external_m3u_sources"`
	Countries                     []string         `json:"countries"`
	Languages                     []string         `json:"languages"`
	Categories                    []string         `json:"categories"`
	TeluguHubSearches             []string         `json:"telugu_hub_searches"`
	CustomSearches                []string         `json:"custom_searches"`
}

type ExternalSource struct {
	Name            string `json:"name"`
	URL             string `json:"url"`
	TeluguOnly      bool   `json:"telugu_only"`
	AlwaysKeep      bool   `json:"always_keep"`
	IsFast          bool   `json:"is_fast"`
	DefaultCountry  string `json:"default_country"`
	DefaultLanguage string `json:"default_language"`
}

type Channel struct {
	Key            string
	Name           string
	TvgID          string
	StreamURL      string
	WatchURL       string
	Logo           string
	Country        string
	Language       string
	Category       string
	ExtraHeaders   map[string]string
	SourceName     string
	IsExternal     bool
	IsFast         bool
	ExplicitTelugu bool
	Height         int
	QualityTag     string
	LatencyMs      int
}

type CatalogEntry struct {
	ID          string
	Name        string
	Logo        string
	CountryCode string
	Category    string
}

type HealthRecord struct {
	Fails int   `json:"fails"`
	TS    int64 `json:"ts"`
}

type HealthCache struct {
	mu   sync.Mutex
	path string
	now  int64
	data map[string]HealthRecord
}

type SourceStat struct {
	Source   string `json:"source"`
	Alive    int    `json:"alive"`
	Imported int    `json:"imported"`
}

var (
	streamClient = &http.Client{
		Transport: &http.Transport{
			DialContext: (&net.Dialer{
				Timeout:   2500 * time.Millisecond,
				KeepAlive: 30 * time.Second,
			}).DialContext,
			TLSHandshakeTimeout:   2500 * time.Millisecond,
			ResponseHeaderTimeout: 3500 * time.Millisecond,
			MaxIdleConns:          512,
			MaxIdleConnsPerHost:   32,
			IdleConnTimeout:       30 * time.Second,
			ForceAttemptHTTP2:     true,
		},
		Timeout: 6 * time.Second,
	}

	pageClient = &http.Client{
		Transport: &http.Transport{
			DialContext: (&net.Dialer{
				Timeout:   5 * time.Second,
				KeepAlive: 30 * time.Second,
			}).DialContext,
			TLSHandshakeTimeout:   5 * time.Second,
			ResponseHeaderTimeout: 15 * time.Second,
			MaxIdleConns:          128,
			MaxIdleConnsPerHost:   24,
			ForceAttemptHTTP2:     true,
		},
		Timeout: 25 * time.Second,
	}

	reHTMLTag      = regexp.MustCompile(`<[^>]*>`)
	reMultiSpace   = regexp.MustCompile(`\s+`)
	reEjaPrefix    = regexp.MustCompile(`(?i)^eja\.tv\s*[-|:–—]+\s*|\s*[-|:–—]+\s*eja\.tv$`)
	reHexHash      = regexp.MustCompile(`^[a-f0-9]{12,}$`)
	reGenericName  = regexp.MustCompile(`^(channel|stream|live|index|master|playlist|tracks)\s*\d*$`)
	reBracketNote  = regexp.MustCompile(`\[[^\]]*\]|\([^\)]*\)`)
	reStripWords   = regexp.MustCompile(`\b(hd|fhd|uhd|sd|4k|tv|live|channel|india)\b`)
	reNormStrip    = regexp.MustCompile(`[^a-z0-9\x{0900}-\x{0d7f}]+`)
	reSafeFile     = regexp.MustCompile(`[^a-zA-Z0-9_-]+`)
	reTvgIDClean   = regexp.MustCompile(`[^a-zA-Z0-9]+`)
	reQualityWord  = regexp.MustCompile(`\b(HD|News|TV|Music|Musix|Movies|Plus|Cinema|Cinemalu)\b`)
	reRawIPPort    = regexp.MustCompile(`(?i)^https?://\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?/`)
	reM3U8Abs      = regexp.MustCompile(`(?i)https?://[^\s"'<>\\()]+?\.m3u8[^\s"'<>\\()]*`)
	reM3U8ProtoRel = regexp.MustCompile(`(?i)(?:^|[\s"'=(])(//[a-zA-Z0-9.-]+/[^\s"'<>\\()]+?\.m3u8[^\s"'<>\\()]*)`)
	reAtob         = regexp.MustCompile(`atob\(\s*["']([A-Za-z0-9+/=]{16,})["']\s*\)`)
	reAnchorTag    = regexp.MustCompile(`(?is)<a\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>`)
	reImgSrc       = regexp.MustCompile(`(?i)<img\b[^>]*src=["']([^"']+)["']`)
	reAttrTitle    = regexp.MustCompile(`(?i)(?:title|alt|aria-label|data-title|data-name)=["']([^"']{2,80})["']`)
	reHeadingTag   = regexp.MustCompile(`(?is)<(?:h1|h2|h3|title)[^>]*>(.*?)</(?:h1|h2|h3|title)>`)
	reExtInfID     = regexp.MustCompile(`(?i)tvg-id="([^"]*)"`)
	reExtInfLogo   = regexp.MustCompile(`(?i)tvg-logo="([^"]*)"`)
	reExtInfLang   = regexp.MustCompile(`(?i)tvg-language="([^"]*)"`)
	reExtInfCntry  = regexp.MustCompile(`(?i)tvg-country="([^"]*)"`)
	reExtInfGrp    = regexp.MustCompile(`(?i)group-title="([^"]*)"`)
	reExtInfUA     = regexp.MustCompile(`(?i)user-agent="([^"]*)"`)
	reExtInfRef    = regexp.MustCompile(`(?i)referer="([^"]*)"`)
	reResolution   = regexp.MustCompile(`RESOLUTION=\d+x(\d+)`)
	reBandwidth    = regexp.MustCompile(`BANDWIDTH=(\d+)`)
	reNonAlpha     = regexp.MustCompile(`[^a-z]+`)

	reStrictTelugu = regexp.MustCompile(
		`(?i)(?:` +
			`\btelugu\b|తెలుగు|` +
			`\betv(?:\s+(?:telugu|andhra|telangana|cinema|plus|abhiruchi|life|bal\s*bharat|news))?\b|` +
			`\bgemini(?:\s+(?:tv|movies|music|comedy|life))?\b|` +
			`\b(?:star\s+)?maa(?:\s+(?:tv|movies|music|gold))?\b|` +
			`\bzee\s+(?:telugu|cinemalu)\b|` +
			`\btv9\s*telugu\b|\bntv\s*telugu\b|\btv5\s*(?:news|telugu)\b|` +
			`\bsakshi(?:\s*tv)?\b|\babn\s*andhra(?:\s*jyothi)?\b|` +
			`\bv6\s*(?:news|telugu)?\b|\b10\s*tv\b|\bhmtv\b|\bt\s*news\b|` +
			`\bmahaa(?:\s+(?:news|max|bhakti))?\b|` +
			`\braj\s+(?:news|musix)\s*telugu\b|\bvissa(?:\s*tv)?\b|\bvanitha(?:\s*tv)?\b|` +
			`\bprime\s*9(?:\s*news)?\b|\b99\s*tv\b|\b6\s*tv\b|\bcvr(?:\s+(?:news|health|spiritual|english))?\b|` +
			`\bsvbc\b|\bbhakti\s*tv\b|\bsubhagruha\b|\bcalvary\s*tv\b|\baradana\b|` +
			`\bkushi\s*tv\b|\btelugu\s*one\b|\bsitara\s*tv\b|\bmango\s*(?:news|music|mobile)\b|` +
			`\bdd\s+(?:yadagiri|saptagiri)\b|\binews\b|\bbig\s*tv\b|\bbrk\s*news\b|\bq\s*news\b|` +
			`\bntv\b|\btv9\b` +
			`)`,
	)

	reNonTeluguExclude = regexp.MustCompile(
		`(?i)\b(?:` +
			`malayalam|bangla|bengali|tamil|kannada|marathi|gujarati|punjabi|odia|oriya|assamese|bhojpuri|urdu|` +
			`arabic|persian|farsi|kurdish|turkish|russian|spanish|french|german|korean|japanese|chinese|` +
			`kairali|asianet|manorama|mathrubhumi|mazhavil|surya\s*tv|flowers\s*tv|amrita\s*tv|janam\s*tv|` +
			`media\s*one|reporter\s*tv|twentyfour|24\s*news|jeevan\s*tv|kaumudy|safari\s*tv|` +
			`sun\s*tv|vijay\s*tv|kalaignar|jaya\s*tv|polimer|puthiya|thanthi|captain\s*tv|` +
			`somoy|jamuna|ekattor|independent\s*tv|channel\s*i|ntv\s*bangla|atn\s*bangla|rtv\s*bangla|maasranga|` +
			`irib|tehran|kurdsat|fashiontv|fashion\s*tv|son\s*of\s*god|michigan|al\s*jazeera|trt|dw\s*news|france\s*24` +
			`)\b`,
	)

	noiseWords = map[string]bool{
		"eja": true, "eja.tv": true, "home": true, "tv": true, "live": true, "live tv": true,
		"channel": true, "channels": true, "stream": true, "streams": true, "watch": true,
		"play": true, "player": true, "video": true, "hd": true, "sd": true, "fhd": true,
		"next": true, "previous": true, "prev": true, "more": true, "all": true,
		"search": true, "filter": true, "menu": true, "sign languages": true,
		"sign language": true, "unknown": true, "untitled": true, "index": true,
		"playlist": true, "master": true, "chunklist": true, "manifest": true,
	}

	genericURLParts = map[string]bool{
		"hls": true, "live": true, "stream": true, "streams": true, "index": true, "playlist": true,
		"master": true, "chunklist": true, "manifest": true, "tv": true, "iptv": true, "out": true,
		"v1": true, "v2": true, "v3": true, "cdn": true, "edge": true, "pub": true, "app": true,
		"smil": true, "ngrp": true, "amlst": true, "disk1": true, "liveevent": true, "bpk-tv": true,
		"tracks-v1a1": true,
	}

	filterKeys = map[string]bool{
		"country": true, "language": true, "category": true, "search": true,
		"offset": true, "limit": true, "page": true, "sort": true, "order": true, "level": true,
	}

	staticSlugs = map[string]bool{
		"about": true, "help": true, "privacy": true, "terms": true, "contact": true,
		"faq": true, "login": true, "register": true, "dmca": true,
	}

	staticExts = []string{".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp"}

	otherIndianLangs = map[string]bool{
		"malayalam": true, "mal": true, "bengali": true, "ben": true, "bangla": true,
		"tamil": true, "tam": true, "hindi": true, "hin": true, "kannada": true, "kan": true,
		"marathi": true, "mar": true, "punjabi": true, "pan": true, "gujarati": true, "guj": true,
		"urdu": true, "urd": true, "odia": true, "ori": true,
	}

	countryAliases = map[string][2]string{
		"gb": {"uk", "United Kingdom"}, "uk": {"uk", "United Kingdom"}, "united kingdom": {"uk", "United Kingdom"},
		"in": {"in", "India"}, "india": {"in", "India"},
		"us": {"us", "United States"}, "usa": {"us", "United States"}, "united states": {"us", "United States"},
		"ca": {"ca", "Canada"}, "canada": {"ca", "Canada"},
		"au": {"au", "Australia"}, "australia": {"au", "Australia"},
	}

	langAliases = map[string][2]string{
		"english": {"eng", "English"}, "hindi": {"hin", "Hindi"}, "telugu": {"tel", "Telugu"},
		"tamil": {"tam", "Tamil"}, "spanish": {"spa", "Spanish"}, "french": {"fra", "French"},
		"german": {"deu", "German"}, "arabic": {"ara", "Arabic"}, "russian": {"rus", "Russian"},
		"portuguese": {"por", "Portuguese"}, "italian": {"ita", "Italian"}, "japanese": {"jpn", "Japanese"},
		"korean": {"kor", "Korean"}, "chinese": {"zho", "Chinese"}, "bengali": {"ben", "Bengali"},
		"marathi": {"mar", "Marathi"}, "gujarati": {"guj", "Gujarati"}, "punjabi": {"pan", "Punjabi"},
		"malayalam": {"mal", "Malayalam"}, "kannada": {"kan", "Kannada"}, "urdu": {"urd", "Urdu"},
	}
)

func newHealthCache(path string) *HealthCache {
	hc := &HealthCache{
		path: path,
		now:  time.Now().Unix(),
		data: make(map[string]HealthRecord),
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return hc
	}
	var parsed map[string]HealthRecord
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return hc
	}
	cutoff := hc.now - (14 * 86400)
	for k, v := range parsed {
		if v.TS >= cutoff {
			hc.data[k] = v
		}
	}
	return hc
}

func (hc *HealthCache) shouldSkip(u string) bool {
	hc.mu.Lock()
	defer hc.mu.Unlock()
	rec, ok := hc.data[u]
	if !ok {
		return false
	}
	return rec.Fails >= 3 && (hc.now-rec.TS) < (72*3600)
}

func (hc *HealthCache) markAlive(u string) {
	hc.mu.Lock()
	delete(hc.data, u)
	hc.mu.Unlock()
}

func (hc *HealthCache) markDead(u string) {
	hc.mu.Lock()
	rec := hc.data[u]
	rec.Fails++
	if rec.Fails > 10 {
		rec.Fails = 10
	}
	rec.TS = hc.now
	hc.data[u] = rec
	hc.mu.Unlock()
}

func (hc *HealthCache) save() {
	hc.mu.Lock()
	defer hc.mu.Unlock()
	_ = os.MkdirAll(filepath.Dir(hc.path), 0o755)
	b, err := json.Marshal(hc.data)
	if err == nil {
		_ = os.WriteFile(hc.path, b, 0o644)
	}
}

func titleCase(s string) string {
	words := strings.Fields(s)
	for i, w := range words {
		runes := []rune(strings.ToLower(w))
		if len(runes) > 0 {
			runes[0] = unicode.ToUpper(runes[0])
		}
		words[i] = string(runes)
	}
	return strings.Join(words, " ")
}

func safeFilename(name string) string {
	clean := reSafeFile.ReplaceAllString(strings.TrimSpace(name), "_")
	clean = strings.ToLower(strings.Trim(clean, "_"))
	if clean == "" {
		return "general"
	}
	return clean
}

func cleanText(text string) string {
	t := reHTMLTag.ReplaceAllString(text, " ")
	t = strings.NewReplacer("<", " ", ">", " ", ",", " ", "&amp;", "&", "&#39;", "'", "&quot;", " ").Replace(t)
	t = strings.TrimSpace(reMultiSpace.ReplaceAllString(t, " "))
	t = reEjaPrefix.ReplaceAllString(t, "")
	return strings.Trim(t, " -|:–—/")
}

func validName(name string, noise map[string]bool) bool {
	if name == "" {
		return false
	}
	low := strings.ToLower(strings.TrimSpace(name))
	if len(low) < 2 || len(low) > 80 {
		return false
	}
	if noise[low] {
		return false
	}
	if strings.ContainsAny(low, "<>{}\\") || strings.Contains(low, "//") {
		return false
	}
	if strings.HasPrefix(low, "http:") || strings.HasPrefix(low, "https:") || strings.HasPrefix(low, "www.") {
		return false
	}
	allDigit := true
	for _, r := range low {
		if !unicode.IsDigit(r) {
			allDigit = false
			break
		}
	}
	if allDigit {
		return false
	}
	if reHexHash.MatchString(low) || reGenericName.MatchString(low) {
		return false
	}
	return true
}

func normalizeChannelKey(name string) string {
	low := strings.ToLower(name)
	low = reBracketNote.ReplaceAllString(low, "")
	low = reStripWords.ReplaceAllString(low, "")
	key := reNormStrip.ReplaceAllString(low, "")
	if key != "" {
		return key
	}
	return reNormStrip.ReplaceAllString(strings.ToLower(name), "")
}

func nameQualityScore(name string) int {
	if name == "" {
		return 0
	}
	score := 10
	if strings.Contains(name, " ") {
		score += 20
	}
	hasUpper, hasLower := false, false
	for _, r := range name {
		if unicode.IsUpper(r) {
			hasUpper = true
		} else if unicode.IsLower(r) {
			hasLower = true
		}
	}
	if hasUpper && hasLower {
		score += 10
	}
	if reQualityWord.MatchString(name) {
		score += 10
	}
	return score
}

func isStrictlyTelugu(name, lang, group, country string, explicitSource bool) bool {
	fullBlob := name + " | " + lang + " | " + group + " | " + country
	nameGroup := name + " | " + group

	if reNonTeluguExclude.MatchString(nameGroup) {
		return false
	}

	hasTeluguToken := false
	hasOtherIndian := false
	for _, tok := range reNonAlpha.Split(strings.ToLower(lang), -1) {
		if tok == "telugu" || tok == "tel" {
			hasTeluguToken = true
		} else if otherIndianLangs[tok] {
			hasOtherIndian = true
		}
	}
	if hasOtherIndian && !hasTeluguToken {
		return false
	}
	if explicitSource || hasTeluguToken {
		return true
	}

	cLow := strings.ToLower(strings.TrimSpace(country))
	if cLow != "" && cLow != "india" && cLow != "in" {
		return false
	}
	return reStrictTelugu.MatchString(fullBlob)
}

func isSportsChannel(ch *Channel) bool {
	blob := strings.ToLower(" " + ch.Name + " " + ch.Category + " " + ch.SourceName + " ")
	for _, kw := range []string{"sport", "cricket", "football", "soccer", "tennis", "f1", "racing", "boxing", "ufc", "wwe", "espn", "dazn", "beinsport"} {
		if strings.Contains(blob, kw) {
			return true
		}
	}
	return false
}

func classifyTeluguGroup(ch *Channel) string {
	blob := strings.ToLower(" " + ch.Name + " " + ch.Category + " ")
	for _, k := range []string{"news", "tv9", "ntv", "tv5", "sakshi", "abn", "v6", "10tv", "hmtv", "t news", "mahaa", "prime9", "99tv", "6tv", "studio n", "raj news", "big tv", "brk", "q news", "inews", "mango news"} {
		if strings.Contains(blob, k) {
			return "Telugu - News"
		}
	}
	for _, k := range []string{"movie", "cinema", "cinemalu", "gold", "sitara"} {
		if strings.Contains(blob, k) {
			return "Telugu - Movies"
		}
	}
	for _, k := range []string{"music", "musix", "beats", "raj musix"} {
		if strings.Contains(blob, k) {
			return "Telugu - Music"
		}
	}
	for _, k := range []string{"svbc", "bhakti", "subhagruha", "calvary", "aradana", "devotional", "hindu", "christian", "spiritual"} {
		if strings.Contains(blob, k) {
			return "Telugu - Devotional"
		}
	}
	for _, k := range []string{"kushi", "bal bharat", "kids"} {
		if strings.Contains(blob, k) {
			return "Telugu - Kids"
		}
	}
	return "Telugu - Entertainment"
}

func splitPipeHeaders(raw string) (string, map[string]string) {
	hdrs := make(map[string]string)
	idx := strings.Index(raw, "|")
	if idx == -1 {
		return strings.TrimSpace(raw), hdrs
	}
	base := strings.TrimSpace(raw[:idx])
	pipePart := raw[idx+1:]
	for _, pair := range strings.FieldsFunc(pipePart, func(r rune) bool { return r == '&' || r == '|' }) {
		eq := strings.Index(pair, "=")
		if eq == -1 {
			continue
		}
		k := strings.ToLower(strings.TrimSpace(pair[:eq]))
		v, err := url.QueryUnescape(strings.TrimSpace(pair[eq+1:]))
		if err != nil {
			v = strings.TrimSpace(pair[eq+1:])
		}
		v = strings.Trim(v, `"'`)
		if v == "" {
			continue
		}
		switch k {
		case "user-agent", "http-user-agent":
			hdrs["User-Agent"] = v
		case "referer", "referrer", "http-referrer", "http-referer":
			hdrs["Referer"] = v
		case "origin":
			hdrs["Origin"] = v
		case "cookie":
			hdrs["Cookie"] = v
		}
	}
	return base, hdrs
}

func isUsableHLSURL(u string, skipRawIP bool) bool {
	low := strings.ToLower(u)
	if !strings.HasPrefix(low, "http://") && !strings.HasPrefix(low, "https://") {
		return false
	}
	pathPart := strings.SplitN(low, "?", 2)[0]
	if strings.Contains(pathPart, ".mpd") {
		return false
	}
	for _, bad := range []string{"youtube.com/", "youtu.be/", "twitch.tv/", "dailymotion.com/"} {
		if strings.Contains(low, bad) {
			return false
		}
	}
	if skipRawIP && reRawIPPort.MatchString(u) {
		return false
	}
	return true
}

func humanizeURL(streamURL string) string {
	parsed, err := url.Parse(streamURL)
	if err != nil {
		return ""
	}
	parts := strings.Split(parsed.Path, "/")
	for i := len(parts) - 1; i >= 0; i-- {
		p := parts[i]
		if p == "" {
			continue
		}
		stem := p
		for _, ext := range []string{".m3u8", ".m3u", ".mpd", ".ts", ".smil", ".sdp"} {
			if strings.HasSuffix(strings.ToLower(stem), ext) {
				stem = stem[:len(stem)-len(ext)]
				break
			}
		}
		low := strings.ToLower(stem)
		if genericURLParts[low] || len(stem) < 2 || reHexHash.MatchString(low) {
			continue
		}
		word := strings.TrimSpace(strings.NewReplacer("_", " ", "-", " ").Replace(stem))
		if len(word) >= 2 {
			if len(word) <= 4 {
				return strings.ToUpper(word)
			}
			return titleCase(word)
		}
	}
	return ""
}

func resolveURL(base, rel string) string {
	bu, err := url.Parse(base)
	if err != nil {
		return rel
	}
	ru, err := url.Parse(rel)
	if err != nil {
		return rel
	}
	return bu.ResolveReference(ru).String()
}

func cloneHeaders(src map[string]string) map[string]string {
	dst := make(map[string]string, len(src))
	for k, v := range src {
		dst[k] = v
	}
	return dst
}

func loadIPTVOrgCatalog() map[string]CatalogEntry {
	catalog := make(map[string]CatalogEntry)
	type rawCh struct {
		ID         string   `json:"id"`
		Name       string   `json:"name"`
		AltNames   []string `json:"alt_names"`
		Country    string   `json:"country"`
		Categories []string `json:"categories"`
		Closed     *string  `json:"closed"`
	}
	type rawLogo struct {
		Channel string `json:"channel"`
		URL     string `json:"url"`
		Format  string `json:"format"`
	}

	var chBytes, lgBytes []byte
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		req, _ := http.NewRequest("GET", iptvOrgChannelsAPI, nil)
		req.Header.Set("User-Agent", defaultUA)
		resp, err := pageClient.Do(req)
		if err == nil && resp.StatusCode == 200 {
			chBytes, _ = io.ReadAll(resp.Body)
			resp.Body.Close()
		}
	}()
	go func() {
		defer wg.Done()
		req, _ := http.NewRequest("GET", iptvOrgLogosAPI, nil)
		req.Header.Set("User-Agent", defaultUA)
		resp, err := pageClient.Do(req)
		if err == nil && resp.StatusCode == 200 {
			lgBytes, _ = io.ReadAll(resp.Body)
			resp.Body.Close()
		}
	}()
	wg.Wait()

	logoMap := make(map[string]string)
	if len(lgBytes) > 0 {
		var logos []rawLogo
		if json.Unmarshal(lgBytes, &logos) == nil {
			for _, lg := range logos {
				if lg.Channel == "" || lg.URL == "" {
					continue
				}
				fmtUp := strings.ToUpper(lg.Format)
				existing, exists := logoMap[lg.Channel]
				if !exists || ((fmtUp == "PNG" || fmtUp == "WEBP" || fmtUp == "JPEG") && strings.HasSuffix(strings.ToLower(existing), ".svg")) {
					logoMap[lg.Channel] = lg.URL
				}
			}
		}
	}

	if len(chBytes) > 0 {
		var channels []rawCh
		if json.Unmarshal(chBytes, &channels) == nil {
			for _, ch := range channels {
				if ch.ID == "" || ch.Name == "" || (ch.Closed != nil && *ch.Closed != "") {
					continue
				}
				cc := strings.ToUpper(ch.Country)
				cat := ""
				if len(ch.Categories) > 0 {
					cat = titleCase(ch.Categories[0])
				}
				entry := CatalogEntry{
					ID:          ch.ID,
					Name:        ch.Name,
					Logo:        logoMap[ch.ID],
					CountryCode: cc,
					Category:    cat,
				}
				names := append([]string{ch.Name}, ch.AltNames...)
				for _, nm := range names {
					k := normalizeChannelKey(nm)
					if k == "" {
						continue
					}
					if prev, ok := catalog[k]; !ok || (cc == "IN" && prev.CountryCode != "IN") {
						catalog[k] = entry
					}
				}
			}
		}
	}
	return catalog
}

func enrichChannel(ch *Channel, catalog map[string]CatalogEntry) {
	if len(catalog) == 0 {
		return
	}
	meta, ok := catalog[normalizeChannelKey(ch.Name)]
	if !ok {
		return
	}
	if ch.TvgID == "" {
		ch.TvgID = meta.ID
	}
	if ch.Logo == "" && meta.Logo != "" {
		ch.Logo = meta.Logo
	}
	if ch.Category == "" && meta.Category != "" {
		ch.Category = meta.Category
	}
}

func extractM3U8FromText(text string) []string {
	seen := make(map[string]bool)
	var out []string
	add := func(u string) {
		u = strings.TrimSpace(strings.ReplaceAll(u, `\/`, "/"))
		if u != "" && !seen[u] {
			seen[u] = true
			out = append(out, u)
		}
	}
	for _, m := range reM3U8Abs.FindAllString(text, -1) {
		add(m)
	}
	for _, sm := range reM3U8ProtoRel.FindAllStringSubmatch(text, -1) {
		if len(sm) > 1 {
			add("https:" + sm[1])
		}
	}
	for _, sm := range reAtob.FindAllStringSubmatch(text, -1) {
		if len(sm) > 1 {
			if dec, err := base64.StdEncoding.DecodeString(sm[1]); err == nil {
				s := strings.TrimSpace(string(dec))
				if strings.HasPrefix(s, "http") && strings.Contains(strings.ToLower(s), ".m3u8") {
					add(s)
				}
			}
		}
	}
	return out
}

func isFilterURL(rawURL string) bool {
	u, err := url.Parse(rawURL)
	if err != nil {
		return true
	}
	path := strings.ToLower(strings.Trim(u.Path, "/"))
	if staticSlugs[path] {
		return true
	}
	for _, ext := range staticExts {
		if strings.HasSuffix(path, ext) {
			return true
		}
	}
	q := u.Query()
	if path == "" && len(q) == 0 {
		return true
	}
	if path != "" {
		return false
	}
	for k := range q {
		if !filterKeys[strings.ToLower(k)] {
			return false
		}
	}
	return true
}

func discoverEjaNoise() map[string]bool {
	noise := make(map[string]bool, len(noiseWords)+64)
	for k := range noiseWords {
		noise[k] = true
	}
	for k, v := range countryAliases {
		noise[k] = true
		noise[strings.ToLower(v[0])] = true
		noise[strings.ToLower(v[1])] = true
	}
	for k, v := range langAliases {
		noise[k] = true
		noise[strings.ToLower(v[0])] = true
		noise[strings.ToLower(v[1])] = true
	}
	return noise
}

func crawlEjaPages(noise map[string]bool, maxPages int, country, language, category, search string) []*Channel {
	items := make(map[string]*Channel)
	offset, page, usePage, count := 0, 1, false, 0

	for maxPages <= 0 || count < maxPages {
		q := url.Values{}
		if country != "" {
			q.Set("country", country)
		}
		if language != "" {
			q.Set("language", language)
		}
		if category != "" {
			q.Set("category", category)
		}
		if search != "" {
			q.Set("search", search)
		}
		if usePage && page > 1 {
			q.Set("page", strconv.Itoa(page))
		} else if !usePage && offset > 0 {
			q.Set("offset", strconv.Itoa(offset))
		}

		reqURL := baseURL
		if encoded := q.Encode(); encoded != "" {
			reqURL += "?" + encoded
		}

		req, _ := http.NewRequest("GET", reqURL, nil)
		req.Header.Set("User-Agent", defaultUA)
		resp, err := pageClient.Do(req)
		if err != nil || resp.StatusCode != 200 {
			if resp != nil {
				resp.Body.Close()
			}
			break
		}
		bodyBytes, _ := io.ReadAll(io.LimitReader(resp.Body, 2*1024*1024))
		finalURL := resp.Request.URL.String()
		resp.Body.Close()
		htmlText := string(bodyBytes)

		added := 0
		for _, m3u8 := range extractM3U8FromText(htmlText) {
			if _, ok := items[m3u8]; ok {
				continue
			}
			items[m3u8] = &Channel{
				Key:          m3u8,
				Name:         humanizeURL(m3u8),
				StreamURL:    m3u8,
				Category:     category,
				ExtraHeaders: map[string]string{},
				SourceName:   "eja.tv",
			}
			added++
		}

		offsetsMap := make(map[int]bool)
		pagesMap := make(map[int]bool)

		for _, m := range reAnchorTag.FindAllStringSubmatch(htmlText, -1) {
			href := strings.TrimSpace(m[1])
			inner := m[2]
			if href == "" || strings.HasPrefix(href, "#") || strings.HasPrefix(href, "javascript:") {
				continue
			}
			full := resolveURL(finalURL, href)
			pu, err := url.Parse(full)
			if err != nil {
				continue
			}
			pq := pu.Query()
			if ov := pq.Get("offset"); ov != "" {
				if n, err := strconv.Atoi(ov); err == nil {
					offsetsMap[n] = true
				}
			}
			if pv := pq.Get("page"); pv != "" {
				if n, err := strconv.Atoi(pv); err == nil {
					pagesMap[n] = true
				}
			}

			if strings.Contains(strings.ToLower(pu.Host), "eja.tv") && !isFilterURL(full) {
				if _, ok := items[full]; ok {
					continue
				}
				name := cleanText(inner)
				if !validName(name, noise) {
					if tm := reAttrTitle.FindStringSubmatch(m[0]); len(tm) > 1 {
						cand := cleanText(tm[1])
						if validName(cand, noise) {
							name = cand
						}
					}
				}
				logo := ""
				if im := reImgSrc.FindStringSubmatch(inner); len(im) > 1 {
					logo = resolveURL(finalURL, im[1])
				}
				items[full] = &Channel{
					Key:          full,
					Name:         name,
					WatchURL:     full,
					Logo:         logo,
					Category:     category,
					ExtraHeaders: map[string]string{},
					SourceName:   "eja.tv",
				}
				added++
			}
		}

		if added == 0 {
			break
		}
		count++

		var hiOff, hiPg []int
		for o := range offsetsMap {
			if o > offset {
				hiOff = append(hiOff, o)
			}
		}
		for p := range pagesMap {
			if p > page {
				hiPg = append(hiPg, p)
			}
		}
		sort.Ints(hiOff)
		sort.Ints(hiPg)
		if len(hiOff) > 0 {
			offset = hiOff[0]
		} else if len(hiPg) > 0 {
			usePage = true
			page = hiPg[0]
		} else {
			if added < 18 {
				offset += 18
			} else {
				offset += added
			}
		}
	}

	out := make([]*Channel, 0, len(items))
	for _, ch := range items {
		out = append(out, ch)
	}
	return out
}

func parseExternalM3U(src ExternalSource, allowedLangs map[string]bool, onlyMatch, skipRawIP bool, noise map[string]bool) []*Channel {
	if strings.TrimSpace(src.URL) == "" {
		return nil
	}
	req, err := http.NewRequest("GET", src.URL, nil)
	if err != nil {
		return nil
	}
	req.Header.Set("User-Agent", defaultUA)
	resp, err := pageClient.Do(req)
	if err != nil || resp.StatusCode != 200 {
		if resp != nil {
			resp.Body.Close()
		}
		fmt.Printf("[WARN] Failed external M3U '%s'\n", src.Name)
		return nil
	}
	defer resp.Body.Close()

	var results []*Channel
	var cur *Channel
	curHdrs := make(map[string]string)

	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 0, 64*1024), 2*1024*1024)

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		if strings.HasPrefix(line, "#EXTINF:") {
			curHdrs = make(map[string]string)
			tvgID := ""
			if m := reExtInfID.FindStringSubmatch(line); len(m) > 1 {
				tvgID = strings.TrimSpace(m[1])
			}
			logo := ""
			if m := reExtInfLogo.FindStringSubmatch(line); len(m) > 1 {
				logo = strings.TrimSpace(m[1])
			}
			lang := src.DefaultLanguage
			if m := reExtInfLang.FindStringSubmatch(line); len(m) > 1 && strings.TrimSpace(m[1]) != "" {
				lang = strings.TrimSpace(m[1])
			}
			cntry := src.DefaultCountry
			if m := reExtInfCntry.FindStringSubmatch(line); len(m) > 1 && strings.TrimSpace(m[1]) != "" {
				cntry = strings.TrimSpace(m[1])
			}
			grp := ""
			if m := reExtInfGrp.FindStringSubmatch(line); len(m) > 1 {
				grp = strings.TrimSpace(m[1])
			}
			if m := reExtInfUA.FindStringSubmatch(line); len(m) > 1 && strings.TrimSpace(m[1]) != "" {
				curHdrs["User-Agent"] = strings.TrimSpace(m[1])
			}
			if m := reExtInfRef.FindStringSubmatch(line); len(m) > 1 && strings.TrimSpace(m[1]) != "" {
				curHdrs["Referer"] = strings.TrimSpace(m[1])
			}

			titlePart := ""
			if comma := strings.LastIndex(line, ","); comma != -1 {
				titlePart = line[comma+1:]
			}
			cleanTitle := cleanText(reBracketNote.ReplaceAllString(titlePart, ""))

			cur = &Channel{
				Name:           cleanTitle,
				TvgID:          tvgID,
				Logo:           logo,
				Language:       lang,
				Country:        cntry,
				Category:       grp,
				SourceName:     src.Name,
				IsExternal:     true,
				IsFast:         src.IsFast,
				ExplicitTelugu: src.TeluguOnly,
			}
		} else if strings.HasPrefix(line, "#EXTVLCOPT:") && cur != nil {
			opt := strings.TrimSpace(strings.TrimPrefix(line, "#EXTVLCOPT:"))
			lowOpt := strings.ToLower(opt)
			if strings.HasPrefix(lowOpt, "http-user-agent=") {
				curHdrs["User-Agent"] = strings.TrimSpace(opt[strings.Index(opt, "=")+1:])
			} else if strings.HasPrefix(lowOpt, "http-referrer=") || strings.HasPrefix(lowOpt, "http-referer=") {
				curHdrs["Referer"] = strings.TrimSpace(opt[strings.Index(opt, "=")+1:])
			}
		} else if strings.HasPrefix(line, "#EXTHTTP:") && cur != nil {
			rawJSON := strings.TrimSpace(strings.TrimPrefix(line, "#EXTHTTP:"))
			var m map[string]string
			if json.Unmarshal([]byte(rawJSON), &m) == nil {
				for hk, hv := range m {
					switch strings.ToLower(hk) {
					case "user-agent":
						curHdrs["User-Agent"] = hv
					case "referer", "referrer":
						curHdrs["Referer"] = hv
					}
				}
			}
		} else if !strings.HasPrefix(line, "#") && cur != nil {
			cleanURL, pipeHdrs := splitPipeHeaders(line)
			merged := cloneHeaders(curHdrs)
			for k, v := range pipeHdrs {
				merged[k] = v
			}

			if isUsableHLSURL(cleanURL, skipRawIP) {
				chName := cur.Name
				if chName == "" {
					chName = humanizeURL(cleanURL)
				}
				if validName(chName, noise) {
					telFlag := isStrictlyTelugu(chName, cur.Language, cur.Category, cur.Country, src.TeluguOnly)
					if telFlag && cur.Language == "" {
						cur.Language = "Telugu"
					}
					langBlob := strings.ToLower(cur.Language + " " + cur.Category + " " + chName)
					langOK := src.AlwaysKeep || telFlag
					if !langOK {
						for lg := range allowedLangs {
							if strings.Contains(langBlob, lg) {
								langOK = true
								break
							}
						}
					}
					if !onlyMatch || langOK {
						cur.Key = cleanURL
						cur.Name = chName
						cur.StreamURL = cleanURL
						cur.ExtraHeaders = merged
						results = append(results, cur)
					}
				}
			}
			cur = nil
			curHdrs = make(map[string]string)
		}
	}
	return results
}

func doStreamGet(ctx context.Context, targetURL string, extraHdrs map[string]string) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, "GET", targetURL, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", defaultUA)
	req.Header.Set("Accept", "*/*")
	for k, v := range extraHdrs {
		req.Header.Set(k, v)
	}
	return streamClient.Do(req)
}

func inspectAndVerifyHLS(ctx context.Context, startURL string, extraHdrs map[string]string, skipRawIP bool) (string, int, string, int, bool) {
	t0 := time.Now()
	currentURL := startURL
	exportM3U8 := startURL
	maxHeight := 0
	var lines []string
	playlistBase := currentURL

	for hop := 0; hop < 3; hop++ {
		if !isUsableHLSURL(currentURL, skipRawIP) {
			return "", 0, "", 0, false
		}
		reqCtx, cancel := context.WithTimeout(ctx, 4500*time.Millisecond)
		resp, err := doStreamGet(reqCtx, currentURL, extraHdrs)
		if err != nil {
			cancel()
			return "", 0, "", 0, false
		}
		if resp.StatusCode != 200 {
			resp.Body.Close()
			cancel()
			return "", 0, "", 0, false
		}
		bodyBytes, err := io.ReadAll(io.LimitReader(resp.Body, 512*1024))
		finalURL := resp.Request.URL.String()
		resp.Body.Close()
		cancel()
		if err != nil {
			return "", 0, "", 0, false
		}
		text := string(bodyBytes)
		head := text
		if len(head) > 300 {
			head = head[:300]
		}
		if !strings.Contains(head, "#EXTM3U") {
			return "", 0, "", 0, false
		}

		playlistBase = finalURL
		if hop == 0 {
			// Preserve top-level master .m3u8 so demuxed audio tracks (e.g. History TV18) stay intact
			exportM3U8 = finalURL
		}

		rawLines := strings.Split(text, "\n")
		lines = lines[:0]
		hasVariant := false
		for _, ln := range rawLines {
			ln = strings.TrimSpace(ln)
			if ln != "" {
				lines = append(lines, ln)
				if strings.HasPrefix(ln, "#EXT-X-STREAM-INF") {
					hasVariant = true
				}
			}
		}

		if hasVariant {
			bestVariant := ""
			bestBW := -1
			for i, ln := range lines {
				if !strings.HasPrefix(ln, "#EXT-X-STREAM-INF") {
					continue
				}
				if rm := reResolution.FindStringSubmatch(ln); len(rm) > 1 {
					if h, err := strconv.Atoi(rm[1]); err == nil && h > maxHeight {
						maxHeight = h
					}
				}
				bw := 0
				if bm := reBandwidth.FindStringSubmatch(ln); len(bm) > 1 {
					bw, _ = strconv.Atoi(bm[1])
				}
				if i+1 < len(lines) && !strings.HasPrefix(lines[i+1], "#") {
					if bw >= bestBW {
						bestBW = bw
						bestVariant = resolveURL(playlistBase, lines[i+1])
					}
				}
			}
			if bestVariant == "" {
				return "", 0, "", 0, false
			}
			currentURL = bestVariant
		} else {
			break
		}
	}

	segRel := ""
	for _, ln := range lines {
		if !strings.HasPrefix(ln, "#") {
			segRel = ln
			break
		}
	}
	if segRel == "" {
		return "", 0, "", 0, false
	}

	segURL := resolveURL(playlistBase, segRel)
	segCtx, cancel := context.WithTimeout(ctx, 4*time.Second)
	defer cancel()
	sr, err := doStreamGet(segCtx, segURL, extraHdrs)
	if err != nil {
		return "", 0, "", 0, false
	}
	defer sr.Body.Close()

	if sr.StatusCode != 200 && sr.StatusCode != 206 {
		return "", 0, "", 0, false
	}
	buf := make([]byte, 256)
	n, _ := sr.Body.Read(buf)
	if n <= 0 {
		return "", 0, "", 0, false
	}

	latMs := int(time.Since(t0).Milliseconds())
	qTag := ""
	if maxHeight >= 480 {
		qTag = fmt.Sprintf("%dp", maxHeight)
	}
	return exportM3U8, maxHeight, qTag, latMs, true
}

func resolveItem(ctx context.Context, item *Channel, noise map[string]bool, verify, skipRawIP bool) (*Channel, bool) {
	ch := *item
	ch.ExtraHeaders = cloneHeaders(item.ExtraHeaders)

	if ch.StreamURL != "" {
		if verify {
			finalURL, h, qTag, lat, ok := inspectAndVerifyHLS(ctx, ch.StreamURL, ch.ExtraHeaders, skipRawIP)
			if !ok {
				return nil, false
			}
			ch.StreamURL = finalURL
			ch.Height = h
			ch.QualityTag = qTag
			ch.LatencyMs = lat
		} else {
			ch.LatencyMs = 999
		}
		if !validName(ch.Name, noise) {
			ch.Name = humanizeURL(ch.StreamURL)
		}
		if !validName(ch.Name, noise) {
			return nil, false
		}
		return &ch, true
	}

	if ch.WatchURL == "" {
		return nil, false
	}
	reqCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	resp, err := doStreamGet(reqCtx, ch.WatchURL, nil)
	if err != nil || resp.StatusCode != 200 {
		if resp != nil {
			resp.Body.Close()
		}
		cancel()
		return nil, false
	}
	bodyBytes, _ := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
	resp.Body.Close()
	cancel()

	htmlText := string(bodyBytes)
	if !validName(ch.Name, noise) {
		for _, hm := range reHeadingTag.FindAllStringSubmatch(htmlText, -1) {
			cand := cleanText(hm[1])
			if validName(cand, noise) {
				ch.Name = cand
				break
			}
		}
	}

	for _, sURL := range extractM3U8FromText(htmlText) {
		if verify {
			finalURL, h, qTag, lat, ok := inspectAndVerifyHLS(ctx, sURL, ch.ExtraHeaders, skipRawIP)
			if ok {
				ch.StreamURL = finalURL
				ch.Height = h
				ch.QualityTag = qTag
				ch.LatencyMs = lat
				if !validName(ch.Name, noise) {
					ch.Name = humanizeURL(ch.StreamURL)
				}
				if validName(ch.Name, noise) {
					return &ch, true
				}
			}
		} else {
			ch.StreamURL = sURL
			ch.LatencyMs = 999
			if !validName(ch.Name, noise) {
				ch.Name = humanizeURL(ch.StreamURL)
			}
			if validName(ch.Name, noise) {
				return &ch, true
			}
		}
	}
	return nil, false
}

func deduplicateChannels(channels []*Channel, maxMirrors int) []*Channel {
	grouped := make(map[string][]*Channel)
	for _, ch := range channels {
		norm := normalizeChannelKey(ch.Name)
		if norm == "" {
			norm = ch.StreamURL
		}
		copyCh := *ch
		grouped[norm] = append(grouped[norm], &copyCh)
	}

	var output []*Channel
	for _, mirrors := range grouped {
		sort.Slice(mirrors, func(i, j int) bool {
			a, b := mirrors[i], mirrors[j]
			if a.Height != b.Height {
				return a.Height > b.Height
			}
			aHTTPS := strings.HasPrefix(a.StreamURL, "https://")
			bHTTPS := strings.HasPrefix(b.StreamURL, "https://")
			if aHTTPS != bHTTPS {
				return aHTTPS
			}
			if (a.TvgID != "") != (b.TvgID != "") {
				return a.TvgID != ""
			}
			sa, sb := nameQualityScore(a.Name), nameQualityScore(b.Name)
			if sa != sb {
				return sa > sb
			}
			return a.LatencyMs < b.LatencyMs
		})

		bestTitle := mirrors[0].Name
		bestScore := nameQualityScore(bestTitle)
		for _, m := range mirrors[1:] {
			if sc := nameQualityScore(m.Name); sc > bestScore {
				bestScore = sc
				bestTitle = m.Name
			}
			if mirrors[0].TvgID == "" && m.TvgID != "" {
				mirrors[0].TvgID = m.TvgID
			}
			if mirrors[0].Logo == "" && m.Logo != "" {
				mirrors[0].Logo = m.Logo
			}
			if mirrors[0].Country == "" && m.Country != "" {
				mirrors[0].Country = m.Country
			}
			if mirrors[0].Language == "" && m.Language != "" {
				mirrors[0].Language = m.Language
			}
			if mirrors[0].Category == "" && m.Category != "" {
				mirrors[0].Category = m.Category
			}
		}

		mirrors[0].Name = bestTitle
		if maxMirrors <= 1 {
			output = append(output, mirrors[0])
		} else {
			seen := make(map[string]bool)
			kept := 0
			for _, m := range mirrors {
				if seen[m.StreamURL] {
					continue
				}
				seen[m.StreamURL] = true
				cm := *m
				if kept == 0 {
					cm.Name = bestTitle
				} else {
					cm.Name = fmt.Sprintf("%s [Mirror %d]", bestTitle, kept+1)
				}
				output = append(output, &cm)
				kept++
				if kept >= maxMirrors {
					break
				}
			}
		}
	}
	return output
}

func writeM3U(path string, channels []*Channel, epgURLs []string, dedup bool, maxMirrors int, tagInfo, subgroupTelugu bool) int {
	_ = os.MkdirAll(filepath.Dir(path), 0o755)
	list := channels
	if dedup {
		list = deduplicateChannels(channels, maxMirrors)
	}
	sort.Slice(list, func(i, j int) bool {
		return strings.ToLower(list[i].Name) < strings.ToLower(list[j].Name)
	})

	var sb strings.Builder
	if len(epgURLs) > 0 {
		epgAttr := strings.Join(epgURLs, ",")
		sb.WriteString(fmt.Sprintf("#EXTM3U url-tvg=\"%s\" x-tvg-url=\"%s\"\n", epgAttr, epgAttr))
	} else {
		sb.WriteString("#EXTM3U\n")
	}

	seen := make(map[string]bool)
	for _, ch := range list {
		if seen[ch.StreamURL] {
			continue
		}
		seen[ch.StreamURL] = true

		dispName := ch.Name
		if tagInfo && ch.QualityTag != "" {
			dispName = fmt.Sprintf("%s [%s]", ch.Name, ch.QualityTag)
		}
		tvgID := ch.TvgID
		if tvgID == "" {
			tvgID = strings.Trim(reTvgIDClean.ReplaceAllString(strings.ToLower(ch.Name), "."), ".")
		}
		group := ch.Category
		if subgroupTelugu {
			group = classifyTeluguGroup(ch)
		} else if group == "" {
			if ch.Country != "" {
				group = ch.Country
			} else {
				group = "General"
			}
		}

		sb.WriteString(fmt.Sprintf(
			"#EXTINF:-1 tvg-id=\"%s\" tvg-name=\"%s\" tvg-logo=\"%s\" tvg-country=\"%s\" tvg-language=\"%s\" group-title=\"%s\",%s\n",
			tvgID, ch.Name, ch.Logo, ch.Country, ch.Language, group, dispName,
		))
		if ua := ch.ExtraHeaders["User-Agent"]; ua != "" {
			sb.WriteString("#EXTVLCOPT:http-user-agent=" + ua + "\n")
		}
		if ref := ch.ExtraHeaders["Referer"]; ref != "" {
			sb.WriteString("#EXTVLCOPT:http-referrer=" + ref + "\n")
		}
		sb.WriteString(ch.StreamURL + "\n")
	}

	_ = os.WriteFile(path, []byte(sb.String()), 0o644)
	return len(seen)
}

func verificationPriority(ch *Channel) int {
	if ch.ExplicitTelugu || isStrictlyTelugu(ch.Name, ch.Language, ch.Category, ch.Country, false) {
		return 0
	}
	cLow := strings.ToLower(ch.Country)
	if cLow == "india" || cLow == "in" {
		return 1
	}
	if ch.IsFast {
		return 2
	}
	return 3
}

func main() {
	startTime := time.Now()
	rawCfg, err := os.ReadFile("config.json")
	if err != nil {
		fmt.Printf("Fatal: cannot read config.json: %v\n", err)
		os.Exit(1)
	}
	var cfg Config
	if err := json.Unmarshal(rawCfg, &cfg); err != nil {
		fmt.Printf("Fatal: invalid config.json: %v\n", err)
		os.Exit(1)
	}
	if cfg.MaxPagesPerQuery <= 0 || cfg.MaxPagesPerQuery > 15 {
		cfg.MaxPagesPerQuery = 12
	}
	if cfg.MaxVerifySeconds <= 0 {
		cfg.MaxVerifySeconds = 300
	}
	if cfg.MaxWorkers <= 0 {
		cfg.MaxWorkers = 350
	}

	allowedLangs := map[string]bool{"telugu": true, "hindi": true, "tamil": true, "english": true}
	for _, lg := range cfg.Languages {
		allowedLangs[strings.ToLower(strings.TrimSpace(lg))] = true
	}

	fmt.Println("Step 1/5: Loading iptv-org EPG/logo catalog & health cache (Go 1.27 native engine)...")
	healthCache := newHealthCache(healthCacheFilePath)
	noise := discoverEjaNoise()
	catalog := loadIPTVOrgCatalog()
	fmt.Printf("  Indexed %d official channel signatures for EPG & HD logos.\n", len(catalog))

	type crawlTask struct {
		bucket, label                      string
		maxPages                           int
		country, language, category, query string
	}
	tasks := []crawlTask{{bucket: "general", label: "Global Feed", maxPages: cfg.MaxPagesPerQuery}}
	for _, c := range cfg.Countries {
		code, lbl := c, c
		if al, ok := countryAliases[strings.ToLower(strings.TrimSpace(c))]; ok {
			code, lbl = al[0], al[1]
		}
		tasks = append(tasks, crawlTask{bucket: "countries", label: lbl, maxPages: cfg.MaxPagesPerQuery, country: code})
	}
	for _, l := range cfg.Languages {
		code, lbl := l, l
		if al, ok := langAliases[strings.ToLower(strings.TrimSpace(l))]; ok {
			code, lbl = al[0], al[1]
		}
		tasks = append(tasks, crawlTask{bucket: "languages", label: lbl, maxPages: cfg.MaxPagesPerQuery, language: code})
	}
	for _, cat := range cfg.Categories {
		tasks = append(tasks, crawlTask{bucket: "categories", label: cat, maxPages: cfg.MaxPagesPerQuery, category: cat})
	}
	for _, term := range cfg.TeluguHubSearches {
		tasks = append(tasks, crawlTask{bucket: "telugu_search", label: titleCase(term), maxPages: 5, query: term})
	}
	for _, term := range cfg.CustomSearches {
		tasks = append(tasks, crawlTask{bucket: "searches", label: titleCase(term), maxPages: 6, query: term})
	}

	var regMu sync.Mutex
	registry := make(map[string]*Channel)
	membership := map[string]map[string]map[string]bool{
		"countries":  {},
		"languages":  {},
		"categories": {},
		"searches":   {},
		"external":   {},
	}
	addMember := func(bucket, lbl, key string) {
		if _, ok := membership[bucket]; !ok {
			return
		}
		if membership[bucket][lbl] == nil {
			membership[bucket][lbl] = make(map[string]bool)
		}
		membership[bucket][lbl][key] = true
	}

	fmt.Printf("Step 2/5: Crawling %d eja.tv queries concurrently...\n", len(tasks))
	var crawlWG sync.WaitGroup
	crawlSem := make(chan struct{}, 20)
	for _, t := range tasks {
		crawlWG.Add(1)
		go func(tk crawlTask) {
			defer crawlWG.Done()
			crawlSem <- struct{}{}
			found := crawlEjaPages(noise, tk.maxPages, tk.country, tk.language, tk.category, tk.query)
			<-crawlSem

			regMu.Lock()
			for _, item := range found {
				if existing, ok := registry[item.Key]; !ok {
					registry[item.Key] = item
				} else {
					if existing.Name == "" {
						existing.Name = item.Name
					}
					if existing.Logo == "" {
						existing.Logo = item.Logo
					}
					if existing.Category == "" {
						existing.Category = item.Category
					}
				}
				switch tk.bucket {
				case "countries":
					if registry[item.Key].Country == "" {
						registry[item.Key].Country = tk.label
					}
				case "languages":
					if registry[item.Key].Language == "" {
						registry[item.Key].Language = tk.label
					}
				case "categories":
					if registry[item.Key].Category == "" {
						registry[item.Key].Category = tk.label
					}
				}
				addMember(tk.bucket, tk.label, item.Key)
			}
			regMu.Unlock()
		}(t)
	}
	crawlWG.Wait()
	fmt.Printf("  eja.tv crawl finished (%d unique items in %ds).\n", len(registry), int(time.Since(startTime).Seconds()))

	fmt.Printf("Step 3/5: Importing %d external public & Kodi M3U sources concurrently...\n", len(cfg.ExternalM3USources))
	sourceCandidateCounts := make(map[string]int)
	var extWG sync.WaitGroup
	extSem := make(chan struct{}, 24)
	for _, src := range cfg.ExternalM3USources {
		extWG.Add(1)
		go func(s ExternalSource) {
			defer extWG.Done()
			extSem <- struct{}{}
			extItems := parseExternalM3U(s, allowedLangs, cfg.OnlyMatchingExternalLanguages, cfg.SkipRawIPStreams, noise)
			<-extSem

			regMu.Lock()
			sourceCandidateCounts[s.Name] = len(extItems)
			fmt.Printf("  [%s] -> %d candidates\n", s.Name, len(extItems))
			for _, item := range extItems {
				if existing, ok := registry[item.Key]; !ok {
					registry[item.Key] = item
				} else {
					if existing.Name == "" {
						existing.Name = item.Name
					}
					if existing.TvgID == "" {
						existing.TvgID = item.TvgID
					}
					if existing.Logo == "" {
						existing.Logo = item.Logo
					}
					if existing.Country == "" {
						existing.Country = item.Country
					}
					if existing.Language == "" {
						existing.Language = item.Language
					}
					if existing.Category == "" {
						existing.Category = item.Category
					}
					if len(existing.ExtraHeaders) == 0 && len(item.ExtraHeaders) > 0 {
						existing.ExtraHeaders = item.ExtraHeaders
					}
					if item.ExplicitTelugu {
						existing.ExplicitTelugu = true
					}
					if item.IsFast {
						existing.IsFast = true
					}
				}
				addMember("external", s.Name, item.Key)
				if strings.EqualFold(item.Country, "India") || strings.EqualFold(item.Country, "IN") {
					addMember("countries", "India", item.Key)
				}
			}
			regMu.Unlock()
		}(src)
	}
	extWG.Wait()

	// Build priority verification queue (skip 72h dead-cached URLs, cap 5 mirrors/channel before probing)
	buckets := make(map[string][]*Channel)
	skippedCached := 0
	for k, ch := range registry {
		if healthCache.shouldSkip(k) {
			skippedCached++
			continue
		}
		norm := normalizeChannelKey(ch.Name)
		if norm == "" {
			norm = k
		}
		buckets[norm] = append(buckets[norm], ch)
	}
	var queue []*Channel
	for _, list := range buckets {
		sort.Slice(list, func(i, j int) bool {
			if list[i].IsExternal != list[j].IsExternal {
				return list[i].IsExternal
			}
			return nameQualityScore(list[i].Name) > nameQualityScore(list[j].Name)
		})
		limit := 5
		if len(list) < limit {
			limit = len(list)
		}
		queue = append(queue, list[:limit]...)
	}
	sort.Slice(queue, func(i, j int) bool {
		pi, pj := verificationPriority(queue[i]), verificationPriority(queue[j])
		if pi != pj {
			return pi < pj
		}
		return nameQualityScore(queue[i].Name) > nameQualityScore(queue[j].Name)
	})

	totalToCheck := len(queue)
	fmt.Printf("Step 4/5: Priority-verifying %d streams (skipped %d cached dead) across %d goroutines (max %ds)...\n",
		totalToCheck, skippedCached, cfg.MaxWorkers, cfg.MaxVerifySeconds)

	verifyCtx, verifyCancel := context.WithTimeout(context.Background(), time.Duration(cfg.MaxVerifySeconds)*time.Second)
	defer verifyCancel()

	jobs := make(chan *Channel, totalToCheck)
	for _, ch := range queue {
		jobs <- ch
	}
	close(jobs)

	var resMu sync.Mutex
	resolved := make(map[string]*Channel)
	var checkedCount, liveCount atomic.Int64

	var workerWG sync.WaitGroup
	for w := 0; w < cfg.MaxWorkers; w++ {
		workerWG.Add(1)
		go func() {
			defer workerWG.Done()
			for ch := range jobs {
				if verifyCtx.Err() != nil {
					return
				}
				res, ok := resolveItem(verifyCtx, ch, noise, cfg.VerifyStreams, cfg.SkipRawIPStreams)
				if ok {
					enrichChannel(res, catalog)
					resMu.Lock()
					resolved[ch.Key] = res
					resMu.Unlock()
					healthCache.markAlive(ch.Key)
					liveCount.Add(1)
				} else if verifyCtx.Err() == nil {
					healthCache.markDead(ch.Key)
				}
				c := checkedCount.Add(1)
				if c%250 == 0 || int(c) == totalToCheck {
					fmt.Printf("  Progress: %d/%d checked (%d live)...\n", c, totalToCheck, liveCount.Load())
				}
			}
		}()
	}
	workerWG.Wait()
	healthCache.save()

	allWorking := make([]*Channel, 0, len(resolved))
	for _, ch := range resolved {
		allWorking = append(allWorking, ch)
	}

	fmt.Println("Step 5/5: Writing EPG-enriched M3U playlists & health reports...")
	var extNames []string
	for name := range membership["external"] {
		extNames = append(extNames, name)
	}
	sort.Strings(extNames)
	var sourceStats []SourceStat
	for _, srcName := range extNames {
		keys := membership["external"][srcName]
		alive := 0
		for k := range keys {
			if _, ok := resolved[k]; ok {
				alive++
			}
		}
		sourceStats = append(sourceStats, SourceStat{
			Source:   srcName,
			Alive:    alive,
			Imported: sourceCandidateCounts[srcName],
		})
		fmt.Printf("  %s: %d working / %d imported\n", srcName, alive, sourceCandidateCounts[srcName])
	}

	totalAll := writeM3U("playlists/all.m3u", allWorking, cfg.EPGURLs, cfg.DeduplicateChannels, 1, cfg.TagStreamInfo, false)
	totalBackups := writeM3U("playlists/special/with_backups.m3u", allWorking, cfg.EPGURLs, true, 3, cfg.TagStreamInfo, false)

	var teluguChannels, indiaChannels, fastChannels, sportsChannels, hdChannels []*Channel
	for _, ch := range allWorking {
		if isStrictlyTelugu(ch.Name, ch.Language, ch.Category, ch.Country, ch.ExplicitTelugu) {
			cp := *ch
			cp.Language = "Telugu"
			teluguChannels = append(teluguChannels, &cp)
		}
		if strings.EqualFold(ch.Country, "India") || strings.EqualFold(ch.Country, "IN") {
			indiaChannels = append(indiaChannels, ch)
		}
		if ch.IsFast {
			fastChannels = append(fastChannels, ch)
		}
		if isSportsChannel(ch) {
			sportsChannels = append(sportsChannels, ch)
		}
		if ch.Height >= 720 {
			hdChannels = append(hdChannels, ch)
		}
	}

	totalTeluguHub := writeM3U("playlists/special/telugu_hub.m3u", teluguChannels, cfg.EPGURLs, cfg.DeduplicateChannels, 1, cfg.TagStreamInfo, true)
	totalTeluguBackups := writeM3U("playlists/special/telugu_hub_backups.m3u", teluguChannels, cfg.EPGURLs, true, 4, cfg.TagStreamInfo, true)
	totalIndia := writeM3U("playlists/special/india_all.m3u", indiaChannels, cfg.EPGURLs, cfg.DeduplicateChannels, 1, cfg.TagStreamInfo, false)
	totalFast := writeM3U("playlists/special/fast_movies_tv.m3u", fastChannels, cfg.EPGURLs, cfg.DeduplicateChannels, 1, cfg.TagStreamInfo, false)
	totalSports := writeM3U("playlists/special/sports_live.m3u", sportsChannels, cfg.EPGURLs, cfg.DeduplicateChannels, 1, cfg.TagStreamInfo, false)
	totalHD := writeM3U("playlists/special/hd_1080p.m3u", hdChannels, cfg.EPGURLs, cfg.DeduplicateChannels, 1, cfg.TagStreamInfo, false)

	type summaryRow struct {
		label, rel string
		count      int
	}
	sectionOrder := []struct{ bucket, title string }{
		{"countries", "Country"},
		{"languages", "Language"},
		{"categories", "Category"},
		{"searches", "Custom Search"},
	}
	summary := make(map[string][]summaryRow)
	for _, sec := range sectionOrder {
		for lbl, keys := range membership[sec.bucket] {
			var clist []*Channel
			subFlag := false
			if sec.bucket == "languages" && lbl == "Telugu" {
				clist = teluguChannels
				subFlag = true
			} else {
				for k := range keys {
					if ch, ok := resolved[k]; ok {
						clist = append(clist, ch)
					}
				}
			}
			fname := fmt.Sprintf("playlists/%s/%s.m3u", sec.bucket, safeFilename(lbl))
			cnt := writeM3U(fname, clist, cfg.EPGURLs, cfg.DeduplicateChannels, 1, cfg.TagStreamInfo, subFlag)
			summary[sec.title] = append(summary[sec.title], summaryRow{label: lbl, rel: fname, count: cnt})
		}
	}

	ist := time.FixedZone("IST", 5*3600+30*60)
	nowIST := time.Now().In(ist).Format("02 Jan 2006, 03:04 PM IST")
	epgMatched := 0
	for _, ch := range allWorking {
		if ch.TvgID != "" {
			epgMatched++
		}
	}
	epgPct := 0
	if len(allWorking) > 0 {
		epgPct = (epgMatched * 100) / len(allWorking)
	}
	elapsed := int(time.Since(startTime).Seconds())

	healthPayload := map[string]any{
		"engine":          "Go 1.27 Native Concurrent Aggregator (Pro v4)",
		"last_synced_ist": nowIST,
		"elapsed_seconds": elapsed,
		"totals": map[string]int{
			"all":               totalAll,
			"telugu_hub":        totalTeluguHub,
			"telugu_backups":    totalTeluguBackups,
			"india_all":         totalIndia,
			"fast_movies_tv":    totalFast,
			"sports_live":       totalSports,
			"hd_1080p":          totalHD,
			"epg_match_percent": epgPct,
		},
		"sources": sourceStats,
	}
	if hb, err := json.MarshalIndent(healthPayload, "", "  "); err == nil {
		_ = os.WriteFile("playlists/health.json", hb, 0o644)
	}

	repo := os.Getenv("GITHUB_REPOSITORY")
	if repo == "" {
		repo = "Tarke4618/iptv"
	}
	rawBase := fmt.Sprintf("https://raw.githubusercontent.com/%s/main", repo)

	var md strings.Builder
	md.WriteString("# Verified Multi-Source IPTV Playlists (Go 1.27 Pro v4)\n\n")
	md.WriteString(fmt.Sprintf("**Last Synced:** `%s` | **Build Time:** `%ds` | **EPG & Logo Match:** `%d%%`\n\n", nowIST, elapsed, epgPct))
	md.WriteString("### Dedicated Telugu & India Playlists (Strict Filter — IPTVnator Ready)\n")
	md.WriteString("| Playlist | Verified Channels | Raw M3U URL |\n| :--- | :---: | :--- |\n")
	md.WriteString(fmt.Sprintf("| **Telugu Master Hub (Clean Best Mirror)** | %d | `%s/playlists/special/telugu_hub.m3u` |\n", totalTeluguHub, rawBase))
	md.WriteString(fmt.Sprintf("| **Telugu Master Hub + Backup Mirrors (Up to 4x)** | %d | `%s/playlists/special/telugu_hub_backups.m3u` |\n", totalTeluguBackups, rawBase))
	md.WriteString(fmt.Sprintf("| **All India Verified Channels** | %d | `%s/playlists/special/india_all.m3u` |\n", totalIndia, rawBase))
	md.WriteString(fmt.Sprintf("| **Telugu Language Standard List** | %d | `%s/playlists/languages/telugu.m3u` |\n\n", totalTeluguHub, rawBase))

	md.WriteString("### FAST Movies, Sports, Kodi & Global Smart Playlists\n")
	md.WriteString("| Playlist | Verified Channels | Raw M3U URL |\n| :--- | :---: | :--- |\n")
	md.WriteString(fmt.Sprintf("| **FAST Movies & Live TV (LG, Samsung, Vizio, Roku, Tubi, Whale, Kodi)** | %d | `%s/playlists/special/fast_movies_tv.m3u` |\n", totalFast, rawBase))
	md.WriteString(fmt.Sprintf("| **Live Sports Hub (Cricket, Football, Global Sports)** | %d | `%s/playlists/special/sports_live.m3u` |\n", totalSports, rawBase))
	md.WriteString(fmt.Sprintf("| **All Verified Channels (Best Mirror)** | %d | `%s/playlists/all.m3u` |\n", totalAll, rawBase))
	md.WriteString(fmt.Sprintf("| **All Channels + Backup Mirrors (Up to 3x)** | %d | `%s/playlists/special/with_backups.m3u` |\n", totalBackups, rawBase))
	md.WriteString(fmt.Sprintf("| **HD 720p / 1080p Verified Streams** | %d | `%s/playlists/special/hd_1080p.m3u` |\n\n", totalHD, rawBase))

	for _, sec := range sectionOrder {
		rows := summary[sec.title]
		if len(rows) == 0 {
			continue
		}
		sort.Slice(rows, func(i, j int) bool {
			return strings.ToLower(rows[i].label) < strings.ToLower(rows[j].label)
		})
		md.WriteString(fmt.Sprintf("### By %s\n", sec.title))
		md.WriteString("| Filter | Verified Channels | Raw M3U URL |\n| :--- | :---: | :--- |\n")
		for _, r := range rows {
			md.WriteString(fmt.Sprintf("| **%s** | %d | `%s/%s` |\n", r.label, r.count, rawBase, r.rel))
		}
		md.WriteString("\n")
	}

	_ = os.WriteFile("README.md", []byte(md.String()), 0o644)
	fmt.Printf("Done in %ds! Telugu=%d, India=%d, Sports=%d, FAST=%d, HD=%d, All=%d, EPG=%d%%\n",
		elapsed, totalTeluguHub, totalIndia, totalSports, totalFast, totalHD, totalAll, epgPct)
}
