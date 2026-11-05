# Rencana Fase 1 — Alat Internal + Telegram Alert (revisi 3, 5 Nov 2026)

Keputusan: alat pribadi untuk trading Anda sendiri, alert Telegram. Fase 2 (mungkin) = langganan/dijual.
Jawaban Anda: sumber data harus **gratis & reliable** + **resmi, hindari sumber yang bisa dimanipulasi**; **perhalus rencana dulu** sebelum scaffold.

Pendamping: [PRINSIP-DATA.md](PRINSIP-DATA.md) *(mengikat — baca dulu)* · [ANALISIS-VALIDASI.md](ANALISIS-VALIDASI.md) · [INVENTARIS-SUMBER-DATA.md](INVENTARIS-SUMBER-DATA.md) · [LANSKAP-KOMPETITOR.md](LANSKAP-KOMPETITOR.md) · [IDXALPHA-TEARDOWN.md](IDXALPHA-TEARDOWN.md) · [SECTORS-API.md](SECTORS-API.md) · [HANDOFF.md](HANDOFF.md) · [RENCANA-MIGRASI-VPS.md](RENCANA-MIGRASI-VPS.md).

> **Revisi 3 (5 Nov 2026).** Revisi 2 ditulis sebelum ada kode: "100% Tier 0, tanpa broker summary, di VPS, masih tanpa kode". Sekarang Fase 1 **sudah berjalan di produksi** dan salah satu asumsi intinya berubah: sebuah feed Tier 3 gratis (`vektorgauss.py`) terpasang untuk data per-broker. Bagian **§0** menuliskan keadaan sebenarnya; bagian lain yang asumsinya sudah tidak berlaku diberi tanda **[revisi 3]** dan teks lamanya sengaja dibiarkan supaya jejak keputusannya terbaca.

> **Prinsip data ([PRINSIP-DATA.md](PRINSIP-DATA.md)):** inti Fase 1 tetap Tier 0 (IDX Stock Summary + KSEI resmi), dan foreign flow dihitung IDX sendiri. **[revisi 3]** Data per-broker datang dari feed Tier 3 — di tingkat yang menurut PRINSIP-DATA "tidak untuk produksi" — jadi ia hanya boleh hidup di **lapis masa-percobaan (bobot 0)**, tidak pernah menimpa data Tier 0, dan semua turunannya wajib lolos forward-test sebelum menggerakkan keputusan. Metodologi tetap condong ke sinyal yang bandar tidak bisa palsukan.

---

## 0. Keadaan nyata per 5 Nov 2026 (revisi 3)

### Apa yang berjalan, dan di mana

| Hal | Revisi 2 (rencana) | Kenyataan |
|---|---|---|
| Tempat | VPS ~$5/bln | **PC rumah Windows → WSL2 Ubuntu.** Alasan: IDX di belakang Cloudflare dan hanya melepas IP residensial; ASN cloud ditolak (1020). Pindah ke VPS ISP Indonesia = [RENCANA-MIGRASI-VPS.md](RENCANA-MIGRASI-VPS.md) |
| Penjadwal | cron | **Windows Task Scheduler**: `VektorGauss-EOD` 19:15 WIB, `VektorGauss-DeepBackfill` 00:10, `VektorGauss-Status` 08:00. Runner GitHub Actions self-hosted dicoba dan gagal (VM WSL tidak hidup di antara sesi `wsl.exe`) |
| Pengumpulan EOD (`scripts/eod.sh`) | satu fetch IDX | harga + indeks + price-history + disclosure + trading halt + deteksi corp action → `quality-gate` → `compute` (pass 1, menghasilkan kandidat radar) → **broker dari feed** (menunggu sampai 60 menit, `VG_FEED_WAIT_MINUTES`, sampai feed menerbitkan tabel broker hari itu) → `compute` (pass 2, sinyal broker masuk) → inventory → `backfill-outcomes` → `paper-update` → digest + status Telegram. KSEI bulanan dan corp action hanya pada slot awal bulan |
| UI | "Telegram = satu-satunya UI" | digest Telegram **dan** dashboard lokal FastAPI (`vg serve`: pelacak forward-test, screener, ranking, coverage, health, risk-test) |
| Forward-test | mulai hari pertama | berjalan; jam forward `VG_FORWARD_SINCE=2026-09-04`; `signal_log` memuat 26 tag (backfill in-sample sejak 2020-01-02 + sinyal maju) |
| Hasil riset 3,5 tahun | — | foreign flow IDX + kepemilikan bulanan KSEI saja **bukan** edge yang bisa diperdagangkan; kandidat yang sedang diuji ada di [HANDOFF.md](HANDOFF.md). Tidak ada tag di lapis berbobot |

### Feed Tier 3 (`src/vg/sources/vektorgauss.py`)

Vendor adalah pengembang tunggal ("IDX EDGE PRO", freemium); di kode, DB, dan output ia sengaja hanya disebut `vektorgauss*` — endpoint dan kunci hanya ada di `.env` (`VG_FEED_BASE_URL`, `VG_FEED_API_KEY`). Statusnya **Tier 3**: sumber tidak diungkap, bus-factor tinggi. Kalau ia mati, harga/sektor/kepemilikan tetap utuh karena Tier 0; broker/inventory/done adalah milik feed saja, cadangannya Sectors.app (~$59/bln) atau IDX Equity & Derivative Log Data (~Rp3 juta/bln) — keputusan terbuka. Setiap respons mentah diarsipkan di `raw_artifacts` (+SHA256), jadi data yang sudah terkumpul selamat walau feed hilang.

| Tabel | Isi | Cakupan (DB produksi, 5 Nov 2026) |
|---|---|---|
| `price_bars` | OHLCV + foreign flow. 1.394.759 baris Tier 0 (IDX); feed hanya **menambal** 2.886 baris yang kosong, dijaga `source_tier` agar tak pernah menimpa Tier 0 | 2020-01-02 → 2026-11-05, 989 ticker, 1.397.645 baris |
| `broker_flows` | per broker: beli/jual/frekuensi/net. **Hanya 20 pembeli bersih teratas** per ticker-hari (penjual bersih tidak ada) | 2025-12-08 → 2026-11-05, 217 hari, 952 ticker, 1.091.879 baris |
| `broker_levels` | tangga **25 pembeli bruto + 25 penjual bruto** per ticker-hari; ditambah setelah revisi 2 | rentang sama, 869 ticker, 2.397.729 baris |
| `broker_inventory` | net harian + inventori berjalan (`cum_net_val`) per broker | 2026-06-03 → 2026-11-05, **5 ticker**, 4.981 baris |
| `done_by_price` | tick done-detail diagregasi ke level harga + agresor HAKA/HAKI + split asing | **1 hari (24 Sep 2026), BBCA saja, 6 baris** — mahal (~130 halaman/saham/hari), hanya on-demand |

Konsekuensi yang harus diingat saat membaca angka broker:
- `broker_flows` saja **tidak bisa menunjukkan penjualan bersih** (hanya pembeli bersih yang masuk). Net per kelompok broker memakai **batas pasti** (`src/vg/broker_bounds.py`): `broker_flows` + `broker_levels` memberi interval `[lo, hi]` yang pasti, dan tag baru menyala hanya bila seluruh interval melewati ambang.
- Satuan sudah dicek pada data hidup: 25 pembeli terbesar bila dijumlah = 95–99% `price_bars.value` dan tak pernah melebihinya, jadi "porsi dari nilai hari itu" terdefinisi.
- Klasifikasi broker (`FOREIGN_BROKERS`, `RETAIL_BROKERS`) adalah **peta statis buatan sendiri**, bukan fakta dari feed. Semua yang dibangun di atasnya adalah inferensi.

### Lapisan validasi data (commit `0d3a07e`, belum di-deploy per tanggal ini)

`src/vg/config.py` kini `pydantic-settings` (nilai `.env` produksi diverifikasi identik dengan kode lama); `src/vg/schemas.py` memvalidasi tiap baris `price_bars`/`broker_flows`/`broker_inventory`/`done_by_price` sebelum `INSERT … ON CONFLICT`. Baris buruk ditolak satu per satu dan dilaporkan di `run_log.error`; bila >10% batch ditolak, tidak ada yang ditulis. Seluruh 2,49 juta baris produksi lolos tanpa penolakan. Temuan: IDX memakai `first_trade = -99999999` sebagai penanda "belum ada transaksi pertama" — data sah, disimpan apa adanya.

### Mesin skoring broker (`src/vg/engine/scoring.py`) — probation, belum terhubung ke `compute`

Tiga ukuran yang bisa dijelaskan dengan satu rumus; tanpa bobot hasil belajar, tanpa skor 0–100, tanpa kata beli/jual. Detail rumus ada di §5 ("Komponen broker").

---

## 1. Kenyataan pahit soal "broker summary gratis & reliable"

**Tidak ada.** Ini harus lurus sebelum lanjut:

| Sumber | Broker summary per emiten? | Gratis? | Reliable? |
|---|---|---|---|
| **IDX Stock Summary** (endpoint publik resmi) | ❌ (hanya **net foreign flow** + OHLCV + papan RG/NG) | ✅ | ✅ (resmi IDX) |
| **KSEI** (kepemilikan bulanan, corp action) | — (kepemilikan, bukan flow) | ✅ | ✅ (resmi KSEI) |
| **Index Alpha** free tier | ✅ | "gratis" tapi **5 ticker/hari** (batch = 1 unit/ticker) → **tidak cukup untuk watchlist** | vendor kecil, limit bisa berubah |
| **Index Alpha** Starter | ✅ (250 ticker/hari muat di 25rb req/bln) | ❌ **Rp200rb/bln** | sedang (vendor kecil, data sejak 2025-01-01 saja) |
| **Stockbit Pro** | ✅ | ❌ Rp200rb/bln | ✅ tapi tanpa API resmi → harus scrape (melanggar ToS, rapuh) |
| **Scrape RTI / app sekuritas** | ✅ | "gratis" | ❌ **tidak reliable** (ToS + sering rusak + risiko blokir) |

**Kesimpulan:** kalau syaratnya **gratis DAN reliable**, Fase 1 **tidak pakai broker summary**. Ia dibangun di atas **IDX Stock Summary + KSEI** saja.

> **[revisi 3]** Kesimpulan itu masih benar untuk *reliable*: tidak ada sumber broker gratis yang reliabel. Yang berubah: pada 27 Sep 2026 ditemukan feed Tier 3 gratis yang memberi broker summary, dan ia dipasang **sebagai lapis percobaan, bukan sumber produksi** (§0). Ia gratis, tetapi tidak resmi dan bergantung pada satu pengembang — jadi baris tabel "Scrape RTI / app sekuritas" di atas berlaku juga untuknya: tidak reliable, hanya boleh menghasilkan sinyal bobot 0.

### Apakah ini cukup?

Cukup untuk MVP, dan mungkin tidak sekehilangan yang dikira:
- **Net foreign flow adalah salah satu sinyal yang paling didukung bukti.** IDXAlpha sendiri (yang punya data broker lengkap) mengakui IC sinyal broker-level cuma **0,02–0,09** — marginal setelah biaya. Sinyal turunan-broker (CR3, mapping, Sultan/Zombi) belum tentu menambah edge nyata.
- Yang hilang tanpa broker summary: rasio konsentrasi (CR3), "broker mana", pemisahan institusi-lokal vs ritel via kode broker. Anda **tetap** dapat asing vs non-asing (dari flag foreign di Stock Summary) dan tren kepemilikan institusi (dari KSEI bulanan).

### Opsi kalau nanti mau broker-level

- **Index Alpha free tier untuk ≤5 saham inti** (posisi yang Anda pegang) — sah, gratis, 5 ticker/hari pas. Bolt-on kecil.
- **Index Alpha Starter Rp200rb/bln** kalau CUMA butuh broker summary + foreign flow (Anda tetap parse KSEI sendiri).
- **Sectors Insider $59/bln** kalau butuh broker summary + kepemilikan KSEI + corp actions + fundamental **dalam satu vendor** — lihat [SECTORS-API.md](SECTORS-API.md). Lebih mahal, tapi menghapus 3 parser sekaligus + siap Fase 2.
- Semua lewat **adapter terpisah** (`BrokerSummarySource`, `OwnershipSource`) → kalau tidak ada/rusak, engine Fase 1 tetap jalan.

---

## 2. Prinsip Fase 1 (revisi)

1. **Gratis & resmi.** Hanya IDX Stock Summary + KSEI. Nol biaya data. **[revisi 3]** Inti tetap begitu; data per-broker dari feed Tier 3 (gratis, tidak resmi) ditambahkan di lapis percobaan saja — lihat §0.
2. **VPS kecil, murah, andal.** ~$5/bln. Cron EOD.
3. **Metodologi 2-lapis (tiru IDXAlpha):**
   - **Lapis berbobot** — sinyal yang sudah Anda forward-test dan terbukti tidak nol.
   - **Lapis "masa percobaan" bobot 0** — dihitung & ditampilkan, tapi tidak menggerakkan keputusan sampai terbukti.
   - **Hari 1: SEMUA sinyal di lapis masa-percobaan.** Tidak ada skor. Hanya komponen mentah + catatan.
4. **Forward-test wajib dari hari pertama.** Tiap tag yang sistem keluarkan → catat otomatis, ukur return H+1/H+3/H+5. Setelah ~50–100 observasi, baru sinyal "lulus" ke lapis berbobot.
5. **Sumber data di balik adapter.** `class PriceSource`, `class OwnershipSource`, (`class BrokerSummarySource` — kosong dulu). Fase 2 ganti isi, engine tak berubah.
6. **Telegram = satu-satunya UI.**
7. **Jangan inferensi data yang tak ada.** Tidak ada maker/taker, CVD, order-size, Sultan/Zombi (pelajaran dari IDXAlpha).

---

## 3. Sumber data Fase 1 (final)

| Data | Sumber | Field | Frekuensi |
|---|---|---|---|
| Price bars + **foreign flow** + papan RG/NG | IDX Stock Summary (`/primary/TradingSummary/GetStockSummary`, param tanggal) | Date, Prev, Open, High, Low, Close, Volume, Value, Frequency, **ForeignBuy, ForeignSell** (lembar), **NonRegularVolume/Value/Freq**, ListedShares, TradebleShares, Bid/Offer | EOD ~17–19:00 WIB |
| Nilai IHSG + indeks sektoral | IDX Index Summary (endpoint publik) | close indeks harian | EOD |
| Kepemilikan Lokal/Asing/tipe investor per emiten | KSEI "Komposisi Kepemilikan (Lokal-Asing)" — unduh XLSX | saldo per kategori investor, akhir bulan | **Bulanan** |
| Corporate action (split, bonus, HMETD, dividen) | KSEI "List of CA Activities" XLS | tanggal, jenis, rasio | Mingguan cek |
| Kalender hari bursa | Turunkan dari tanggal unik di Stock Summary | — | otomatis |

**Etika fetch:** 1 request tiap 3–5 detik, 1 koneksi, User-Agent Chromium realistis + header lengkap, jalan **sekali sehari** ~19:00 WIB. Simpan raw JSON/XLSX + SHA256. Retry backoff+jitter maks 3×; kalau 403/429 beruntun → stop + kirim pesan Telegram "collector gagal", jangan lawan.

---

## 4. VPS — spesifikasi & pilihan

**Spek cukup:** 1–2 vCPU, 2 GB RAM, 20–40 GB SSD. Beban = 1 batch fetch/hari + Postgres kecil + 1 pesan Telegram. Nyaris idle.

**Pilihan (urut rekomendasi untuk kasus ini):**

| Provider | ~Harga/bln | Region | Catatan |
|---|---|---|---|
| **Contabo** (VPS S) | ~$5–6 | Singapura tersedia | Spek besar untuk harganya; region Asia oke |
| **DigitalOcean / Vultr / Linode** (basic) | $5–6 | **Singapura** | Andal, dokumentasi banyak, snapshot mudah |
| **Biznet Gio / IDCloudHost / Dewaweb** | ~Rp50–100rb | **Indonesia** | IP Indonesia → friksi Cloudflare IDX paling kecil |
| Hetzner (CX22) | ~€4 | Jerman/Finlandia | Termurah & andal, tapi latency + IP Eropa (risiko challenge IDX lebih tinggi) |

**Pertimbangan penting — Cloudflare di depan IDX:** endpoint `/primary/` di belakang Cloudflare. IP datacenter kadang kena challenge. Mitigasi skala pribadi:
- UA + header realistis (bukan `python-httpx/x.x`).
- Rate sangat rendah (Anda cuma butuh 1 fetch/hari).
- Kalau `httpx` kena 403: fallback ke `curl_cffi` (impersonate Chrome TLS) atau Playwright headless 1× sehari.
- **IP Indonesia (Biznet Gio dll.) paling mulus** — kalau ragu, ambil yang ini.
- Uji dulu: dari VPS, `curl` ke endpoint Stock Summary sebelum bangun apa pun.

---

## 5. Metodologi v0 — semua di lapis "masa percobaan" (bobot 0)

Window pakai **hari bursa**. Tidak ada skor komposit di v0. Sistem hanya menghitung komponen + memberi **tag deskriptif**, lalu forward-test-nya sendiri.

### Kategori pelaku: NeoBDM punya, kita (Fase 1) tidak

NeoBDM: Market Maker · Foreign · Non-Retail · Sultan · Zombie · Top Retail Jualan. Semua ini butuh **broker summary per emiten** (siapa broker, beli/jual per broker) — yang **tidak ada di Fase 1** (kita hanya IDX Stock Summary + KSEI).

| Kategori NeoBDM | Butuh | Fase 1? |
|---|---|---|
| **Foreign** | broker asing / flag D/F | **Ada — beda cara.** Kita pakai split `ForeignBuy`/`ForeignSell` resmi IDX (berbasis domisili rekening, "un-paintable"), bukan tebakan kode broker |
| Non-Retail / Market Maker | klasifikasi broker × flow per broker | ❌ tidak ada broker summary |
| Sultan | broker + blok tunggal besar + ukuran order | ❌ (order size hilang sejak 2021, lihat C3) |
| Zombie | riwayat aktivitas per broker | ❌ tidak ada broker summary |
| Top Retail Jualan | broker retail (YP/PD/CC…) net jual | ❌ butuh broker summary + mapping |

**Yang kita punya sebagai proksi pelaku:**
- **Asing vs bukan-Asing** — harian, dari split resmi IDX (`nff_*`).
- **Institusi vs Ritel vs Asing (level kepemilikan)** — **bulanan**, dari 9 kategori investor KSEI (`ownership_monthly.detail`: asuransi, korporat, dapen, lembaga keuangan, individu, reksadana, sekuritas, yayasan, lainnya × lokal/asing). Ini *posisi ter-settle*, bukan *flow harian*.

Kategori NeoBDM baru tersedia di **Fase 1.5** kalau beli broker summary — Sectors bahkan sudah punya `cohort` (institutional/retail/mixed) bawaan. "Sultan/Zombi/Top Retail" tetap = heuristik buatan sendiri yang harus dikalibrasi + di-forward-test (bukan diberi bobot begitu saja). Ingat: IDXAlpha yang punya semua ini mengakui IC sinyal broker-level cuma 0,02–0,09 — belum tentu menambah edge.

### Komponen yang dihitung (dari data gratis)

| Komponen | Definisi | "un-paintable"? |
|---|---|---|
| **Net Foreign Flow (NFF) D0** | (ForeignBuy − ForeignSell) × est. harga rata-rata hari itu | ✅ (flag resmi IDX) |
| **NFF rolling** | Σ NFF untuk D0–D3, W1–W4 (blok 5 hari bursa) | ✅ |
| **Foreign flow consistency** | # hari bursa net-foreign-buy dari 5/10 hari terakhir | ✅ |
| **Kuadran Harga × NFF** | (Δharga 5H: naik/sideways/turun) × (NFF W1: + / −) → Akumulasi / Markup / Distribusi / Markdown | ✅ |
| **Volume surge** | Volume hari ini ÷ rata-rata 20 hari bursa | — |
| **RS vs IHSG** | return saham − return IHSG, window 5/10/20 hari | — |
| **Papan negosiasi** | rasio NonRegularValue ÷ Value total → flag "banyak crossing hari ini" | ✅ |
| **Tren kepemilikan (KSEI bulanan)** | Δ %Asing & %Institusi 1–3 bulan terakhir → naik / turun / datar | ✅ (register KSEI) |
| **Holder-base (jika ada di file KSEI)** | Δ jumlah pemegang (broadening = ke ritel) | ✅ |
| **Penyesuaian CA** | koreksi harga & volume historis saat split/bonus | — |

### Tag deskriptif (bukan rekomendasi)

- `AKUM-ASING` — harga sideways/turun 5H **dan** NFF W1 & W2 positif **dan** foreign consistency ≥ 6/10 hari
- `DISTRIBUSI-ASING` — harga naik 5H **dan** NFF W1 negatif **dan** foreign consistency ≤ 3/10
- `MARKUP` — harga naik **dan** NFF positif (searah — kurang menarik, sekadar konteks)
- `CROSSING-TINGGI` — NonRegular ratio > ambang (hati-hati baca flow hari itu)
- `KSEI-KONFIRM` / `KSEI-DIVERGEN` — apakah tren kepemilikan bulanan searah dgn tag flow

Ambang (`≥6/10`, `NonRegular ratio > X`, dst.) = tebakan awal. **Bukan untuk dipatuhi — untuk diuji.**

### Cek integritas data — jalan SEBELUM hitung sinyal (lihat [PRINSIP-DATA.md](PRINSIP-DATA.md) Bagian C)
- **Pisahkan RG dari NG** — semua flow dihitung di reguler saja; `NonRegularValue/Value` tinggi → flag `CROSSING-TINGGI`.
- **Penyesuaian corporate action** wajib (split/bonus) — kalau tidak, matematika flow sampah.
- **Emiten suspend / volume 0** → keluarkan dari analisa.
- **Validasi silang bulanan:** akumulasi net foreign bulanan harus searah Δ %asing KSEI bulan itu → kalau divergen persisten, flag `KSEI-DIVERGEN`.
- (Fase 1.5, kalau ada broker summary) rekonsiliasi `Σ broker value ≈ IDX Value`, deteksi pola wash, netting broker terafiliasi.
- Tambah kolom `data_flags` di `signal_components`; baris `RECON_FAIL` → tidak menghasilkan tag hari itu.

### Komponen broker (Tier 3, probation) — **[revisi 3]**

Modul: `src/vg/engine/scoring.py`. Murni membaca (`SELECT`) `price_bars`, `broker_flows`, `broker_levels`, `done_by_price`; tidak mengubah skema. Setiap fungsi mengembalikan `None` bila data tidak cukup untuk jawaban jujur, dan `score_ticker` mencatat alasannya di `TickerScore.missing`.

| Ukuran | Rumus | Catatan |
|---|---|---|
| **Bandar Power Index** (Top 3 / Top 5) | `cr{N}_buy` = nilai beli N pembeli bruto terbesar ÷ `price_bars.value`; `cr{N}_sell` serupa untuk penjual; **`bpi{N} = cr{N}_buy − cr{N}_sell`** ∈ [−1, 1] | >0: sisi beli lebih terkonsentrasi daripada sisi jual. Disertai `overlap3` (berapa broker ada di top-3 beli *dan* top-3 jual) dan, dari `broker_flows` saja, `net_buyer_concentration` (cr1/cr3/cr5 di antara 20 pembeli bersih — pandangan lama `bkr_cr3`, diperluas ke 5) |
| **Aggressor Ratio** (HAKA vs HAKI) | `haka_share = HAKA ÷ (HAKA + HAKI)` ∈ [0, 1]; `ratio = HAKA ÷ HAKI`; `coverage = (HAKA+HAKI) ÷ qty`, berdasarkan volume | Default hanya papan **RG**: di NG/TN feed menandai hampir semua sebagai BUY (negosiasi tidak punya spread untuk dilintasi) |
| **Retail Net-Flow Divergence** | net kelompok **ritel** (`RETAIL_BROKERS`) vs **institusi asing** (`FOREIGN_BROKERS − RETAIL_BROKERS`) sebagai interval pasti `[lo, hi]` dari `broker_flows`+`broker_levels`; `other` = pelengkap (jumlah ketiganya nol); `gap = (asing-inst − ritel) ÷ nilai hari itu` | Status (mis. `retail_buying_institutions_selling`) hanya diberikan bila **seluruh** interval kedua kelompok ada di satu sisi nol (± `band`); selain itu `indeterminate` |

Hasil uji pada data hidup (5 Nov 2026, 173 ticker ber-tangga, hanya-baca): BPI3 tersebar sekitar nol (min −0,40, maks +0,42), terhitung untuk semua ticker; status divergensi pasti untuk 141 dari 173, `indeterminate` untuk 32. Dua peringatan yang ikut dari data:
- **`overlap3 ≥ 1` pada 151 dari 173 ticker (87%)** — broker yang sama sering ada di tiga besar beli *dan* jual (crossing / market-making / pialang yang melayani kedua sisi). Konsentrasi tinggi *tidak* otomatis berarti satu pihak mengakumulasi; baca selalu bersama `overlap3`.
- **Aggressor Ratio belum menghasilkan angka** untuk hampir semua ticker karena `done_by_price` baru berisi satu ticker-hari. Ia baru berguna setelah dipanen untuk watchlist (biaya: ~130 halaman per saham-hari).

Status: **probation, bobot 0**, belum menulis ke `signal_components`/`signal_log`, belum masuk digest, dan belum di-forward-test. Ambang apa pun yang kelak dipasang di atasnya = tebakan yang harus diuji, sama seperti §5 lainnya. Kategori pelaku di tabel NeoBDM di atas tetap heuristik (daftar broker ritel hanya 6 kode, sengaja konservatif).

### Yang TIDAK dilakukan v0
Skor 0–100 · mapping broker lengkap · Sultan/Zombi · maker/taker/CVD · pattern scanner · AI narasi · target harga / stop loss. **[revisi 3]** CR3/konsentrasi broker kini ada sebagai probation (lihat di atas); sisanya tetap tidak dilakukan, dan tidak ada model AI di mesin (aturan eksplisit, angka jujur).

---

## 6. Protokol forward-test (inti Fase 1)

Ini yang membuat Fase 1 bernilai — bukan tag-nya, tapi bukti apakah tag-nya berguna.

1. Tiap EOD, untuk tiap saham (IDX250), sistem tulis baris ke `signal_log`: tanggal, ticker, semua komponen, tag, harga close.
2. Tiap EOD berikutnya, sistem isi kolom outcome baris-baris lama: `ret_h1`, `ret_h3`, `ret_h5`, `ret_h10` (return close-to-close), `ret_vs_ihsg_h5`.
3. Sekali seminggu, kirim ke Telegram ringkasan evaluasi:
   ```
   📈 EVAL MINGGUAN (data: 5 Sep 2026, n obs kumulatif)
   AKUM-ASING     : n=63  avg ret H5 +1.8%  vs IHSG +1.1%  hit-rate 58%
   DISTRIBUSI-ASING: n=41  avg ret H5 -0.9%  vs IHSG -0.4%  hit-rate 55%
   CROSSING-TINGGI : n=88  avg ret H5 -0.2%  (no edge)
   ```
4. Setelah **≥50 observasi** per tag **dan** ≥2 bulan: kalau avg ret vs IHSG konsisten + arah benar → naikkan tag itu ke **lapis berbobot** dan mulai kirim alert aksi untuknya. Kalau tidak → tetap deskriptif atau matikan.
5. Paralel: **Anda paper-trade** tag AKUM-ASING di watchlist, catat manual (entry, exit, alasan) di spreadsheet. Bandingkan intuisi Anda vs sistem.

---

## 7. Telegram (Fase 1)

- **1 digest EOD/hari bursa**, ~19:15 WIB, ke chat pribadi Anda.
  ```
  📊 VEKTOR GAUSS — EOD 02 Sep 2026
  harga ✓ · indeks ✓ · KSEI Aug 2026

  WATCHLIST
  📌 BBRI  3.390 (+0,3%)   NFF D0 +45,2B  W1 +180,4B  asing 7/10
     markdown · KSEI %asing ↑ · AKUM-ASING ⚡ · KSEI-KONFIRM
     aksi ADD · SL 3.180 (-6,2%) · target 3.915 (+15,5%) · R:R 2,5 · ~48 lot (risiko 1,0% akun)
     dasar: SL = close − 2×ATR14 (105); target = 2,5R · mekanis, bukan prediksi
  ADRO  2.650 (-4,7%)   NFF D0 -54,3B  W1 -210,1B  asing 2/10
     markup · KSEI %asing → · DISTRIBUSI-ASING · ⚠ CROSSING-TINGGI
     aksi AVOID

  RADAR — AKUM-ASING hari ini (di luar watchlist)
  ANTM  3.030 (-1,6%)  W1 +88,0B  · SL 2.850 / tgt 3.480
  ```
- `📌` = posisi yang Anda pegang (`VG_HOLDINGS`) → aksi jadi ADD/HOLD/REDUCE; sisanya BUY/WATCH/AVOID.
- `⚡` = tag berubah dari hari bursa sebelumnya.
- **Target / SL / aksi ADA** (permintaan Anda): aturan **mekanis** — SL = `close − N×ATR14`, target = `R×risiko`, lot size dari `VG_ACCOUNT_BALANCE` + `VG_RISK_PCT`. Bukan prediksi. Logika di `src/vg/calc.py` (dipakai juga oleh kalkulator web Fase 2).
- Di-gate `VG_DIGEST_TRADE_CALLS` — **wajib dimatikan / ditulis ulang kalau digest dijual** (lihat §11).
- `vg calc --entry .. --sl .. --target-r ..` — kalkulator posisi mandiri di CLI.
- Eval mingguan (§6.3) via `vg digest --weekly`.

---

## 8. Skema data (revisi — broker_flows opsional/kosong)

```
trading_calendar(session_date PK)
tickers(ticker PK, name, sector, is_idx250)
price_bars(session_date, ticker, prev, open, high, low, close,
           volume, value, frequency, foreign_buy_vol, foreign_sell_vol,
           nonreg_volume, nonreg_value, listed_shares, tradable_shares,
           source, source_tier, PK(session_date,ticker))
index_bars(session_date, index_code, close, source, PK(session_date,index_code))
ownership_monthly(month_end, ticker, local_individual, local_institution,
                  foreign_total, <kategori investor lain>, holders_count,
                  source, source_tier, PK(month_end,ticker))
corp_actions(ticker, ex_date, type, ratio, notes)
signal_components(session_date, ticker, nff_d0, nff_w1, nff_w2, nff_w4,
                  foreign_consistency_10d, vol_surge, rs_ihsg_5d, rs_ihsg_20d,
                  nonreg_ratio, ksei_foreign_trend_3m, ksei_inst_trend_3m,
                  quadrant, data_flags,          -- array: RECON_FAIL, CROSSING-TINGGI, KSEI-DIVERGEN, SOURCE_CONFLICT, SUSPECT_WASH
                  PK(session_date,ticker))
broker_affiliation_groups(group_id, broker_code, group_name, version, valid_from)  -- Fase 1.5, kalau ada broker summary
signal_log(session_date, ticker, tag, close, layer,           -- layer='probation'|'weighted'
           ret_h1, ret_h3, ret_h5, ret_h10, ret_vs_ihsg_h5,   -- diisi belakangan
           PK(session_date,ticker,tag))
-- [revisi 3] rencana awal: broker_flows dibiarkan kosong, adapter = stub. Kenyataan: terisi dari feed Tier 3
-- (kolom aktual ada di db/schema.sql; di bawah ringkasannya). Skema tidak berubah oleh revisi ini.
broker_flows(session_date, ticker, broker_code, side_flag, buy_val, sell_val, buy_vol, sell_vol,
             buy_avg, sell_avg, buy_freq, sell_freq, net_val, net_vol, board, source, source_tier,
             raw_sha, parser_version, PK(session_date,ticker,broker_code,board))   -- 20 pembeli bersih teratas
broker_levels(session_date, ticker, side 'B'|'S', rank, broker_code, broker_name, val, vol, freq, avg_price,
              source, source_tier, PK(session_date,ticker,side,rank))             -- tangga 25 bruto per sisi
broker_inventory(session_date, ticker, broker_code, net_val, net_vol, cum_net_val, buy_avg, sell_avg,
                 source, source_tier, PK(session_date,ticker,broker_code))
done_by_price(session_date, ticker, board, price_num, qty, value, trades, buy_aggressor_qty,
              sell_aggressor_qty, foreign_buy_qty, foreign_sell_qty, source, source_tier,
              PK(session_date,ticker,board,price_num))
raw_artifacts(id, fetched_at, source, path, sha256)
run_log(run_id, batch_date, phase, status, error, started_at, finished_at)
```

DB: **Postgres** (Docker di VPS). Bukan SQLite — Fase 2 pakai Postgres, hindari migrasi. TimescaleDB tidak perlu di Fase 1 (data kecil), tapi pakai image `timescale/timescaledb` saja biar gampang di-enable nanti.

---

## 9. Arsitektur

> **[revisi 3]** Diagram di bawah adalah rencana awal. Kenyataannya: PC rumah → WSL2 Ubuntu, PostgreSQL 16 lokal, Task Scheduler (bukan cron/Docker), `scripts/eod.sh` dengan urutan di §0, dan `psycopg` murni (tanpa SQLAlchemy). Pydantic dipakai hanya untuk konfigurasi dan validasi baris.

```
VPS ($5/bln)
├─ Docker: postgres (timescaledb image, tak di-enable)
├─ cron 19:00 WIB hari bursa → python -m vg.collect
│    ├─ PriceSource.fetch()      → raw + price_bars + index_bars
│    ├─ OwnershipSource.monthly() → (kalau tanggal awal bulan) ownership_monthly
│    ├─ corp_actions refresh (mingguan)
│    ├─ engine.compute()          → signal_components + signal_log(probation)
│    ├─ engine.backfill_outcomes() → isi ret_* baris lama
│    └─ telegram.send_digest()
├─ cron Senin 19:30 → telegram.send_weekly_eval()
└─ .env: DB_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WATCHLIST
```

**Stack:** Python 3.12, `httpx` (+ `curl_cffi` fallback), `pandas`, `openpyxl` (XLSX KSEI), `psycopg`/SQLAlchemy, `python-telegram-bot` atau raw Bot API. Scheduler: **cron OS** (paling sederhana & andal untuk 2 job/hari). Tanpa Celery/Redis/Airflow.

---

## 10. Yang DITUNDA ke Fase 2

Broker summary berbayar & CR3 · **web dashboard** (termasuk **kalkulator posisi** ala `neobdm.tech/calculator/` — logikanya sudah ada di `calc.py`, tinggal UI) · multi-user · pembayaran · skor 0–100 · Sultan/Zombi · rotation chart · AI narasi · lisensi data resmi · disclaimer/PT/UU PDP · coverage penuh 900 emiten · Redis/Celery.

---

## 11. Dinding Fase 1 → Fase 2

Yang jadi wajib saat dijual: lisensi data (broker summary), PT + UU PDP, multi-user infra, pembayaran, **dan penanganan trade calls**. Untuk pemakaian pribadi target/SL/aksi bebas; begitu dijual, "aksi BUY/SELL/HOLD + target harga" bersinggungan dengan aturan OJK (Penasihat Investasi, penindakan influencer/pom-pom). Opsi Fase 2: (a) matikan `VG_DIGEST_TRADE_CALLS`, atau (b) ubah jadi "kalkulator" murni — user masukkan entry/SL sendiri, sistem hanya hitung lot & R:R (tidak menyarankan arah), atau (c) kajian izin. Sudah di-gate satu env var, jadi mudah dimatikan.

Keputusan desain yang memudahkan pivot: **adapter per sumber**, kolom `source`/`source_tier` di semua tabel, simpan raw+hash, Postgres dari awal, trade calls di balik satu toggle, **arsitektur 2-lapis + forward-test** (justru itu yang bikin produk Fase 2 kredibel — tunjukkan bukti, bukan janji).

---

## 12. Urutan eksekusi

| # | Langkah | Status |
|---|---|---|
| 1 | Uji `curl` IDX + KSEI dari VPS (Cloudflare check) — `scripts/check_idx_access.sh` | ↪ digantikan: ternyata hanya IP residensial yang lolos, jadi berjalan di PC rumah/WSL2 (§0); VPS = [RENCANA-MIGRASI-VPS.md](RENCANA-MIGRASI-VPS.md) |
| 2 | Scaffold repo + docker-compose (postgres) + skema + `trading_calendar` | ✅ `6aa70c8` |
| 3 | Collector IDX Stock Summary → `price_bars` | ✅ `6aa70c8` |
| 4 | Collector IDX Index Summary → `index_bars` (IHSG) | ✅ `8a2d7a2` |
| 5 | Collector KSEI kepemilikan bulanan → `ownership_monthly` | ✅ `e75a82a` |
| 6 | Collector corp actions (KSEI schedule + split detector) → `corp_actions` | ✅ `8a2d7a2` |
| 7 | `engine.compute()` — komponen §5 + probation tags → `signal_components` / `signal_log` | ✅ (this commit) |
| 8 | `engine.backfill_outcomes()` + `vg eval` | ✅ (this commit) |
| 9 | **Telegram: digest harian + eval mingguan** | ✅ (`vg digest`, `vg digest --weekly`) |
| 10 | jadwalkan, **diamkan 2 bulan**, baca `vg eval`, paper-trade paralel | ▶ berjalan: Task Scheduler 19:15 WIB; jam forward sejak 2026-09-04; `vg paper-update` jalan tiap EOD |
| 11 | Putuskan: sinyal naik ke lapis berbobot? Fase 1.5 (beli broker summary)? berhenti? | ⬜ menunggu gerbang forward-test; semua 26 tag masih `probation` |
| 12 | **[revisi 3]** Feed Tier 3: broker summary, inventory, price-history (tier-guarded), done-details | ✅ `vg collect-broker-summary`, `collect-broker-inventory`, `collect-price-history`, `collect-done-details`; `broker-deep` backfill malam hari |
| 13 | **[revisi 3]** `broker_levels` + batas pasti net kelompok (`broker_bounds.py`) | ✅ |
| 14 | **[revisi 3]** Validasi skema Pydantic + `pydantic-settings` di semua jalur tulis | ✅ commit `0d3a07e`; ⬜ belum di-deploy (butuh `pip install -e .` di venv produksi — sudah terpasang di venv WSL ini) |
| 15 | **[revisi 3]** Mesin skoring broker (`engine/scoring.py`): Bandar Power Index, Aggressor Ratio, Retail Net-Flow Divergence | ✅ modul + 28 tes; ⬜ belum dihubungkan ke `compute`/digest; ⬜ belum di-forward-test; ⬜ Aggressor Ratio menunggu data `done_by_price` |

---

## Yang perlu Anda putuskan sebelum "mulai"

> **[revisi 3]** Keempat butir ini sudah dijawab oleh praktik: watchlist ada di `.env` (`VG_WATCHLIST`), bot Telegram sudah terpasang, hosting = PC rumah (VPS ditunda), dan Index Alpha dilewati karena feed Tier 3 mengisi kebutuhan broker. Keputusan yang masih terbuka sekarang: (a) cadangan bila feed mati (Sectors.app / IDX Log Data), (b) kapan memindahkan ke VPS ISP Indonesia, (c) apakah ukuran §5 "Komponen broker" layak diberi ambang dan dihubungkan ke `compute` untuk forward-test. Daftar asli dibiarkan di bawah sebagai jejak.

1. **Watchlist awal** — daftar ticker (mis. 5–15). IDX250 penuh tetap di-scan untuk RADAR + forward-test; watchlist cuma untuk digest detail.
2. **VPS** — mau saya finalkan rekomendasi ke 1 provider (Biznet Gio Indonesia vs Vultr Singapura)? Butuh tahu: prefer bayar Rupiah/lokal atau kartu/USD?
3. **Bot Telegram** — Anda buat via @BotFather, kasih saya token + chat ID Anda saat scaffold (atau taruh sendiri di `.env`).
4. **Index Alpha free tier untuk ≤5 saham inti** — mau dipakai sejak awal (sah, gratis, 5/hari) atau lewati sampai Fase 1.5?
