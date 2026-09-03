# Analisis & Validasi — Blueprint "Vektor Gauss"

**Status:** Laporan validasi. **Tidak ada kode yang dibuat.**
**Sumber yang dinilai:**
- Percakapan Gemini versi lengkap — `share.gemini.google/DPhM7cQKhKPn` (redirect `gemini.google.com/share/1b63a2af1f2c`), diekstrak penuh (3.400+ baris).
- `neobdm.tech` — dicek langsung: seluruh situs di belakang login Django (`/accounts/login/`), tidak ada halaman fitur/pricing publik. Tidak ada info baru selain yang sudah ada di memori proyek.
- Memori proyek "NeoBDM Pipeline Architecture".

Tanggal: 2026-09-03.

---

## 1. Ringkasan eksekutif

Blueprint Gemini **benar di tingkat konsep, rapuh di tingkat sumber data dan kalibrasi**.

- **Arah besar sudah tepat:** NeoBDM = *aggregator/enricher*, bukan bursa. Nilai jualnya = klasifikasi broker + agregasi flow + cross-check kepemilikan, dikemas agar mudah dipakai. Membangun dari sumber primer (BEI + KSEI) — bukan scrape NeoBDM — adalah keputusan yang benar.
- **Tiga asumsi menahan beban (load-bearing) yang salah atau terlalu optimistis:**
  1. Data KSEI "harian T+2 per emiten per kategori" **tidak tersedia publik** — yang publik itu **bulanan & agregat**. Padahal Gemini menyebut lapisan ini "komponen paling powerful" (Pilar/Tahap 4).
  2. Broker Summary EOD historis untuk 900+ emiten × bertahun-tahun adalah **produk berlisensi IDX**, bukan "data publik gratis yang sangat mudah diduplikasi".
  3. Deteksi "Sultan/Zombi" dan rasio konsentrasi intraday butuh data **order-level / kode broker intraday** yang **disembunyikan BEI sejak 2021**.
- **Semua angka skoring (bobot 30/25/25/20, threshold CR3 >1.5, dst.) adalah karangan Gemini** tanpa basis empiris. Dilabeli seperti fakta ("Skor 93 ⟹ STRONG ENTRY").
- **Label kepercayaan Gemini (`[Certain]` / `[Likely]`) miskalibrasi** — banyak tebakan arsitektural dicap `[Certain]`.

Rekomendasi: perlakukan blueprint sebagai **hipotesis produk**, bukan spesifikasi. Sebelum menulis kode, lewati dulu 3 *gate* di §7.

---

## 2. Yang sudah benar — pertahankan

| Poin | Catatan |
|---|---|
| NeoBDM bukan penyedia data primer; membangun dari BEI + KSEI langsung | Benar. Menghindari ToS NeoBDM yang melarang scraper. |
| Inti bandarmologi = pisahkan flow ritel vs institusi/asing, lalu lihat harga vs flow | Ini memang yang dilakukan praktisi. Kuadran akumulasi/distribusi masuk akal sebagai kerangka naratif. |
| Arsitektur batch EOD (bukan real-time order book) | Sesuai karakter data. Beban infra terprediksi. |
| Rolling multi-window (D & W) sebagai peredam noise | Konsep valid — 1 hari hijau ≠ akumulasi sistematis. |
| Simpan raw artifact + hash + replay; DLQ/quarantine untuk parser drift; broker mapping ter-versioned | Praktik engineering yang benar. |
| Trading calendar wajib untuk window | Benar (lihat C5 — Gemini menyebutnya tapi contoh SQL-nya melanggar). |
| Posisi legal "alat olah data statistik", bukan penasihat investasi + disclaimer wajib | Perlu — walau belum cukup (lihat C7). |
| Stack: Python + Postgres/Timescale + Redis + Celery + single-node Docker | Wajar untuk skala ini. |

---

## 3. Temuan kritis (diurutkan dari paling berdampak)

### C1 — Data KSEI publik itu **bulanan & agregat**, bukan harian T+2 per emiten
**Klaim Gemini:** "KSEI Scripless Balance Position … kategori Local/Foreign/Individual bertambah/berkurang di level settlement (T+2)" dipakai untuk "audit rekonsiliasi Broksum tanggal T vs KSEI tanggal T+2" per emiten. Disebut **senjata utama / komponen paling powerful**.

**Realita (perlu diverifikasi final ke KSEI, tapi keyakinan tinggi):**
- Yang dipublikasikan KSEI secara terbuka adalah **Statistik Kepemilikan Efek / Data Kepemilikan** dengan frekuensi **bulanan** (via situs KSEI / AKSes), agregat per kategori pemegang (lokal individu, lokal institusi, asing, dst.) per emiten — **bukan** feed harian per settlement date.
- **Tidak ada** feed publik yang memungkinkan Anda mengambil "delta saldo custody Asing emiten X pada hari Kamis (T+2)" untuk diadu dengan broksum hari Selasa.
- Data KSEI yang lebih granular ada, tapi lewat kanal berbayar/berizin, bukan unduhan publik.

**Dampak:**
- **Pilar 4 skoring (bobot 20 poin) dan seluruh "Tahap 4: Rekonsiliasi T+2" tidak bisa diimplementasikan dari sumber publik sebagaimana dirancang.**
- Fitur NeoBDM yang sebenarnya kemungkinan berbasis **perubahan kepemilikan bulanan** (jauh lebih kasar) + data registrasi, bukan audit wash-sale harian. Klaim "broksum bisa bohong, KSEI T+2 tidak bisa dimanipulasi" **secara praktis tidak tersedia** pada resolusi itu.

**Aksi:** Sebelum apa pun — konfirmasi ke KSEI: dataset apa, format apa, frekuensi apa, lisensi apa. Ini menentukan apakah produk punya "senjata utama" atau tidak.

---

### C2 — Broker Summary EOD historis = produk **berlisensi**, bukan gratisan
**Klaim Gemini:** "NeoBDM tidak punya data eksklusif. Data publik, dapat diakses siapa saja … secara teknis menduplikasi bukan hal rumit."

**Realita:**
- Broker summary EOD *hari ini* memang bisa dilihat gratis di banyak platform sekuritas/vendor — tetapi **mengambilnya secara otomatis** melanggar ToS platform tsb., dan **redistribusi/olah komersial** menyentuh lisensi data IDX.
- **Broker summary historis** (per emiten × per broker × per hari, 900+ emiten, multi-tahun) untuk backfill matriks W4–W2 adalah **produk berbayar** (mis. IDX DataShop / vendor berizin). Estimasi biaya "Rp200–300 ribu/bulan" di blueprint **mengabaikan biaya lisensi data sepenuhnya** — ini bisa jadi komponen biaya terbesar, bukan server.

**Aksi:** Putuskan jalur — (a) lisensi vendor resmi (biaya, tapi bersih), (b) scraping platform (murah, tapi ToS + lisensi bermasalah + rapuh), atau (c) hybrid. Blueprint diam-diam mengasumsikan (b) sambil bilang "mudah".

---

### C3 — "Sultan / Zombi" dan konsentrasi intraday butuh data yang **sudah hilang sejak 2021**
**Klaim Gemini:** Sultan = "broker + order size thickness + konsentrasi … eksekusi miliaran dalam 1–2 match". Zombi = "akun dormant mendadak aktif". Freq Analyzer = "tangkap pecah order otomatis / HFT".

**Realita:**
- Sejak Agustus 2021, BEI **menyembunyikan kode broker dan flag D/F selama jam perdagangan**; baru dibuka setelah tutup. **Data per-order / per-match tidak publik.**
- Dari **EOD broker summary saja**, Anda hanya punya total buy value, sell value, volume per broker per emiten per hari. Tidak ada ukuran order, tidak ada frekuensi, tidak ada timestamp.
- Artinya "Sultanmologi", "Zombimologi", dan "Freq Analyzer" **tidak bisa direproduksi** dengan setia. Paling jauh Anda bisa membuat proksi kasar (mis. konsentrasi nilai net EOD di 1 broker) dan **harus jujur menyebutnya proksi**, bukan fitur setara NeoBDM.

**Aksi:** Coret fitur ini dari MVP, atau turunkan ekspektasi jadi "broker concentration EOD" eksplisit.

---

### C4 — Semua bobot & threshold skoring adalah **angka karangan**
**Klaim Gemini:** Pilar 1 CR3 = 30 pts, Pilar 2 = 25, Pilar 3 = 25, Pilar 4 = 20. Threshold: CR3 ≥ 2.0 → +30; 1.5–2.0 → +20; < 0.9 → −15. Skor ≥ 80 → STRONG ENTRY. Contoh: "$BBCA skor 93".

**Masalah:**
- Tidak ada satu pun angka ini yang berasal dari data. Tidak ada backtest, tidak ada dasar statistik, tidak ada dataset. Dipresentasikan dengan presisi palsu.
- Rumus CR3 sendiri arbitrer: `Σ(top-k buy value) / Σ(top-k sell value)`. Membandingkan konsentrasi sisi beli vs sisi jual adalah *ide* yang wajar, tapi bentuk rasio spesifik + cutoff-nya perlu dibuktikan.
- Pilar 4 memberi 20 poin ke sinyal yang (per C1) tidak punya data.

**Aksi:** Jika mau ada "skor", ia harus lahir dari **backtest berlabel** (definisi outcome: return d+N setelah sinyal, hit-rate, dll.) di atas data historis nyata. Sampai itu ada, skor tunggal 0–100 adalah *security blanket*, bukan alat.

---

### C5 — Rolling window pakai **hari kalender**, salah untuk bursa
**Klaim Gemini (SQL):**
```sql
SUM(CASE WHEN txn_date BETWEEN CURRENT_DATE - INTERVAL '5 days' AND CURRENT_DATE ...) AS W1_flow,
SUM(CASE WHEN txn_date BETWEEN CURRENT_DATE - INTERVAL '20 days' ...) AS W4_flow
```
`INTERVAL '5 days'` / `'20 days'` = hari kalender. Akhir pekan + libur bursa (dan Indonesia banyak libur) membuat "W1" kadang berisi 3 hari bursa, kadang 5. Window jadi tidak konsisten antar emiten dan antar waktu.

**Aksi:** Semua window harus `ROW`-based di atas `trading_calendar` (hari bursa ke-n), bukan interval tanggal. Menariknya Gemini *menyebut* butuh trading calendar di bagian lain — tapi contoh eksekusinya melanggar sendiri.

---

### C6 — "Derivative data ⟹ aman hak cipta" itu menyesatkan
**Klaim Gemini:** "Kami tidak publikasikan ulang data mentah, hanya data turunan (CR3, skor, sparkline) → itu kekayaan intelektual kami, bebas klaim."

**Masalah:**
- Menghitung metrik di atas sumber **tidak membersihkan cara perolehan sumbernya**. Kalau tick/broksum diperoleh melanggar ToS platform atau lisensi IDX, menghitung CR3 di atasnya tidak menghapus pelanggaran akuisisi.
- Klaim/kontrak (ToS) menempel pada **ekstraksi**, bukan hanya redistribusi. "Kami cuma menampilkan angka olahan" bukan pembelaan kalau pipeline-nya menyedot data berlisensi tanpa izin.

**Aksi:** Nasihat legal lokal betulan sebelum rilis komersial. Minimal, jalur data harus bisa dijelaskan asal-usulnya (licensed vendor / izin tertulis).

---

### C7 — Alert "Entry/Hold/Exit + target + stop-loss" berbayar ≈ nasihat investasi
**Klaim Gemini:** Disclaimer "bukan financial advice, DYOR" dianggap cukup. Tapi template Telegram yang dirancang berisi: `🎯 Target Area : Rp10.500–10.800`, `🛑 Stop Loss : Rp10.000`, `👉 Action: Sell / Take Profit`.

**Masalah:**
- Itu **rekomendasi beli/jual dengan level harga**, bukan "statistik murni". Di Indonesia ini bersinggungan dengan aturan OJK soal Penasihat Investasi berizin dan gelombang penindakan "influencer/afiliator saham / pom-pom".
- Disclaimer membantu tapi tidak otomatis melindungi kalau produknya *berperilaku* seperti signal-service berbayar.

**Aksi:** Pilih sikap sadar — (a) benar-benar hanya tampilkan metrik/anomali tanpa "action/target/SL", atau (b) konsultasi izin. Jangan setengah-setengah.

---

### C8 — Mapping broker (YP=ritel, AK/BK/RX=asing) = folk knowledge, dan sebagian tak perlu
- Contoh mapping itu pengetahuan komunitas ritel, **bukan** ground truth mapping NeoBDM. Broker "berafiliasi asing" (AK/BK/RX/KZ/ZP) juga mengeksekusi untuk institusi domestik bahkan ritel — melabeli **seluruh broker** sebagai "asing" itu kasar.
- Lagipula, broker summary EOD **sudah memuat flag domestik/asing (D/F) per sisi transaksi**. Untuk "net foreign", pakai flag itu, jangan tebak dari kode broker.
- Mapping broker tetap berguna untuk *dugaan* institusi vs ritel, tapi harus: (a) ter-versioned, (b) diperlakukan sebagai hipotesis, (c) idealnya dikalibrasi ulang dari data.

---

### C9 — Label kepercayaan Gemini miskalibrasi
`[Certain]` dipakai untuk: "NeoBDM pakai TimescaleDB", "komponen paling powerful adalah cross-validation KSEI T+2", pembobotan 4 pilar, "sangat bisa diduplikasi". Semua ini **tebakan/asumsi**, bukan fakta terverifikasi. Saat membaca ulang dokumen itu, abaikan tag-nya dan nilai tiap klaim sendiri.

---

## 4. Yang belum dipertimbangkan sama sekali

| Celah | Kenapa penting |
|---|---|
| **Papan perdagangan (RG / TN / NG)** | Wash sale & crossing sering di papan negosiasi. Net flow harus dipisah per papan, kalau tidak sinyal bias. |
| **Corporate action** (split, right issue, waran, dividen saham) | Memutus deret jumlah saham & perbandingan nilai. Wajib tabel adjustment. |
| **Market maker / liquidity provider / ETF creation-redemption / rebalancing indeks** | Menghasilkan flow "mirip smart money" yang bukan. Tanpa filter, false positive. |
| **Foreign nominee / "asing gadungan"** | Uang lokal lewat entitas offshore tercatat "Foreign". Justru alasan butuh KSEI — yang (C1) tak tersedia granular. |
| **Survivorship & lookahead bias** di backtest skor | Emiten delisting, data yang baru "final" H+1, dsb. Backtest naif akan overstate. |
| **Biaya lisensi data** | Tidak ada di anggaran. Bisa > biaya server. |
| **Rekonsiliasi volume broksum vs volume tape** | Cek integritas ingest (jumlah broksum harus rekonsiliasi ke total volume emiten). |

---

## 5. Validasi klaim infrastruktur (sebagian besar wajar)

| Klaim | Nilai |
|---|---|
| 1 VPS/EC2, 4 vCPU, 8–16 GB, Docker Compose | OK untuk ~900 emiten EOD. |
| "5 tahun data broker flow ~20–30 GB terkompres" | Orde besaran masuk akal (~200–250 jt baris; ~10–30 GB dengan index). |
| "Scrape 900+ emiten < 5 menit" | Optimistis jika per-halaman; realistis jika satu file bulk. |
| Telegram limit 30 msg/s global, 20 msg/min per grup | Sesuai limit informal Telegram yang terdokumentasi. |
| QRIS via Xendit/Midtrans/Tripay, MDR ~0.7% | Akurat. Auto kick/unban via bot saat langganan habis — workable. |
| pdfplumber untuk "PDF KSEI harian" | Memecahkan masalah yang sebagian **imajiner** — data kepemilikan KSEI publik umumnya CSV/XLSX bulanan, bukan PDF harian. |

---

## 6. Peta ulang metodologi (realistis, dari sumber publik/berlisensi)

```
Feasible sekarang (EOD broker summary + trading calendar):
  - Net flow per kasta pakai flag D/F bawaan broksum          [kuat]
  - Dugaan institusi vs ritel via broker mapping (hipotesis)   [sedang]
  - Broker concentration EOD (bukan intraday)                  [sedang]
  - Rolling flow consistency D/W berbasis hari bursa           [kuat]
  - Harga vs flow (kuadran akumulasi/distribusi)               [kuat sbg framing]

Feasible terbatas (kepemilikan KSEI BULANAN):
  - Tren kepemilikan asing/institusi bulanan per emiten        [kasar, lagging]
  - "Sanity check" arah flow multi-bulan vs kepemilikan        [bukan audit T+2]

TIDAK feasible dari sumber publik (butuh data yang hilang/berlisensi):
  - Audit wash-sale T+2 harian per emiten                      -> jangan janjikan
  - Sultan/Zombi tagging berbasis order size/frekuensi         -> jangan janjikan
  - Freq Analyzer / jejak HFT intraday                          -> jangan janjikan
```

---

## 7. Rekomendasi — 3 gate sebelum menulis kode

**Gate 1 — Kejelasan sumber data (blocking).**
Jawab dengan bukti, bukan asumsi:
- KSEI: dataset publik apa persisnya? Frekuensi (harian/bulanan)? Format? Lisensi? → menentukan nasib "senjata utama".
- Broker summary historis: lisensi vendor (biaya?) atau scraping (ToS?)? Berapa tahun backfill realistis?
- Apakah flag D/F ada di sumber broksum yang akan dipakai?

**Gate 2 — Definisi sukzes yang terukur.**
Sebelum bikin "skor", tetapkan: outcome apa yang diprediksi (return H+1/H+3/H+5? probabilitas breakout?), metrik evaluasi (hit-rate, expectancy), dan periode uji. Tanpa ini, skor 0–100 tidak bisa divalidasi.

**Gate 3 — Sikap legal.**
Putuskan: produk metrik/anomali murni (tanpa "action/target/SL") **atau** signal-service (butuh kajian izin OJK). Pilih satu.

**Setelah 3 gate lolos**, urutan bangun yang masuk akal:
1. `trading_calendar` + skema kanonik (`daily_broker_flows`, `broker_mappings` ter-versioned).
2. Ingest EOD broker summary + price bars → validasi rekonsiliasi volume.
3. Net flow per kasta (pakai flag D/F) + rolling D/W berbasis hari bursa.
4. Ingest kepemilikan KSEI bulanan (apa pun yang benar-benar tersedia) → tren, bukan audit.
5. Backtest kerangka sinyal → **baru** kalibrasi bobot/threshold.
6. (Opsional) UI screener + alert — setelah data plane stabil.

---

## 8. Pertanyaan untuk Anda

1. Sudah cek langsung dataset publik KSEI? Apa yang benar-benar bisa diunduh (link + frekuensi + format)?
2. Anggaran untuk lisensi data broker summary historis — ada, atau harus jalur scraping?
3. Produk akhir: **metrik/anomali murni** atau **signal-service Entry/Hold/Exit**? (mempengaruhi legal + desain)
4. Target awal: seluruh ~900 emiten, atau watchlist kecil dulu untuk validasi metodologi?
5. Mau saya lanjut ke inventaris konkret endpoint/file resmi IDX + KSEI (masih tanpa kode), atau bedah satu komponen metodologi lebih dalam?
