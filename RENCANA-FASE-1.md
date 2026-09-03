# Rencana Fase 1 — Alat Internal + Telegram Alert (revisi 2)

Keputusan: alat pribadi untuk trading Anda sendiri, alert Telegram, **di VPS**. Fase 2 (mungkin) = langganan/dijual.
Jawaban Anda: sumber data harus **gratis & reliable** + **resmi, hindari sumber yang bisa dimanipulasi**; host **VPS**; **perhalus rencana dulu** sebelum scaffold.

Pendamping: [PRINSIP-DATA.md](PRINSIP-DATA.md) *(mengikat — baca dulu)* · [ANALISIS-VALIDASI.md](ANALISIS-VALIDASI.md) · [INVENTARIS-SUMBER-DATA.md](INVENTARIS-SUMBER-DATA.md) · [LANSKAP-KOMPETITOR.md](LANSKAP-KOMPETITOR.md) · [IDXALPHA-TEARDOWN.md](IDXALPHA-TEARDOWN.md) · [SECTORS-API.md](SECTORS-API.md).
**Masih tanpa kode.**

> **Prinsip data ([PRINSIP-DATA.md](PRINSIP-DATA.md)):** Fase 1 = 100% Tier 0 (IDX Stock Summary + KSEI resmi). Tanpa broker summary → nol permukaan manipulasi kode-broker. Foreign flow dihitung IDX sendiri. Kalau nanti tambah broker summary: redistributor berlisensi (Tier 1) > agregator komersial (Tier 2, wajib rekonsiliasi) > **jangan** scrape platform retail (Tier 3). Metodologi selamanya condong ke sinyal yang bandar tidak bisa palsukan.

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

1. **Gratis & resmi.** Hanya IDX Stock Summary + KSEI. Nol biaya data.
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

### Yang TIDAK dilakukan v0
Skor 0–100 · CR3 / konsentrasi broker (tak ada data) · mapping broker · Sultan/Zombi · maker/taker/CVD · pattern scanner · AI narasi · target harga / stop loss.

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
  📊 VEKTOR GAUSS — EOD 2 Sep 2026  (Stock Summary ✓ · KSEI: Agu 2026)

  WATCHLIST
  BBRI  3.390 (+0,3%)   NFF D0 +45B  W1 +180B  asing 7/10 hari
        harga sideways · KSEI: %asing ↑ 2 bln → AKUM-ASING (KSEI-KONFIRM)
  ADRO  2.650 (-4,7%)   NFF D0 -54B  W1 -210B  asing 2/10 hari
        harga turun · NonReg 41% ⚠ → DISTRIBUSI-ASING

  RADAR — baru masuk AKUM-ASING hari ini (IDX250)
  ANTM · PTBA · LSIP

  ⚠ collector KSEI: file Sep belum terbit (pakai Agu 2026)
  ```
- Alert terpisah hanya kalau **watchlist ticker ganti tag** (mis. AKUM→DISTRIBUSI).
- Eval mingguan (§6.3).
- **Tanpa** target/SL/BUY-SELL. Alat baca, bukan sinyal.

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
-- broker_flows: DEFINISIKAN tabelnya, biarkan kosong. Adapter BrokerSummarySource = stub.
broker_flows(session_date, ticker, broker_code, side_flag, buy_val, sell_val,
             buy_vol, sell_vol, buy_avg, sell_avg, source, PK(...))
raw_artifacts(id, fetched_at, source, path, sha256)
run_log(run_id, batch_date, phase, status, error, started_at, finished_at)
```

DB: **Postgres** (Docker di VPS). Bukan SQLite — Fase 2 pakai Postgres, hindari migrasi. TimescaleDB tidak perlu di Fase 1 (data kecil), tapi pakai image `timescale/timescaledb` saja biar gampang di-enable nanti.

---

## 9. Arsitektur

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

Broker summary berbayar & CR3 · web dashboard · multi-user · pembayaran · skor 0–100 · Sultan/Zombi · rotation chart · AI narasi · lisensi data resmi · disclaimer/PT/UU PDP · coverage penuh 900 emiten · Redis/Celery.

---

## 11. Dinding Fase 1 → Fase 2

*(tak berubah dari revisi 1)* Yang jadi wajib saat dijual: lisensi data (broker summary), disclaimer + hindari "target/SL/action", PT + UU PDP, multi-user infra, pembayaran. Keputusan desain sekarang yang memudahkan pivot: **adapter per sumber**, kolom `source` di semua tabel, simpan raw+hash, Postgres dari awal, output tanpa "BUY/SELL", **arsitektur 2-lapis + forward-test** (justru itu yang bikin produk Fase 2 kredibel — bisa tunjukkan bukti, bukan janji).

---

## 12. Urutan eksekusi

| # | Langkah | Status |
|---|---|---|
| 1 | Uji `curl` IDX + KSEI dari VPS (Cloudflare check) — `scripts/check_idx_access.sh` | ⬜ butuh VPS |
| 2 | Scaffold repo + docker-compose (postgres) + skema + `trading_calendar` | ✅ `6aa70c8` |
| 3 | Collector IDX Stock Summary → `price_bars` | ✅ `6aa70c8` |
| 4 | Collector IDX Index Summary → `index_bars` (IHSG) | ✅ `8a2d7a2` |
| 5 | Collector KSEI kepemilikan bulanan → `ownership_monthly` | ✅ `e75a82a` |
| 6 | Collector corp actions (KSEI schedule + split detector) → `corp_actions` | ✅ `8a2d7a2` |
| 7 | `engine.compute()` — komponen §5 + probation tags → `signal_components` / `signal_log` | ✅ (this commit) |
| 8 | `engine.backfill_outcomes()` + `vg eval` | ✅ (this commit) |
| 9 | **Telegram: digest harian + eval mingguan** | ⬜ next |
| 10 | cron di VPS, jalankan, **diamkan 2 bulan**, baca `vg eval`, paper-trade paralel | ⬜ |
| 11 | Putuskan: sinyal naik ke lapis berbobot? Fase 1.5 (beli broker summary)? berhenti? | ⬜ |

---

## Yang perlu Anda putuskan sebelum "mulai"

1. **Watchlist awal** — daftar ticker (mis. 5–15). IDX250 penuh tetap di-scan untuk RADAR + forward-test; watchlist cuma untuk digest detail.
2. **VPS** — mau saya finalkan rekomendasi ke 1 provider (Biznet Gio Indonesia vs Vultr Singapura)? Butuh tahu: prefer bayar Rupiah/lokal atau kartu/USD?
3. **Bot Telegram** — Anda buat via @BotFather, kasih saya token + chat ID Anda saat scaffold (atau taruh sendiri di `.env`).
4. **Index Alpha free tier untuk ≤5 saham inti** — mau dipakai sejak awal (sah, gratis, 5/hari) atau lewati sampai Fase 1.5?
