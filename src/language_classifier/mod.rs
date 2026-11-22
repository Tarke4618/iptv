use crate::iptv_org::IPTVOrgCatalog;
use crate::models::CatalogEntry;
use dashmap::DashSet;
use std::collections::HashMap;

#[derive(Clone)]
pub struct LanguageClassifier {
    // Language detection patterns (positive keywords)
    language_patterns: HashMap<String, Vec<String>>,
    // Exclusion patterns per language (negative keywords)
    exclusion_patterns: HashMap<String, Vec<String>>,
    // IPTV-org catalog for authoritative language mapping
    catalog: HashMap<String, CatalogEntry>,
    // Supported languages (discovered from sources) - thread-safe for concurrent classification
    supported_languages: DashSet<String>,
    // ISO 639-3 code mapping
    iso_codes: HashMap<String, String>,
}

impl LanguageClassifier {
    pub fn new(catalog: &IPTVOrgCatalog) -> Self {
        let mut classifier = Self {
            language_patterns: HashMap::new(),
            exclusion_patterns: HashMap::new(),
            catalog: catalog.channels.clone(),
            supported_languages: DashSet::new(),
            iso_codes: Self::build_iso_codes(),
        };
        classifier.init_patterns();
        classifier
    }

    fn build_iso_codes() -> HashMap<String, String> {
        let mut m = HashMap::new();
        m.insert("english".to_string(), "eng".to_string());
        m.insert("hindi".to_string(), "hin".to_string());
        m.insert("telugu".to_string(), "tel".to_string());
        m.insert("tamil".to_string(), "tam".to_string());
        m.insert("malayalam".to_string(), "mal".to_string());
        m.insert("kannada".to_string(), "kan".to_string());
        m.insert("bengali".to_string(), "ben".to_string());
        m.insert("marathi".to_string(), "mar".to_string());
        m.insert("gujarati".to_string(), "guj".to_string());
        m.insert("punjabi".to_string(), "pan".to_string());
        m.insert("urdu".to_string(), "urd".to_string());
        m.insert("odia".to_string(), "ori".to_string());
        m.insert("assamese".to_string(), "asm".to_string());
        m.insert("spanish".to_string(), "spa".to_string());
        m.insert("french".to_string(), "fra".to_string());
        m.insert("german".to_string(), "deu".to_string());
        m.insert("arabic".to_string(), "ara".to_string());
        m.insert("russian".to_string(), "rus".to_string());
        m.insert("portuguese".to_string(), "por".to_string());
        m.insert("italian".to_string(), "ita".to_string());
        m.insert("japanese".to_string(), "jpn".to_string());
        m.insert("korean".to_string(), "kor".to_string());
        m.insert("chinese".to_string(), "zho".to_string());
        m.insert("turkish".to_string(), "tur".to_string());
        m.insert("persian".to_string(), "fas".to_string());
        m.insert("thai".to_string(), "tha".to_string());
        m.insert("vietnamese".to_string(), "vie".to_string());
        m.insert("indonesian".to_string(), "ind".to_string());
        m.insert("dutch".to_string(), "nld".to_string());
        m.insert("polish".to_string(), "pol".to_string());
        m.insert("ukrainian".to_string(), "ukr".to_string());
        m.insert("romanian".to_string(), "ron".to_string());
        m.insert("greek".to_string(), "ell".to_string());
        m.insert("hebrew".to_string(), "heb".to_string());
        m.insert("swedish".to_string(), "swe".to_string());
        m.insert("norwegian".to_string(), "nor".to_string());
        m.insert("danish".to_string(), "dan".to_string());
        m.insert("finnish".to_string(), "fin".to_string());
        m.insert("czech".to_string(), "ces".to_string());
        m.insert("hungarian".to_string(), "hun".to_string());
        m
    }

    fn init_patterns(&mut self) {
        // Positive language patterns (keywords that strongly indicate a language)
        self.language_patterns.insert(
            "telugu".to_string(),
            vec![
                "telugu".to_string(),
                "తెలుగు".to_string(),
                "etv".to_string(),
                "gemini".to_string(),
                "maa".to_string(),
                "zee telugu".to_string(),
                "zee cinemalu".to_string(),
                "tv9 telugu".to_string(),
                "ntv telugu".to_string(),
                "tv5 telugu".to_string(),
                "sakshi".to_string(),
                "abn andhra".to_string(),
                "v6 news".to_string(),
                "10tv".to_string(),
                "hmtv".to_string(),
                "t news".to_string(),
                "mahaa".to_string(),
                "raj news telugu".to_string(),
                "raj musix telugu".to_string(),
                "vissa".to_string(),
                "vanitha".to_string(),
                "prime9".to_string(),
                "99tv".to_string(),
                "6tv".to_string(),
                "cvr".to_string(),
                "svbc".to_string(),
                "bhakti tv".to_string(),
                "subhagruha".to_string(),
                "calvary tv".to_string(),
                "aradana".to_string(),
                "kushi tv".to_string(),
                "teluguone".to_string(),
                "dd yadagiri".to_string(),
                "dd saptagiri".to_string(),
            ],
        );

        self.language_patterns.insert(
            "hindi".to_string(),
            vec![
                "hindi".to_string(),
                "हिंदी".to_string(),
                "star plus".to_string(),
                "star bharat".to_string(),
                "colors".to_string(),
                "sony tv".to_string(),
                "sony sab".to_string(),
                "ze tv".to_string(),
                "&tv".to_string(),
                "star gold".to_string(),
                "sony max".to_string(),
                "zee cinema".to_string(),
                "colors cineplex".to_string(),
                "aaj tak".to_string(),
                "abp news".to_string(),
                "india tv".to_string(),
                "news18 india".to_string(),
                "republic bharat".to_string(),
                "tv9 bharatvarsh".to_string(),
                "dd national".to_string(),
                "dd news".to_string(),
                "loksabha tv".to_string(),
                "nick india".to_string(),
                "disney channel india".to_string(),
                "pogo".to_string(),
            ],
        );

        self.language_patterns.insert(
            "tamil".to_string(),
            vec![
                "tamil".to_string(),
                "தமிழ்".to_string(),
                "sun tv".to_string(),
                "vijay tv".to_string(),
                "zee tamil".to_string(),
                "colors tamil".to_string(),
                "kalaignar".to_string(),
                "jaya tv".to_string(),
                "polimer".to_string(),
                "puthiya thalaimurai".to_string(),
                "thanthi tv".to_string(),
                "captain tv".to_string(),
                "news18 tamil".to_string(),
                "pudhu yugam".to_string(),
                "dd podhigai".to_string(),
                "makkal tv".to_string(),
                "iyarkai tv".to_string(),
            ],
        );

        self.language_patterns.insert(
            "malayalam".to_string(),
            vec![
                "malayalam".to_string(),
                "മലയാളം".to_string(),
                "asianet".to_string(),
                "manorama".to_string(),
                "mathrubhumi".to_string(),
                "mazhavil".to_string(),
                "surya tv".to_string(),
                "flowers tv".to_string(),
                "amrita tv".to_string(),
                "janam tv".to_string(),
                "media one".to_string(),
                "reporter tv".to_string(),
                "twentyfour".to_string(),
                "24 news".to_string(),
                "jeevan tv".to_string(),
                "kaumudy".to_string(),
                "safari tv".to_string(),
                "dd malayalam".to_string(),
                "kairali".to_string(),
            ],
        );

        self.language_patterns.insert(
            "kannada".to_string(),
            vec![
                "kannada".to_string(),
                "ಕನ್ನಡ".to_string(),
                "colors kannada".to_string(),
                "zee kannada".to_string(),
                "star suvarna".to_string(),
                "udaya tv".to_string(),
                "suvarna news".to_string(),
                "tv9 kannada".to_string(),
                "news18 kannada".to_string(),
                "public tv".to_string(),
                "power tv".to_string(),
                "dd chandana".to_string(),
                "kasturi".to_string(),
                "pravasi".to_string(),
            ],
        );

        self.language_patterns.insert(
            "bengali".to_string(),
            vec![
                "bengali".to_string(),
                "bangla".to_string(),
                "বাংলা".to_string(),
                "star jalsha".to_string(),
                "zee bangla".to_string(),
                "colors bangla".to_string(),
                "sony aath".to_string(),
                "aakash aath".to_string(),
                "dd bangla".to_string(),
                "news18 bangla".to_string(),
                "abp ananda".to_string(),
                "zee 24 ghanta".to_string(),
                "kolkata tv".to_string(),
                "tv9 bangla".to_string(),
            ],
        );

        self.language_patterns.insert(
            "marathi".to_string(),
            vec![
                "marathi".to_string(),
                "मराठी".to_string(),
                "star pravah".to_string(),
                "zee marathi".to_string(),
                "colors marathi".to_string(),
                "sony marathi".to_string(),
                "dd sahyadri".to_string(),
                "news18 marathi".to_string(),
                "abp majha".to_string(),
                "tv9 marathi".to_string(),
                "saam tv".to_string(),
            ],
        );

        self.language_patterns.insert(
            "gujarati".to_string(),
            vec![
                "gujarati".to_string(),
                "ગુજરાતી".to_string(),
                "colors gujarati".to_string(),
                "dd girnar".to_string(),
                "tv9 gujarati".to_string(),
                "news18 gujarati".to_string(),
                "abp ashmita".to_string(),
                "gs tv".to_string(),
            ],
        );

        self.language_patterns.insert(
            "punjabi".to_string(),
            vec![
                "punjabi".to_string(),
                "ਪੰਜਾਬੀ".to_string(),
                "zee punjabi".to_string(),
                "colors punjabi".to_string(),
                "ptc punjabi".to_string(),
                "ptc news".to_string(),
                "ptc chakde".to_string(),
                "dd punjabi".to_string(),
            ],
        );

        self.language_patterns.insert(
            "english".to_string(),
            vec![
                "english".to_string(),
                "cnn".to_string(),
                "bbc".to_string(),
                "al jazeera english".to_string(),
                "dw news".to_string(),
                "france 24".to_string(),
                "rt".to_string(),
                "discovery".to_string(),
                "national geographic".to_string(),
                "history tv".to_string(),
                "cartoon network".to_string(),
                "nickelodeon".to_string(),
                "disney channel".to_string(),
                "hbo".to_string(),
                "star movies".to_string(),
                "warner bros".to_string(),
            ],
        );

        self.language_patterns.insert(
            "spanish".to_string(),
            vec![
                "spanish".to_string(),
                "español".to_string(),
                "telemundo".to_string(),
                "univision".to_string(),
                "antena 3".to_string(),
                "telecinco".to_string(),
                "la 1".to_string(),
                "la 2".to_string(),
                "rtve".to_string(),
                "rt".to_string(),
            ],
        );

        self.language_patterns.insert(
            "french".to_string(),
            vec![
                "french".to_string(),
                "français".to_string(),
                "tf1".to_string(),
                "france 2".to_string(),
                "france 3".to_string(),
                "m6".to_string(),
                "arte".to_string(),
                "bfm tv".to_string(),
            ],
        );

        self.language_patterns.insert(
            "german".to_string(),
            vec![
                "german".to_string(),
                "deutsch".to_string(),
                "ard".to_string(),
                "zdf".to_string(),
                "rtl".to_string(),
                "sat.1".to_string(),
                "pro7".to_string(),
                "dw deutsch".to_string(),
            ],
        );

        self.language_patterns.insert(
            "arabic".to_string(),
            vec![
                "arabic".to_string(),
                "العربية".to_string(),
                "al jazeera".to_string(),
                "al arabiya".to_string(),
                "mbc".to_string(),
                "rotana".to_string(),
                "sky news arabia".to_string(),
                "dubai tv".to_string(),
            ],
        );

        self.language_patterns.insert(
            "russian".to_string(),
            vec![
                "russian".to_string(),
                "русский".to_string(),
                "rt russian".to_string(),
                "channel one".to_string(),
                "russia 1".to_string(),
                "ntv".to_string(),
                "match tv".to_string(),
            ],
        );

        self.language_patterns.insert(
            "japanese".to_string(),
            vec![
                "japanese".to_string(),
                "日本語".to_string(),
                "nhk".to_string(),
                "tv tokyo".to_string(),
                "tv asahi".to_string(),
                "tbs".to_string(),
                "fuji tv".to_string(),
            ],
        );

        self.language_patterns.insert(
            "korean".to_string(),
            vec![
                "korean".to_string(),
                "한국어".to_string(),
                "kbs".to_string(),
                "mbc".to_string(),
                "sbs".to_string(),
                "ytn".to_string(),
                "jtbc".to_string(),
            ],
        );

        self.language_patterns.insert(
            "chinese".to_string(),
            vec![
                "chinese".to_string(),
                "中文".to_string(),
                "cctv".to_string(),
                "phoenix tv".to_string(),
                "hunan tv".to_string(),
                "zhejiang tv".to_string(),
                "jiangsu tv".to_string(),
            ],
        );

        // Exclusion patterns (negative keywords that indicate NOT this language)
        self.exclusion_patterns.insert(
            "telugu".to_string(),
            vec![
                "malayalam",
                "bangla",
                "bengali",
                "tamil",
                "kannada",
                "marathi",
                "gujarati",
                "punjabi",
                "odia",
                "oriya",
                "assamese",
                "bhojpuri",
                "urdu",
                "arabic",
                "persian",
                "farsi",
                "kurdish",
                "turkish",
                "russian",
                "spanish",
                "french",
                "german",
                "korean",
                "japanese",
                "chinese",
                "kairali",
                "asianet",
                "manorama",
                "mathrubhumi",
                "mazhavil",
                "surya tv",
                "flowers tv",
                "amrita tv",
                "janam tv",
                "media one",
                "reporter tv",
                "twentyfour",
                "24 news",
                "jeevan tv",
                "kaumudy",
                "safari tv",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
        );

        self.exclusion_patterns.insert(
            "hindi".to_string(),
            vec![
                "telugu",
                "tamil",
                "malayalam",
                "kannada",
                "bengali",
                "marathi",
                "gujarati",
                "punjabi",
                "odia",
                "assamese",
                "bhojpuri",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
        );

        self.exclusion_patterns.insert(
            "tamil".to_string(),
            vec![
                "telugu",
                "hindi",
                "malayalam",
                "kannada",
                "bengali",
                "marathi",
                "gujarati",
                "punjabi",
                "odia",
                "assamese",
                "bhojpuri",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
        );

        self.exclusion_patterns.insert(
            "malayalam".to_string(),
            vec![
                "telugu", "hindi", "tamil", "kannada", "bengali", "marathi", "gujarati", "punjabi",
                "odia", "assamese", "bhojpuri",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
        );

        self.exclusion_patterns.insert(
            "kannada".to_string(),
            vec![
                "telugu",
                "hindi",
                "tamil",
                "malayalam",
                "bengali",
                "marathi",
                "gujarati",
                "punjabi",
                "odia",
                "assamese",
                "bhojpuri",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
        );

        self.exclusion_patterns.insert(
            "bengali".to_string(),
            vec![
                "telugu",
                "hindi",
                "tamil",
                "malayalam",
                "kannada",
                "marathi",
                "gujarati",
                "punjabi",
                "odia",
                "assamese",
                "bhojpuri",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
        );

        // Add more exclusion patterns for other languages
        for lang in ["marathi", "gujarati", "punjabi", "urdu", "odia", "assamese"] {
            self.exclusion_patterns.insert(
                lang.to_string(),
                vec![
                    "telugu",
                    "hindi",
                    "tamil",
                    "malayalam",
                    "kannada",
                    "bengali",
                ]
                .into_iter()
                .map(String::from)
                .collect(),
            );
        }
    }

    pub fn classify(
        &self,
        name: &str,
        explicit_lang: &str,
        category: &str,
        country: &str,
    ) -> (String, String) {
        let name_lower = name.to_lowercase();
        let cat_lower = category.to_lowercase();
        let country_lower = country.to_lowercase();

        // 1. Check explicit language from source
        if !explicit_lang.is_empty() {
            let lang = self.normalize_language(explicit_lang);
            if self.is_valid_language(&lang) {
                let c = self.infer_country(&name_lower, &cat_lower, &lang);
                return (lang, c);
            }
        }

        // 2. Check IPTV-org catalog for authoritative mapping
        let norm_key = crate::models::normalize_channel_key(name);
        if let Some(entry) = self.catalog.get(&norm_key) {
            let lang = self.iso_to_language(&entry.country_code.to_lowercase());
            if self.is_valid_language(&lang) {
                return (lang, entry.country_code.clone());
            }
        }

        // 3. Score each language based on positive patterns
        let mut scores: HashMap<String, i32> = HashMap::new();
        for (lang, patterns) in &self.language_patterns {
            let mut score = 0;
            for pattern in patterns {
                if name_lower.contains(pattern) || cat_lower.contains(pattern) {
                    score += 10;
                }
            }
            // Check country hint
            if self.country_matches_language(&country_lower, lang) {
                score += 5;
            }
            if score > 0 {
                scores.insert(lang.clone(), score);
            }
        }

        // 4. Apply exclusion patterns
        for (lang, patterns) in &self.exclusion_patterns {
            for pattern in patterns {
                if name_lower.contains(pattern) || cat_lower.contains(pattern) {
                    scores.remove(lang);
                    break;
                }
            }
        }

        // 5. Pick highest scoring language
        if let Some((best_lang, _)) = scores.into_iter().max_by_key(|(_, s)| *s) {
            let c = self.infer_country(&name_lower, &cat_lower, &best_lang);
            self.supported_languages.insert(best_lang.clone());
            return (best_lang, c);
        }

        // 6. Fallback to country-based inference
        let lang = self.infer_language_from_country(&country_lower);
        let c = self.infer_country(&name_lower, &cat_lower, &lang);
        self.supported_languages.insert(lang.clone());
        (lang, c)
    }

    fn normalize_language(&self, lang: &str) -> String {
        let lower = lang.to_lowercase().trim().to_string();
        match lower.as_str() {
            "eng" | "english" => "English".to_string(),
            "hin" | "hindi" => "Hindi".to_string(),
            "tel" | "telugu" => "Telugu".to_string(),
            "tam" | "tamil" => "Tamil".to_string(),
            "mal" | "malayalam" => "Malayalam".to_string(),
            "kan" | "kannada" => "Kannada".to_string(),
            "ben" | "bengali" | "bangla" => "Bengali".to_string(),
            "mar" | "marathi" => "Marathi".to_string(),
            "guj" | "gujarati" => "Gujarati".to_string(),
            "pan" | "punjabi" => "Punjabi".to_string(),
            "urd" | "urdu" => "Urdu".to_string(),
            "ori" | "odia" | "oriya" => "Odia".to_string(),
            "asm" | "assamese" => "Assamese".to_string(),
            "spa" | "spanish" => "Spanish".to_string(),
            "fra" | "french" => "French".to_string(),
            "deu" | "german" => "German".to_string(),
            "ara" | "arabic" => "Arabic".to_string(),
            "rus" | "russian" => "Russian".to_string(),
            "por" | "portuguese" => "Portuguese".to_string(),
            "ita" | "italian" => "Italian".to_string(),
            "jpn" | "japanese" => "Japanese".to_string(),
            "kor" | "korean" => "Korean".to_string(),
            "zho" | "chinese" => "Chinese".to_string(),
            "tur" | "turkish" => "Turkish".to_string(),
            "fas" | "persian" => "Persian".to_string(),
            "tha" | "thai" => "Thai".to_string(),
            "vie" | "vietnamese" => "Vietnamese".to_string(),
            "ind" | "indonesian" => "Indonesian".to_string(),
            "nld" | "dutch" => "Dutch".to_string(),
            "pol" | "polish" => "Polish".to_string(),
            "ukr" | "ukrainian" => "Ukrainian".to_string(),
            "ron" | "romanian" => "Romanian".to_string(),
            "ell" | "greek" => "Greek".to_string(),
            "heb" | "hebrew" => "Hebrew".to_string(),
            "swe" | "swedish" => "Swedish".to_string(),
            "nor" | "norwegian" => "Norwegian".to_string(),
            "dan" | "danish" => "Danish".to_string(),
            "fin" | "finnish" => "Finnish".to_string(),
            "ces" | "czech" => "Czech".to_string(),
            "hun" | "hungarian" => "Hungarian".to_string(),
            _ => title_case(&lower),
        }
    }

    fn is_valid_language(&self, lang: &str) -> bool {
        self.iso_codes.values().any(|v| *v == lang.to_lowercase())
            || self.language_patterns.contains_key(&lang.to_lowercase())
    }

    fn country_matches_language(&self, country: &str, lang: &str) -> bool {
        match (country, lang) {
            ("in", "hindi")
            | ("in", "telugu")
            | ("in", "tamil")
            | ("in", "malayalam")
            | ("in", "kannada")
            | ("in", "bengali")
            | ("in", "marathi")
            | ("in", "gujarati")
            | ("in", "punjabi")
            | ("in", "urdu")
            | ("in", "odia")
            | ("in", "assamese") => true,
            ("us", "english")
            | ("gb", "english")
            | ("uk", "english")
            | ("ca", "english")
            | ("au", "english") => true,
            ("es", "spanish") | ("mx", "spanish") | ("ar", "spanish") | ("co", "spanish") => true,
            ("fr", "french") | ("be", "french") | ("ch", "french") => true,
            ("de", "german") | ("at", "german") | ("ch", "german") => true,
            ("sa", "arabic") | ("ae", "arabic") | ("eg", "arabic") | ("ma", "arabic") => true,
            ("ru", "russian") | ("by", "russian") | ("kz", "russian") => true,
            ("jp", "japanese") => true,
            ("kr", "korean") => true,
            ("cn", "chinese") | ("tw", "chinese") | ("hk", "chinese") => true,
            _ => false,
        }
    }

    fn infer_country(&self, name: &str, category: &str, lang: &str) -> String {
        // Try to infer country from name/category
        let combined = format!("{} {}", name, category).to_lowercase();

        if combined.contains("india") || combined.contains("indian") {
            return "India".to_string();
        }
        if combined.contains("uk ") || combined.contains("british") || combined.contains("bbc") {
            return "United Kingdom".to_string();
        }
        if combined.contains("us ") || combined.contains("america") || combined.contains("usa") {
            return "United States".to_string();
        }

        // Default by language
        match lang {
            "Hindi" | "Telugu" | "Tamil" | "Malayalam" | "Kannada" | "Bengali" | "Marathi"
            | "Gujarati" | "Punjabi" | "Urdu" | "Odia" | "Assamese" => "India".to_string(),
            "English" => "United States".to_string(),
            "Spanish" => "Spain".to_string(),
            "French" => "France".to_string(),
            "German" => "Germany".to_string(),
            "Arabic" => "Saudi Arabia".to_string(),
            "Russian" => "Russia".to_string(),
            "Japanese" => "Japan".to_string(),
            "Korean" => "South Korea".to_string(),
            "Chinese" => "China".to_string(),
            _ => "Global".to_string(),
        }
    }

    fn infer_language_from_country(&self, country: &str) -> String {
        match country {
            "in" | "india" => "Hindi".to_string(),
            "us" | "united states" => "English".to_string(),
            "gb" | "uk" | "united kingdom" => "English".to_string(),
            "ca" | "canada" => "English".to_string(),
            "au" | "australia" => "English".to_string(),
            "es" | "spain" | "mx" | "mexico" => "Spanish".to_string(),
            "fr" | "france" => "French".to_string(),
            "de" | "germany" => "German".to_string(),
            "sa" | "arabic" => "Arabic".to_string(),
            "ru" | "russia" => "Russian".to_string(),
            "jp" | "japan" => "Japanese".to_string(),
            "kr" | "korea" => "Korean".to_string(),
            "cn" | "china" => "Chinese".to_string(),
            _ => "English".to_string(),
        }
    }

    fn iso_to_language(&self, iso: &str) -> String {
        // This is a simplified mapping - in practice we'd use the full catalog
        match iso {
            "in" => "Hindi".to_string(),
            "us" => "English".to_string(),
            "gb" => "English".to_string(),
            _ => "English".to_string(),
        }
    }

    pub fn supported_languages(&self) -> Vec<String> {
        let mut langs: Vec<String> = self.supported_languages.iter().map(|v| v.clone()).collect();
        langs.sort();
        langs
    }

    pub fn get_iso_code(&self, language: &str) -> String {
        self.iso_codes
            .get(&language.to_lowercase())
            .cloned()
            .unwrap_or_else(|| "und".to_string())
    }
}

fn title_case(s: &str) -> String {
    s.split_whitespace()
        .map(|w| {
            let mut chars = w.chars();
            match chars.next() {
                Some(first) => {
                    first.to_uppercase().collect::<String>() + &chars.as_str().to_lowercase()
                }
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}
