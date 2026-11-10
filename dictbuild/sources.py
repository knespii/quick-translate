"""Download sources for the dictionary build. Each source lists mirrors in order of preference."""

SOURCES = [
    {
        "name": "gnu-en-cs",
        "file": "gnu-en-cs.txt",
        "license": "GFDL 1.1+ (svobodneslovniky.cz)",
        "mirrors": [
            # svobodneslovniky.cz is IPv6-only, so the Wayback copy of the 2025-01-24 release comes first.
            {"url": "https://web.archive.org/web/20250124045639id_/https://www.svobodneslovniky.cz/data/en-cs.txt.gz",
             "gzip": True,
             "sha256": "9fc6b797555baae24ab39b37105eee6eb06da7e0bc93136d7fd13746967a3d9c",
             "sha256_unpacked": "1e1f78dc2da954a9e404463e1e8f9f41080a8ee2e0ecca4a07006ce96bd79747"},
            {"url": "https://www.svobodneslovniky.cz/data/en-cs.txt.gz", "gzip": True},
            {"url": "https://raw.githubusercontent.com/lehrbuch/svobodneslovniky/master/en-cs/en-cs.txt",
             "sha256": "299c47d1b355f3390d9e45da16884f9fb11fde5a5f2780a5f7860ee3bddc9445"},
            {"url": "https://sources.debian.org/data/non-free/s/stardict-english-czech/20210401-1/en-cs.txt"},
        ],
    },
    {
        "name": "kaikki-czech",
        "file": "kaikki-czech.jsonl.gz",
        "license": "CC BY-SA 4.0 / GFDL (English Wiktionary via kaikki.org)",
        "mirrors": [{"url": "https://kaikki.org/dictionary/Czech/kaikki.org-dictionary-Czech.jsonl.gz"}],
    },
    {
        "name": "kaikki-cs-extract",
        "file": "kaikki-cs-extract.jsonl.gz",
        "license": "CC BY-SA 4.0 / GFDL (Czech Wiktionary via kaikki.org)",
        "mirrors": [{"url": "https://kaikki.org/dictionary/downloads/cs/cs-extract.jsonl.gz"}],
    },
    {
        "name": "syn2015-lemma",
        "file": "syn2015_lemma_utf8.zip",
        "license": "CC BY 4.0 (Český národní korpus)",
        "mirrors": [{"url": "https://wiki.korpus.cz/lib/exe/fetch.php/seznamy:syn2015_lemma_utf8.zip"}],
    },
    {
        "name": "syn2015-word",
        "file": "syn2015_word_utf8.zip",
        "license": "CC BY 4.0 (Český národní korpus)",
        "mirrors": [{"url": "https://wiki.korpus.cz/lib/exe/fetch.php/seznamy:syn2015_word_utf8.zip"}],
    },
    {
        "name": "freq-en",
        "file": "en_full.txt",
        "license": "CC BY-SA 4.0 (hermitdave/FrequencyWords, OpenSubtitles 2018)",
        "mirrors": [{"url": "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/en/en_full.txt"}],
    },
    {
        "name": "oewn-2025",
        "file": "english-wordnet-2025.xml.gz",
        "license": "CC BY 4.0 (Open English WordNet)",
        "optional": True,
        "mirrors": [{"url": "https://github.com/globalwordnet/english-wordnet/releases/download/2025-edition/english-wordnet-2025.xml.gz"}],
    },
]
