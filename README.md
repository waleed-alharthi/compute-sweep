# fp4sweep

Crawls the GPU/AI-box markets for the best price per VRAM GB among hardware
with an FP4 path and >= 24 GB (catalog.yaml decides what counts: native NVFP4
Blackwell, Tenstorrent bfp4, Huawei MXFP4, and software-FP4 Apple flagged
`sw`). One crawl per hour via a systemd user timer; exports a JSON the
clidash FP4 MARKET panel reads.

    python3 -m fp4sweep.run crawl [--only opensooq,haraj,ebay,dubizzle,discovery,retail]
    python3 -m fp4sweep.run export
    python3 -m fp4sweep.run status

Store: ~/.local/state/fp4sweep/market.db (listings + price_events + fx)
Export: ~/.local/state/fp4sweep/market.json

Sources: OpenSooq (all GCC domains, __NEXT_DATA__ products), Haraj
(browserless), dubizzle (SERP snippets, AED-marker required), eBay
(search pages via the fetch ladder; Browse API when keys exist - drop
`{"app_id": "...", "cert_id": "..."}` into ~/.config/fp4sweep/ebay.json and
the next crawl uses the official API instead of scraping), a SearXNG
discovery loop that loads every unknown domain a catalog-term query finds,
and curated retail pages (Tenstorrent, ComputaHardware).

API-gated sources (silent until keys exist): eBay Browse API reads
~/.config/fp4sweep/ebay.json `{"app_id": "...", "cert_id": "..."}`;
AliExpress affiliate search reads ~/.config/fp4sweep/aliexpress.json
`{"app_key": "...", "app_secret": "..."}` (optional `gateway` and
`sign_method` keys; default gateway api-sg.aliexpress.com, md5 signing).

Evidence levels travel with every row: `direct`, `browser`, `serp` - a
snippet-price is never presented as a page-price.

Deploy (kaiju, user units):
    cp systemd/fp4sweep.{service,timer} ~/.config/systemd/user/
    systemctl --user daemon-reload && systemctl --user enable --now fp4sweep.timer
