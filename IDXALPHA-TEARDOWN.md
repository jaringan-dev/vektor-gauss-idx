# Teardown IDXAlpha — Fitur: Valid atau Gimmick?

Diperiksa 3 Sep 2026 lewat sesi login Anda (`idxalpha.com/dashboard`). Data platform bertanggal **IDX · 02 Sep 2026 19:04 WIB** (EOD, hari bursa sebelumnya). Read-only.
Pendamping [LANSKAP-KOMPETITOR.md](LANSKAP-KOMPETITOR.md).

---

## Verdict singkat

**~70% substansi nyata, ~30% basi / placeholder / overclaim.**

- **Analitik aliran dana (broker flow) = nyata & dikerjakan dengan baik.** Screener, Market Pulse, Broker Activity, Live Flow Feed, Position Tracker — semua komputasi sungguhan di atas broker summary EOD (+ sedikit intraday).
- **Kejujuran intelektualnya luar biasa** untuk pasar ini: sistem sinyal 2-lapis (berbobot vs "riset/masa percobaan bobot 0"), backtest negatif dipublikasikan, tag "un-paintable", analisis IC vs biaya transaksi yang blak-blakan.
- **TAPI:** seluruh lapisan **kepemilikan / korporat / insider — yang jadi jualan utama di landing page — praktis MATI.** Sinyal order-flow (maker/taker) melampaui data yang mereka punya. Dan skor komposit **tidak terbukti** punya edge setelah biaya.

**Untuk Anda:** gratis, jadi *fair value*. Berguna sebagai **konfirmasi aliran dana**, bukan sebagai mesin keputusan. Jangan trading dari skornya saja. "KSEI ownership + insider alert" yang mereka iklankan = **vaporware saat ini**.

---

## Yang NYATA & bagus

### Screener (skor komposit −2..+2)
215 saham diperingkat. Kolom: Score, Δ (+ transisi rating, mis. "BUY → STRONG BUY"), Rating (STRONG BUY/BUY/WATCH/NEUTRAL/AVOID), Inst. Flow (Heavy Accum → Heavy Distrib), Valuation (CHEAP/FAIR/EXPENSIVE), 1D/1W %, sparkline, Vol Surge (×), 13-kotak indikator. Update EOD. **Komputasi nyata.**

### Stock detail — rincian skor (contoh BBRI +1.480)
**~14 sinyal berbobot** menggerakkan skor:
| Sinyal | Bobot |
|---|---|
| Broker Accum (top-5) | 12% |
| Broker Avg Cost vs Price | 12% |
| Retail FOMO Detector | 12% |
| Tier-1 Institutional Flow ("Big Dog") | 10% |
| Maker/Taker Volume Delta | 10% |
| Concentrated Buyer Identity | 8% |
| Volume Profile | 8% |
| OBV Divergence | 8% |
| Relative Strength vs IHSG | 8% |
| Trade-Size / Ticket Fingerprint | 5% |
| Exchange Retail Participation (split D/F resmi IDX) | 3% |
| Bandar Reversal (flip seller→buyer) | 2% |
| Float Accumulation Ratio | 1% |
| Foreign Net Flow | 1% |

**+ ~12 "Research Signals" berbobot 0.00** (BCR, Patterns, Compression, NG Cross, Holders, KSEI Rotation, Insider Cluster, Syndicate, Operator P&L, Aggression, Ticket Skew, Whale Print) — *"Computed and shown, weighted 0.00 — here to be judged before they earn weight."*

Fitur pendukung yang solid:
- **Institutional Position Tracker** — estimasi entry/durasi/P&L/conviction tiap broker dari histori flow 260 hari. Dilabeli jujur: *"⚠️ Estimated ... Not actual custodian data."*
- **Broker Summary lengkap** per timeframe (1D–1Y) dengan lots, value, freq, VWAP, **avg ticket** (pembeda positioning vs churn), tier T1/T2/Retail.
- **Registered Holders 21 bulan** — grafik jumlah pemegang dari data BAE/KSEI. Dilabeli *"un-paintable — broker crossing dengan dirinya sendiri tidak mengubah register."*
- Key levels S/R + VWAP, fundamental (P/E, P/B, ROE, D/E).

### IDX Market Pulse
Tide score institusi seluruh pasar, Risk Regime, **"Is it safe to trade right now?"** (dispersi cross-section vs biaya round-trip ~1,67%). Sector flow breakdown, Unusual Activity Feed (T1 selling, FOMO trap, foreign flip, BCR spike).
**Kejujuran:** *"Measured signal ICs in this product are 0.02–0.09 against a break-even near 0.19 implied by ~150–190bp round-trip costs. Use it to size, not to predict."* — mereka bilang sendiri edge-nya di bawah ambang biaya.

### Broker Activity
83 broker aktif diperingkat net lots universe-wide, window 1D/1W/1M. Nama broker benar & termapping (KZ=CLSA, BK=JPM, ZP=Maybank, AK=UBS, RX=Macquarie, XL=Stockbit Sekuritas Digital…). Tier T1 (asing bulge-bracket) / T2 (lokal besar). Kolom "Buy Breadth %". **Agregasi broksum yang bersih.**

### Live Flow Feed
60 event/hari: New T1 Entry, BCR Spike (konsentrasi hari ini vs baseline 2W), Retail FOMO (ritel flip 2W-jual → hari-ini-beli), Foreign Inflow (≥2 sesi beruntun). Definisi jelas, dari broksum. **Nyata.**

### Pattern Scanner — jujur soal ketidakbergunaannya
Deteksi 168 pola (Cup&Handle, VCP, H&S, Wyckoff Accum…) di-grade A+..B. Disertai disclaimer eksplisit:
> *"The first forward test (2026-08-31) found the ladder does not order returns... **A+ returned 0.00% excess at 10 days**... Overall, patterns showed **no measurable edge** at 5, 10 or 20 days. Bullish patterns **underperformed** at every horizon. **Patterns carry weight 0.00 in the composite score.** Read them as a description of shape, not a forecast."*

Ini **bukan gimmick** — ini fitur yang diuji, terbukti tidak prediktif, lalu dipertahankan sebagai deskriptor bentuk dengan label yang sangat jelas. Langka.

---

## Yang BASI / PLACEHOLDER / GIMMICK

### 1. Ownership Intel ("UBO Intelligence") — praktis MATI ⚠️
- Header: **"T2: 2026-02-27 · No T1"** — datanya **snapshot tunggal 27 Feb 2026**, basi 6+ bulan.
- Sub-tab **"Insider Txn"** = placeholder yang belum di-deploy. Teks aslinya:
  > *"To enable this tab: Deploy the IDX transaction scraper agent and set `JOURNAL_WEBHOOK_URL` on the server... Rows shown above are **sample/placeholder data only**."*
- Sub-tab **"Smart Money Delta"** = tidak berfungsi: *"Two periods needed for delta. Currently only one period is loaded: 2026-02-27."*
- Yang berfungsi (Overview, Owner Search, Conglomerate map, "Legal Risk — Kejaksaan ownership") = **berjalan di atas data Feb 2026**.

→ Landing page menjual *"955+ Data Kepemilikan KSEI"*, *"insider transaction alerts"*, *"beneficial ownership"* sebagai fitur inti. **Kenyataannya: snapshot statik lama + 2 sub-fitur rusak.**

### 2. Corp Intel — pipeline sync tidak jalan
- "Alerts": *"No change alerts in the last 14 days."*
- "Shareholders": *"No shareholder changes in the last 30 days."*
Untuk 215+ emiten selama 30 hari, nol perubahan = **daily sync-nya memang tidak berjalan**, bukan pasar yang sepi.

### 3. Maker/Taker / CVD / "Aggression" — melampaui data yang ada
Broker summary EOD IDX **tidak memuat** data agresor order (maker/taker, HAKA/HAKI). Pengakuan mereka sendiri di sinyal "Aggression":
> *"Inferred aggression **invalidated** against native HAKA/HAKI (r=−0.404, 40% sign agreement) — needs momentum-chart data"*

Tapi **"Vol Delta" tetap berbobot 10%** dan breakdown maker/taker ditampilkan dengan presisi palsu ("Taker Buy 9,96M / Taker Sell 2,11M → Buy ratio 82,5%") — padahal sebagian **tautologis** (Taker Buy % = Maker Sell %, Net taker = Net maker). Ini bagian **paling gimmick**.

### 4. Foreign flow sebagian basi
Market Pulse: *"Foreign Net Lots... only 10 sessions of data (2026-08-10 to 2026-08-24)"* — ~2 minggu lag. Tidak konsisten dengan halaman saham (yang tampilkan 10 hari terakhir + streak 3 hari).

### 5. Metadata bolong
Puluhan ticker tampil *"(unknown)"* untuk nama perusahaan (PRDA, TOBA, MAPA, BNGA, dst.) — data referensi tidak lengkap.

### 6. "AI Analysis" (deepseek-v4-flash) — kadang sampah
Teks LLM yang garbled masuk produksi: *"5B net from small-ticket sellers. 84T, a classic distribution tell."* Nilai tambah rendah, lebih hiasan.

### 7. Skor komposit belum terbukti punya edge
Dari pengakuan mereka: IC komponen **0,02–0,09** vs break-even **~0,19** setelah biaya. Skornya **mendeskripsikan** kondisi aliran dana institusi dengan baik — tapi **tidak ditunjukkan memprediksi return** setelah ongkos transaksi.

---

## Arsitektur (tersirat dari UI)

"refreshed each build", "one API call per session then cached permanently", "IDX transaction scraper agent... inject data at each rebuild", webhook. → **Scraper + static-site generator**, bukan backend live. Menjelaskan pola basi: modul yang scraper-nya jalan = segar (broksum), yang tidak = beku (ownership sejak Feb). Kemungkinan dev solo/kecil.

---

## Pelajaran untuk Vektor Gauss

**Tiru:**
- Sistem sinyal **2-lapis**: sinyal berbobot (sudah tervalidasi) vs "masa percobaan bobot 0" (ditampilkan, dinilai dulu). Disiplin.
- **Forward-test tiap sinyal, publikasikan hasil negatif.** Set bobot 0 kalau tidak jalan.
- Tag **"un-paintable"** untuk sinyal yang tak bisa dimanipulasi cross-broker (split D/F resmi IDX, jumlah holder, register).
- Peringatkan **zero-sum double-counting** (net ritel = −net non-ritel, itu satu observasi bukan dua).
- Analisis **IC vs biaya transaksi** — ukur edge dalam satuan yang jujur.

**Hindari:**
- Menampilkan modul basi seolah hidup (ownership stuck 6 bulan).
- Menyisakan placeholder + instruksi dev di produksi.
- **Menginferensi data yang tidak Anda punya** (maker/taker dari EOD broksum) lalu memberinya bobot.
- LLM slop tanpa QA.
- Menjanjikan "KSEI ownership + insider alert" kalau pipeline-nya belum jalan.

**Insight strategis:** IDXAlpha = versi paling matang & jujur dari konsep "Vektor Gauss" — **gratis** — dan **tetap tidak bisa menunjukkan edge setelah biaya.** Itu sinyal kuat bahwa nilai produk sejenis ada di **disiplin proses + kejujuran + kecepatan/UX**, bukan di "punya skor bandarmologi". Untuk alat pribadi Anda: pakai kerangka 2-lapis + forward-test dari hari 1, dan ukur apakah tag Anda benar-benar mendahului pergerakan — persis yang IDXAlpha lakukan dan berani akui hasilnya tipis.
