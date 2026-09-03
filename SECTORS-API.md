# Analisa Sectors Financial API (sectors.app)

Diperiksa 3 Sep 2026 (docs + pricing publik). Pendamping [RENCANA-FASE-1.md](RENCANA-FASE-1.md) §1, [LANSKAP-KOMPETITOR.md](LANSKAP-KOMPETITOR.md) §2.

---

## Ringkasan

**Vendor data paling kredibel & terluas di lanskap ini** — tapi harganya kelas "professional data platform" ($59/bln), bukan kelas tool retail.

- Dibuat **Supertype Pte. Ltd. (Singapura)** + PT Supertype Teknologi Nusantara. Perusahaan data betulan, dipakai institusi keuangan & riset akademik. **Coverage IDX 99,99%**, refresh harian.
- API punya **broker summary per emiten dengan klasifikasi cohort (institusi/ritel/campuran) + origin (domestik/asing) sudah jadi** — ini menyelesaikan masalah mapping broker yang tidak bisa dari data gratis.
- Juga: foreign flow, kepemilikan/ownership + analisa kategori investor, insider transaction, corporate filings, financial statements dalam, screener 500+ metrik.
- **API hanya di plan Insider: $59/bln (~Rp950rb) atau $531/thn**, termasuk **5.000 API credits/bln**.
- Plan Standard ($49/bln) punya "Broker analysis (Bandarmologi)" **di dalam app tapi TANPA API**.

**Untuk Fase 1 (alat pribadi, ide belum terbukti):** $59/bln terlalu mahal untuk dikeluarkan sekarang. Validasi dulu pakai data gratis. **Untuk Fase 2 (komersial):** kandidat kuat karena reliabilitas + kedalaman + ToS komersial yang eksplisit — tapi cek dulu syarat redistribusi.

---

## Katalog endpoint (pembacaan mendalam docs.sectors.app, 3 Sep 2026)

### Grup Brokers — inti bandarmologi
| Endpoint | Isi | Kredit |
|---|---|---|
| `GET /v2/broker-summary/{symbol}/top/` | Top net buyer & seller 1 emiten. Per broker: `buy_idr`, `sell_idr`, `net_idr`. **Filter `cohort`** (all/institutional/mixed/retail/unknown) **+ `origin`** (all/domestic/foreign), `n_brokers` s/d 90. Default 30 hari. Agregat lintas hari dalam rentang. | **2** |
| `GET /v2/broker-activity/{broker_code}/` | Semua (saham, hari) 1 broker, s/d 14 hari. Tiap saham: `bfreq/blot/bval/bavg_per_share`, `sfreq/slot/sval`, `nlot/nval`. Filter opsional `symbol`. **Efisien untuk scan seluruh pasar (per-broker, bukan per-saham).** | **1** |
| `GET /v2/foreign-flow/{symbol}/` (Daily Net Foreign Inflow) | Net foreign-broker inflow **dalam IDR** (bukan volume), harian, s/d 90 hari. Positif = broker asing net beli. | **1** |
| Broker Activity Per Symbol | Aktivitas semua broker di 1 saham | ? |
| Top Accumulations & Distributions Per Broker | Ranking akum/distribusi per broker | ? |
| Top Brokers Daily Ranking | Broker teratas harian (universe-wide) | ? |
| `GET /v2/broker/registry` (Broker Registry) | Daftar kode broker + nama | 1 |

> **Penting:** "foreign flow" di sini = klasifikasi **broker asing**, BEDA dengan `ForeignBuy/ForeignSell` IDX gratis yang berbasis **domisili rekening**. Dua metodologi berbeda — jangan dicampur. Sectors kasih **nilai IDR**; IDX gratis cuma volume.

### Grup Detailed Reports — mengganti parsing KSEI manual
| Endpoint | Isi | Kredit |
|---|---|---|
| `GET /v2/company/shareholders-composition/{symbol}/` | **= data kepemilikan bulanan KSEI, sudah terstruktur.** Snapshot **bulanan** per kategori investor (asuransi, korporat, dapen, lembaga keuangan, individu, reksadana, sekuritas, yayasan, lainnya) × **lokal (`_l`) / asing (`_f`)** + `numbers_of_shareholders` + `change_in_shareholders`. **Data sejak 2021.** 1 call = 1 tahun snapshot bulanan 1 ticker. | **1** |
| `GET /v2/company/corporate-actions/{symbol}/` | **Semua riwayat corporate action** 1 emiten: stock split, right issue, warrant, bonus, AGM, dividen (upcoming + historis). | **1** |
| Company Report / Quarterly Financials / Revenue Segments | Fundamental dalam | 1 |
| `GET /v2/company/shareholders-composition` juga ada versi board & ownership (management) | Direksi/komisaris + pemegang saham | 1 |

### Grup Transaction Data
| Endpoint | Isi | Kredit |
|---|---|---|
| `GET /v2/daily/{symbol}/` (Daily Transaction Data) | OHLCV + `market_cap` per emiten, s/d 90 hari. **Tanpa foreign flow, tanpa split papan** (itu di endpoint lain). | **1** |
| `GET /v2/close/` (Daily Full-Universe Close) | Close semua ~950 emiten 1 hari, paginated (limit 30/page → ~32 page). | **~32/hari** |
| `GET /v2/index-daily/{index_code}/` (Index Daily) | Close indeks (IHSG/LQ45/sektoral), **data sejak 2 Jan 2019**, s/d 90 hari. | **1** |
| IDX Market Summary | Rekap pasar harian | 1 |

### Lainnya
Company Screener (`/v2/companies/` — 500+ metrik, `where`/`order_by` = 1 kr, NL `q` = 3 kr) · Helper Lists (industries/subsectors/news-tags) · Rankings (Top Movers, Most Traded) · IPO & Performance · News & Filings · **MCP server resmi** (bisa colok ke Claude/ChatGPT untuk query data percakapan).

**"Zero-sum trap"** (recipe mereka sendiri): jangan jumlahkan semua `net_idr` buyer + seller → selalu ~0. Vendor ini paham betul — konsisten dengan peringatan di [IDXALPHA-TEARDOWN.md](IDXALPHA-TEARDOWN.md) & [PRINSIP-DATA.md](PRINSIP-DATA.md) §B.

---

## Harga & kalkulasi kredit untuk Fase 1

| Plan | Harga | API |
|---|---|---|
| Forever Free | $0 | ❌ (app: top 3 broker saja) |
| Standard | **$49/bln** / $490/thn | ❌ (app: bandarmologi penuh, ownership, insider) |
| **Insider** | **$59/bln** / **$531/thn** | ✅ **5.000 credits/bln** + Workflow + screener |

**Apakah 5.000 kredit/bln cukup?** Sangat cukup untuk pemakaian pribadi — kredit kebanyakan **1** per call:
- Scan seluruh pasar via `/broker-activity/{broker}/` — ~40 broker × 22 hari = **~880 kr/bln**
- Foreign flow (IDR) watchlist 15 emiten, window 90 hari, refresh mingguan — 15 × 4 = **~60 kr/bln**
- Kepemilikan bulanan `shareholders-composition` — 250 emiten × 1 (per tahun) refresh bulanan = **~250 kr/bln**
- Corporate actions refresh — 250 × 1, bulanan = **~250 kr/bln**
- Harga: pakai **IDX Stock Summary gratis** (jangan buang kredit), atau `/close/` full-universe ~32/hari = ~700/bln
- **Total realistis: < 2.000 kr/bln.** Ruang lega.
- **Backfill sekali** kepemilikan sejak 2021: 250 × 5 tahun = 1.250 kr (satu bulan sekali habiskan untuk ini).

Fase 2 (banyak user, query kustom, full-market harian) → tembus 5.000 cepat → Enterprise (harga custom).

---

## Sectors vs alternatif

| | **Sectors Insider** | **Index Alpha Starter** | **Data gratis** (IDX Stock Summary + KSEI) |
|---|---|---|---|
| Harga | $59/bln (~Rp950rb) | Rp200rb/bln | Rp0 |
| Broker summary per emiten | ✅ **+ klasifikasi cohort/origin bawaan** | ✅ mentah (Anda klasifikasi sendiri) | ❌ |
| Foreign flow | ✅ **nilai IDR** (broker asing) | ✅ | ✅ **volume saja** (domisili rekening, flag IDX) |
| Kepemilikan bulanan KSEI | ✅ **terstruktur, per kategori L/F, + holder count, sejak 2021** | ❌ | ✅ tapi **parse XLSX sendiri** |
| Corporate actions | ✅ **per ticker, siap pakai** | ❌ | ✅ tapi parse XLS KSEI sendiri |
| Insider txn / corp filings | ✅ | ❌ | ⚠️ (disclosure IDX, parse sendiri) |
| Financial statements | ✅ dalam | ❌ | ❌ |
| Kematangan vendor | **Tinggi** (Supertype, dipakai institusi) | Kecil (`@neon_scale`) | Resmi (IDX/KSEI) |
| Kedalaman histori | **?** (contoh dok: Mei 2025 — perlu konfirmasi) | 2025-01-01 | bertahun (IDX) / bulanan (KSEI) |
| Limit | 5.000 kr/bln | 25.000 req/bln | rate-limit sopan sendiri |
| Cocok untuk | Fase 2 / kalau serius & butuh breadth | Fase 1.5 (broker-level murah) | **Fase 1 (validasi)** |

---

## Yang bermanfaat (jawaban pertanyaan Anda)

Pembacaan mendalam ini mengubah posisi Sectors: **satu langganan Insider bisa mengganti 3 hal sekaligus** —
1. beli broker summary (yang tadinya Index Alpha),
2. parsing file XLSX kepemilikan KSEI manual (`shareholders-composition` sudah terstruktur, + holder count),
3. parsing XLS corporate action KSEI (`corporate-actions` per ticker).

Plus foreign flow **dalam IDR** (bukan cuma volume seperti IDX gratis), indeks sektoral sejak 2019, dan financial statements. Untuk **Fase 2**, ini konsolidasi yang rapi — lebih sedikit adapter, satu vendor kredibel, satu tagihan.

**Un-paintable check (PRINSIP-DATA §B):** `numbers_of_shareholders` + `change_in_shareholders` dari `shareholders-composition` = persis sinyal "jumlah pemegang tercatat" yang sulit dipalsukan. Bagus.

## Fase 1 vs Fase 2

**Fase 1 (sekarang):** tetap **jangan** ambil Sectors. $59/bln untuk ide belum terbukti = bakar uang. Jalankan rencana gratis (IDX Stock Summary + KSEI), forward-test 2 bulan.

**Fase 1.5 (kalau broker-level terbukti perlu):** pilihan jadi lebih jelas —
- **Index Alpha Rp200rb** kalau CUMA butuh broker summary + foreign flow, dan Anda tetap parse KSEI sendiri.
- **Sectors Insider $59** kalau mau broker summary + KSEI ownership + corp actions + fundamental dalam satu tempat (dan siap Fase 2). Selisih harga ~Rp750rb/bln dibayar oleh: tidak perlu bangun & rawat parser KSEI, klasifikasi cohort sudah jadi, vendor mapan.

**Fase 2 (komersial):** Sectors kandidat utama — reliabilitas + breadth + konsolidasi + ToS komersial eksplisit ("Commercial Use" FAQ). Wajib baca: boleh redistribusi metrik turunan ke pelanggan berbayar Anda?

---

## Yang WAJIB dikonfirmasi sebelum pakai (butuh akun/tanya `help@sectors.app`)

1. **Kedalaman histori broker summary & foreign flow** — sampai tanggal berapa mundur? (Kritis untuk backtest. Semua contoh dok = Mei 2025; bandingkan: indeks sejak 2019, kepemilikan sejak 2021 — broker-level mungkin lebih pendek.)
2. **Jam update EOD** — "refreshed daily" jam berapa WIB? (untuk jadwal cron)
3. **Rate limit** (request/menit) di plan Insider.
4. **Dasar klasifikasi `cohort`** (institutional/retail/mixed) — statis per broker atau dinamis? Metodologinya apa? (menentukan seberapa Anda percaya sinyal "smart money vs ritel"-nya)
5. **Syarat Commercial Use** — boleh redistribusi data turunan di produk langganan? Ada batasan jumlah end-user?
6. **Kelebihan kredit** — apa yang terjadi kalau tembus 5.000/bln? Bisa top-up atau langsung Enterprise?

---

## Rekomendasi

- **Sekarang:** Sectors = **opsi Fase 1.5 / Fase 2 terkuat** (bukan cuma Fase 2). Jangan bayar dulu.
- **Fase 1:** data gratis (rencana yang sudah ada — IDX Stock Summary + KSEI).
- **Fase 1.5:** kalau butuh **hanya** broker summary → Index Alpha Rp200rb. Kalau butuh broker summary **+ KSEI ownership + corp actions + fundamental jadi satu** → langsung Sectors Insider (nilai konsolidasinya sepadan).
- **Sebelum komit ke Sectors:** buka Standard $49 **1 bulan** untuk eksplorasi data di app + `mcp` di Claude, lalu email `help@sectors.app` dengan 6 pertanyaan di atas (terutama kedalaman histori broker & syarat commercial-use). Upgrade ke Insider kalau cocok.
