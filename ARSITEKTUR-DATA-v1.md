# Arsitektur Data & Analitik Vektor Gauss — v1 (2 Nov 2026)

Dokumen ini menjawab: *"Pastikan data lengkap; rancang arsitektur manajemen dan analisis data berkelas institusi untuk IHSG."*
Semua angka di §1–§2 **diukur langsung dari Postgres produksi pada 2 Nov 2026** memakai `scripts/data_audit.py`
(baca-saja, dapat diulang) ditambah empat pemeriksaan lanjutan; bukan dari ingatan atau dokumen lama.

## 0. Ringkasan jujur

1. **Data harga & arus asing kita kuat; data broker dan penyesuaian harga yang kurang.** Harga harian 2020→2026 bersih
   (nol pelanggaran OHLC, kalender cocok dengan bar, tidak ada hari bursa yang hilang). Tetapi:
2. **Sisi penjual broker tidak pernah kita simpan.** Feed mengirim dua struktur dalam satu respons: `brokers` (20 *net buyer*
   teratas) dan `broker_levels` (25 pasang pembeli-kotor vs penjual-kotor teratas). Parser kita hanya membaca yang pertama
   (pada 30 Okt: median 19 baris bernet positif dan 1 negatif per saham, 196 saham). Akibatnya tag distribusi (mis. `BROKER-ASING-DIST`, n = 14)
   **kelaparan secara struktural** — itu bukan temuan pasar, itu lubang pengumpulan. Responsnya tidak diarsipkan per
   tanggal (file ditimpa), jadi sisi jual untuk 105 hari lalu harus ditarik ulang.
3. **Harga kita mentah (belum disesuaikan).** Jumlah dividen per saham *sudah ada* (di kolom `proceed_ratio`, 4.136 dari
   4.137 baris), begitu pula rasio split — jadi deret *total-return* bisa dibangun tanpa sumber baru. Sampai itu ada,
   return H5/H10 sekitar ex-date tidak akurat.
4. **"Kelas dunia" harus didefinisikan jujur.** Yang bisa kita capai dengan data gratis/murah: *disiplin* institusional
   (point-in-time, lineage, uji statistik ketat, biaya realistis, tata kelola promosi sinyal). Yang tidak bisa:
   data tick/L2, fundamental analis, riset konsensus. Arsitektur ini mengejar yang pertama dan menyatakan terang
   yang kedua belum ada.
5. **Pagar pengaman tetap:** tanpa LLM/skor kotak-hitam di mesin; angka jujur dulu; tanpa perintah beli/jual; disclaimer OJK.

## 1. Kelengkapan data — nilai per dataset

Nilai: **A** lengkap & terverifikasi · **B** bisa dipakai dengan catatan · **C** parsial/bias · **D** tidak ada.

| Dataset | Cakupan terukur | Nilai | Catatan utama |
|---|---|:-:|---|
| Harga EOD (`price_bars`) | 1,39 jt baris, 2020-01-02→2026-10-30, 722→965 saham/hari | **A−** | OHLC 0 pelanggaran; 99,8% baris dari sumber primer IDX. Mentah, tidak disesuaikan (§2-Q1). |
| Kalender (`trading_calendar`) | 1.645 hari | **A** | 82 celah hari-kerja, **0** yang menunjukkan hari bursa hilang (uji: `prev` hari berikutnya = close terakhir). |
| Arus asing harian | di `price_bars`, null 0% | **A** | 35% baris bernilai 0 = saham tidak ditransaksikan asing, bukan data hilang. |
| Indeks (`index_bars`) | 57 indeks; COMPOSITE tanpa celah | **B** | 10 indeks sektor lama berhenti 30 Apr 2021 (ganti klasifikasi IDX-IC); deret sektor tidak menyambung (§2-Q6). |
| KSEI bulanan | 94 bulan 2018-12→2026-09, tanpa celah | **B** | Basis = saham *scripless* di KSEI, bukan total tercatat: 419 dari 980 saham < 95% dari `listed_shares` (284 < 50%). `holders_count` **100% null**. |
| Aksi korporasi | 4.137 dividen, 371 split, 347 rights, 22 saham | **B+** | 0 perubahan jumlah saham tanpa catatan CA. Satuan dividen belum diverifikasi (ada nilai `0,1504`). |
| Broker (`broker_flows`) | 105 hari, 952 saham, 94 broker; sejak 3 Jun 2026 | **C** | Hanya 20 net-buyer teratas; jual-kotor ≈ 32% nilai transaksi vs beli ≈ 75% (§2-Q2). |
| Inventori broker | 4.579 baris, top-6 | **C** | Kumulatif 80 hari; sempit. |
| Disclosure >5%/insider | 1.332 pengumuman, 2.975 holding | **B** | Parse PDF; cakupan sejak 2023-09. |
| Profil/sektor | 2 snapshot (9 Sep, 3 Okt) | **C** | Tidak point-in-time; 1 saham tanpa sektor (GOTOM). |
| Universe / delisting | 989 saham pernah ada, 26 menghilang | **C** | `delisting_date` **0 terisi**; 25 saham hilang tanpa alasan tercatat. |
| Margin / short-sell | 12 hari (24 Sep) | **D** | Basi dan tidak masuk EOD. |
| Fundamental (laporan keuangan) | — | **D** | Belum ada sama sekali. |
| Makro / FX / komoditas / global | — | **D** | USD/IDR, BI-rate, yield SBN, batubara/CPO/nikel belum ada. |
| Order flow tick / L2 | 6 baris `done_by_price` | **D** | Sengaja; fitur berbayar, bukan untuk EOD. |
| Lineage mentah (`raw_artifacts`) | 27.989 baris feed → **hanya 963 path** | **C** | File feed ditimpa tiap tarikan; 27.886 sha256 tercatat tetapi isinya hanya yang terakhir (§2-Q3). |

## 2. Temuan kualitas (bukti → akibat → perbaikan)

| # | Temuan (terukur) | Akibat | Perbaikan |
|---|---|---|---|
| **Q1** | Harga **tidak disesuaikan**. Lompatan |ret|>60% tanpa CA ±3 hari: **48** (dari ±1,39 jt baris). Sebagian tampaknya nyata (saham Rp1→Rp2 di 2024: BTEK, MKNT), sebagian tidak (FORU 215,95→4.020, dari hari sumber cadangan). Dividen & split tersedia di `corp_actions`. | Return H5/H10 dan semua "edge" sedikit bias pada saham yang ex-date di jendela. | Tabel `adj_factor` (split, rights, dividen) → deret `close_adj` dan *total return*. Simpan mentah **dan** disesuaikan. |
| **Q2** | `brokers` = top-20 *net buyer* (neg_net = 0 pada semua tarikan uji). `broker_levels` (25 level beli vs jual kotor) **tidak diparse**. `limit`, `broker_net`, `flow` tidak mengubah ini. | Distribusi tak teramati; semua tag broker miring ke sisi beli; DOM-RETAIL/ASING-DIST kelaparan. | Parse `broker_levels` dari respons yang sama (0 kuota tambahan); simpan sisi jual + batas atas sisi yang tidak terlihat; tarik ulang 105 hari. |
| **Q3** | 963 path untuk 27.989 tarikan; satu file ditimpa 105 kali. | Tak ada jejak audit; tak bisa diparse ulang bila parser diperbaiki. | Penyimpanan *content-addressed* `…/{sha[:2]}/{sha}.json.zst`; `raw_artifacts` mengacu ke sha. |
| **Q4** | 14/15/25 Sep memakai sumber cadangan: ±0,6% baris (≈6 per hari, ±17 total) berharga non-bulat/disesuaikan (FORU, PBSA); 11 dari 1.924 baris menyimpang >0,5% dari `prev` hari berikutnya. | Return di sekitar tiga hari itu salah untuk ≈17 baris saham-hari. | Tandai `source_quality='adjusted'`, ganti dengan harga mentah dari `prev` hari bursa berikutnya bila konsisten, kalau tidak NULL. |
| **Q5** | `open` di luar [low, high]: 16 baris traded **hanya pada 16 Okt 2026** (ditambah 112 baris ber-`open` = 0); hari lain 0. | `open` tak boleh dipercaya hari itu. | Kontrak data: `open` dibuat NULL bila di luar rentang; alarm pada hari lain. |
| **Q6** | 10 indeks sektor lama berhenti 2021-04-30; 11 IDX-IC mulai setelahnya. | Rotasi sektor lintas 2021 tidak valid. | Peta JASICA→IDX-IC atau mulai deret sektor di Mei 2021; jangan menyambung diam-diam. |
| **Q7** | KSEI: median total/listed = 0,994, tetapi p10 = 0,21. Mesin memakai basis *scripless* (terdokumentasi di `engine.py`). | `%asing` bukan `% dari saham beredar`; jangan dibandingkan lintas saham tanpa rasio cakupan. | Simpan `ksei_coverage = total/listed` dan tampilkan; saring saham < 50% dari tag KSEI. |
| **Q8** | `delisting_date` tidak pernah terisi; 25 saham berhenti tanpa alasan. | Survivorship terbatas: saham yang hilang *masih ada* di data (bagus), tetapi alasan (delisting/merger/suspensi) tak diketahui. | Isi dari pengumuman IDX; tambah `security_master`. |
| **Q9** | `holders_count` 100% null; `margin_securities` berhenti 24 Sep. | Dua baris di `INVENTARIS-DATA-DB.md` terlalu optimis. | Dikoreksi di dokumen itu hari ini. |
| **Q10** | `market_breadth` berhenti 16 Okt: **dijelaskan** (butuh 10 hari bursa ke depan; selisih terukur 10). `raw_artifacts` bertanggal 2026-12-31: **dijelaskan** (penanda tahun untuk `ksei_ca_schedule`). | D7 & D8 selesai. | Tidak ada. |

Hal yang diperiksa dan **bersih**: kalender vs bar, `net_val = buy − sell` (0 selisih), `volume`/`value` negatif (0), nilai/volume
tidak masuk akal (0), CA tanpa jumlah saham berubah (0), COMPOSITE tanpa hari hilang.

## 3. Celah data vs kebutuhan analitik

| Kebutuhan | Status | Sumber yang realistis | Biaya | Prioritas |
|---|---|---|---|---|
| Deret return sesuai CA (total return) | data ada, belum dibangun | `corp_actions` sendiri | rendah (hari) | **P0** |
| Sisi jual broker + sampel tak bias | ada di respons, tak diparse | feed yang sama | rendah (kode) + ±6.5k panggilan | **P0** |
| Arsip mentah tak-berubah | bug penyimpanan | lokal | rendah | **P0** |
| Riwayat broker 2020→Mei 2026 | sumber punya | feed (kuota Popular 21k/hari) | ±300 ribu panggilan ≈ 15 hari kuota (perkiraan) | **P1** — satu-satunya jalan menguji tag broker lintas rezim (2020, 2022) |
| Universe/security master + sektor point-in-time | parsial | IDX profil + pengumuman | sedang | **P1** |
| Keanggotaan indeks (LQ45/IDX30/KOMPAS100) historis | tidak ada | pengumuman IDX | sedang | **P1** |
| Fundamental (EPS, BV, ROE, tanggal rilis) | tidak ada | laporan keuangan IDX | tinggi | P2 (untuk faktor kualitas/nilai) |
| Makro/FX/komoditas | tidak ada | BI, sumber publik (cek lisensi) | sedang | P2 |
| Short-sell eligible, marjin harian | basi | IDX | rendah | P2 |
| Tick/L2, konsensus analis | tidak ada | berbayar | tinggi | **tidak dikejar** |

## 4. Arsitektur data target

Prinsip: (1) **mentah tak pernah ditimpa**; (2) **semua tabel berstatus point-in-time** — setiap fakta punya `event_time` (kapan
terjadi) dan `known_at` (kapan kita tahu), sehingga backtest tidak melihat masa depan; (3) **kontrak data diuji otomatis**;
(4) **setiap angka di layar bisa ditelusuri ke berkas mentah**.

```
 SUMBER ──► L0 RAW (immutable, content-addressed, zstd)         arsip/{source}/{sha[:2]}/{sha}.zst + manifest
              │  parser versi N (kode di git, deterministik)
              ▼
            L1 BRONZE  parsed apa adanya, + parser_version, raw_sha, known_at   (price_bars, broker_flows, …)
              │  kontrak data (DQ gate) ── gagal → karantina + alarm, tidak masuk L2
              ▼
            L2 SILVER  curated & point-in-time
              • security_master (ticker, perubahan nama, listing/delisting, papan, sektor PIT, keanggotaan indeks)
              • calendar_master
              • adj_factor → close_adj, total_return_index
              • broker_net_full (buyer + seller gross, batas atas sisi tak terlihat)
              ▼
            L3 GOLD    fitur (signal_components) versi + tabel `feature_registry` (definisi, window, data yang dipakai)
              ▼
            L4 RESEARCH  eval, backtest, paper — dataset dibekukan dengan hash (snapshot Parquet/DuckDB)
              ▼
            L5 SERVING   web FastAPI, Telegram digest (hanya membaca L3/L4)
```

Keputusan teknologi (sengaja konservatif — satu orang, satu mesin):
- **Tetap Postgres** sebagai sumber kebenaran; **tidak** menambah Kafka/Spark/Snowflake — volume 1,4 jt baris/6 tahun tidak membutuhkannya.
- **Parquet + DuckDB** hanya untuk snapshot riset yang dibekukan (reproduksibel: hash dataset tertulis di hasil eval).
- **Orkestrasi**: tetap Task Scheduler + `eod.sh`, tetapi tiap fase mencatat ke `run_log` dan *gate* DQ menghentikan tahap berikutnya.
- **Skema**: migrasi bernomor (sekarang skema dibuat dari kode); setiap tabel punya `source`, `source_tier`, `fetched_at` (sudah ada) **+ `raw_sha`, `parser_version`**.
- **Ketahanan**: backup mingguan ke GitHub Release (sudah diperbaiki, G11) + uji pulih bulanan ke skema `scratch`.
- **Keamanan**: kunci di `.env`, tidak di repo; arsip mentah mengandung respons berlisensi → repo privat.

### Kontrak data (gate sebelum L2)
Diturunkan langsung dari `scripts/data_audit.py`. Contoh aturan *blocking*: hari bursa dengan baris harga < 50% median 60 hari;
`high < low`; `close` di luar [low, high]; `net_val ≠ buy − sell`; rasio nilai broker/harga di luar [0, 2]; dividen negatif.
Aturan *peringatan*: `open` di luar rentang, KSEI `total/listed` < 0,5, sumber cadangan terpakai.

## 5. Arsitektur analitik

Yang sudah baik dan dipertahankan: gerbang promosi 3 status (n ≥ 50, rentang ≥ 60 hari, edge H5 positif setelah ±170 bp),
jam forward-test (`VG_FORWARD_SINCE`), tag pra-terdaftar, rapor jujur.

Celah metodologi (belum diukur, bukan sekadar teori):
1. **n efektif ≪ n tercatat.** `n` menghitung saham-hari. Sinyal pada hari yang sama berkorelasi lewat pasar/sektor, dan jendela H5/H10
   saling tumpang tindih. Usulan: *date-clustered bootstrap* / Newey-West untuk selang kepercayaan edge; gerbang memakai batas bawah, bukan rata-rata.
2. **Pembanding tunggal** (rata-rata sama-bobot universe). Usulan: edge dinetralkan terhadap faktor ukuran, likuiditas, beta, sektor.
3. **Uji berganda.** 24 tag × 3 horizon = 72 uji sebelum irisan (sektor, likuiditas, rezim) — jumlah sebenarnya jauh lebih besar. Usulan: kontrol FDR (Benjamini-Hochberg) dan laporkan *deflated* hasil terbaik.
4. **Validasi.** Walk-forward dengan *purge/embargo* seukuran horizon; jangan memilih ambang pada seluruh sampel.
5. **Realisme eksekusi IDX.** Lot 100 saham, tick bertingkat, ARA/ARB (saham tak bisa dibeli/dijual di batas), T+2, **tanpa short** (hanya long-only atau
   daftar short-sell terbatas), kapasitas = persentase ADV, dampak harga untuk saham tipis. Biaya ±170 bp pulang-pergi sudah konservatif untuk ritel; untuk
   dana institusi dampak lebih besar.
6. **Peluruhan alpha.** Pantau edge bergulir 60/120 hari per tag; tag yang lolos otomatis kembali ke "masa percobaan" bila batas bawah < biaya.
7. **Rezim.** Edge per rezim (bull/bear/volatilitas) — butuh riwayat broker panjang (§3, P1).

Keluaran institusional yang realistis: *laporan faktor* per tag (edge, selang kepercayaan, kapasitas, rezim), *daftar pantau* yang bisa
diaudit, dan *kartu data* (kelengkapan hari ini) — bukan rekomendasi transaksi.

## 6. Peta jalan & checklist

Fase dikerjakan berurutan; tiap butir selesai hanya bila bukti yang tertulis terpenuhi.

**Fase 0 — Integritas** — dikerjakan 2 Nov; hasil terukur di §9
- [x] **K1** (P0) Arsip mentah content-addressed + `raw_sha`/`parser_version` di tabel bronze. *Selesai bila:* dua tarikan berbeda untuk ticker yang sama tersimpan terpisah dan bisa diparse ulang.
- [x] **K2** (P0) Parse `broker_levels` + simpan sisi jual dan batas atas sisi tak terlihat; tes dengan respons BBCA 30 Okt. *Selesai bila:* tag distribusi bisa muncul dan n `BROKER-ASING-DIST` naik dari 14 pada data baru.
- [x] **K3** (P0) `adj_factor` + `close_adj` + total-return dari `corp_actions`; verifikasi 20 kasus manual (split, rights, dividen). *Selesai bila:* eval H5/H10 dapat dihitung ulang dengan `close_adj` dan selisih ke versi mentah dilaporkan.
- [x] **K4** (P0) Jadikan `scripts/data_audit.py` gerbang EOD (peringatan ke Telegram; blocking tahap berikutnya). *Selesai bila:* insiden 14/15/25 Sep tersuntik di skema `scratch` menghentikan tahap compute.
- [x] **K5** (P1) Perbaiki Q4/Q5 (tandai baris sumber cadangan, NULL-kan `open` di luar rentang).

**Fase 1 — Cakupan (± 1–3 minggu, kuota memengaruhi)**
- [ ] **K6** (P1) Tarik ulang sisi jual 105 hari (perkiraan ±6,5 ribu panggilan).
- [ ] **K7** (P1) Backfill broker 2023→Mei 2026, lalu 2020→2022 (perkiraan total ±300 ribu panggilan ≈ 15 hari kuota; **perkiraan, belum diuji**).
- [ ] **K8** (P1) `security_master`: delisting, perubahan nama, sektor PIT, keanggotaan indeks historis.
- [ ] **K9** (P1) Peta JASICA→IDX-IC (Q6) dan `ksei_coverage` (Q7).

**Fase 2 — Riset institusional (± 2–4 minggu)**
- [ ] **K10** (P1) Selang kepercayaan date-clustered untuk `eval_tags`; ukur n efektif per tag dan laporkan selisihnya.
- [ ] **K11** (P1) Netralisasi faktor (ukuran, likuiditas, beta, sektor) + FDR.
- [ ] **K12** (P2) Walk-forward dengan purge/embargo; edge per rezim.
- [ ] **K13** (P2) Model biaya & kapasitas (lot, tick, ARA/ARB, ADV).

**Fase 3 — Perluasan data (hanya bila fase 0–2 menunjukkan manfaat)**
- [ ] **K14** (P2) Fundamental (laporan keuangan IDX) dan tanggal rilis.
- [ ] **K15** (P2) Makro/FX/komoditas, short-sell eligible, marjin harian.

## 7. Yang sengaja tidak dilakukan
- Tidak ada LLM/skor kotak-hitam di mesin (jaminan `vektor-gauss-no-ai-model`); AI hanya boleh sebagai lapisan penjelas.
- Tidak mengejar data tick/L2 atau konsensus analis: berbayar, dan hasil riset menunjukkan edge EOD gratis pun belum melewati biaya.
- Tidak ada rekomendasi beli/jual; disclaimer OJK tetap di setiap layar.

## 8. Keputusan yang dibutuhkan dari pemilik
1. Setuju memulai Fase 0 (K1–K5)? Ini tidak memakai kuota feed dan memperbaiki lubang terbesar.
2. Backfill broker 2020→2026 memakai kuota Popular ± 15 hari — setuju, atau cukup 2023→2026 dulu?
3. Fundamental (K14): perlu atau cukup fokus arus & harga?

## 9. Hasil Fase 0 (2 Nov 2026) — terukur, termasuk yang belum terbukti

**K1 arsip mentah** — `artifacts.store(immutable=True)` menyimpan `…/{sumber}/{kunci}/{sha16}.json`; byte identik dedupe, byte berbeda
disimpan terpisah. Dipakai untuk broker, riwayat, inventori, harga, dan indeks. `broker_flows`/`broker_levels` kini membawa `raw_sha` dan
`parser_version` (`vg-broker/2`). Terbukti: tes unit + tarikan nyata BBCA/TLKM/GOTO (berkas baru `BBCA/9f20b13bd46c8d8c.json` ada;
baris → `raw_artifacts` lewat sha). **Belum:** 963 berkas lama tetap seperti adanya (isi lama sudah tertimpa; tak bisa dipulihkan).

**K2 sisi jual broker** — `broker_levels` diparse dan disimpan (25 rung beli + 25 rung jual per saham-hari, terbukti pada 3 saham).
Batas pasti (`broker_bounds.py`) dihitung pada 196 saham likuid 30 Okt dari tarikan langsung:
- lebar interval median **2,5%** dari nilai transaksi (p25 0,6%; p75 4,7%) — cukup sempit untuk dipakai;
- ukuran lama (hanya net buyer) **berada di luar interval pasti pada 139 dari 196 saham (71%)**; contoh BBCA: tercatat **+16,6%**
  nilai transaksi, interval pasti **[−6,3%; −0,1%]**, yaitu tanda yang berlawanan;
- pita 2%: ukuran lama menandai 146 saham "akumulasi-asing" dan 2 "distribusi"; dengan batas pasti: 78 akumulasi dan **62 distribusi**.
**Konsekuensi yang harus dibaca terang:** `BROKER-ASING-AKUM/DIST`, `FOREIGN-INST`, `DOM-RETAIL` (dan hasil riset yang bertumpu padanya) memakai
ukuran satu sisi. Verdict "tidak ada edge" untuk tag broker belum menguji pertanyaan yang dimaksud. Tag lama dibiarkan agar forward-test
berjalan utuh; tag baru `BROKER2-ASING-AKUM/DIST` (aturan sama, ukuran benar, hanya menyala bila seluruh interval melewati pita) memulai jam
forward-test baru dan baru menyala pada hari yang punya `broker_levels` (mulai EOD pertama setelah deploy, atau setelah K6).
**Belum terbukti:** edge tag baru (butuh data; K6/K7), dan kenaikan n `BROKER2-ASING-DIST` pada data baru.

**K3 penyesuaian harga** — `adj_factor` (2.673 peristiwa: 2.560 dividen, 72 split, 41 bonus) dan `price_adj`; view `price_bars_adj`
(`close_adj` = split/bonus, `close_tr` = + dividen). Validasi terhadap pergerakan ex-date nyata (`vg adj-report`):
dividen 90% dalam 5% dari faktor, selisih rata-rata +0,6% (tanpa bias tanda); split 67%; bonus 50% (n = 38, kecil). Dua temuan yang
mengubah aturan: tanggal ex KSEI **terlambat satu hari bursa** (harga turun sehari sebelumnya pada 35% dividen dan 74% bonus, vs ±9%
kebetulan) sehingga dipakai hari bursa sebelumnya; dan lonjakan jumlah saham dengan rasio 0,7–1,5 hanyalah penempatan/rights (harga
bergerak ≈1/rasio hanya 31%) sehingga tidak disesuaikan (64 peristiwa). Tidak disesuaikan sama sekali: rights ber-harga-tebus (318;
harga tebus tidak ada di data), dividen dengan DPS mustahil (4), 1.567 peristiwa sebelum bar pertama.
Dampak pada return 5-bar: dividen **+2,2 s.d. +4,2 bp** rata-rata per tahun (0,6–1,0% baris bergeser >50 bp); split/bonus +1 s.d. +3 bp.
Kecil secara rata-rata, terkonsentrasi pada musim dividen — relevan bagi tag yang berkorelasi dengan musim itu.
**Belum:** `eval`/`signal_log` masih memakai close mentah (sengaja, agar forward-test tidak berubah di tengah jalan); memakai `close_tr` untuk
outcome adalah keputusan terpisah.

**K4 gerbang kualitas** — `vg quality-gate` (aturan murni di `quality.py`) dipasang di `eod.sh` sebelum kedua pass `compute`; BLOCK =
harga bolong (<50% median 60 hari), tidak ada baris harga, hari non-bursa, pelanggaran OHLC, close ≤ 0, volume negatif; WARN = harga
parsial, `open` di luar rentang, indeks hilang, sumber cadangan, broker parsial. Lari nyata pada 30 Okt: lolos dengan 1 peringatan
(broker 196 vs normal ≈ 264). **Caveat jujur:** insiden September dibuktikan lewat uji murni berangka insiden + uji kabel `eod.sh`;
uji end-to-end pada skema `scratch` BELUM dijalankan.

**K5 perbaikan harga** — `vg repair-prices`: 17 baris sumber cadangan (6 saham: AHAP, BUVA, ENRG, FORU, PBSA, VKTR) berharga
tersesuaikan diganti ke harga mentah dari `prev` hari bursa berikutnya (14 Sep memakai faktor vendor yang konstan, terbukti sama pada 15
dan 25 Sep); `open` di luar rentang → NULL (16 baris; kini juga di saat ingest). Efek: sinyal 14 Sep–30 Okt dihitung ulang, 350 outcome
dihitung ulang, 7 `close` di `signal_log` dikoreksi. Tiap baris yang disentuh membawa `quality_note`. Idempoten (kueri ulang: 0).

**Mengubah data produksi (semua aditif/terlacak):** tabel baru `broker_levels`, `adj_factor`, `price_adj`, view `price_bars_adj`, kolom
baru `raw_sha`, `parser_version`, `quality_note`, `bkr_foreign_net_lo/hi`; 17 baris harga + 16 `open` diperbaiki; sinyal 14 Sep–30 Okt
dihitung ulang; 3 saham ditarik ulang untuk uji ujung-ke-ujung. Backup `backup-2026-11-02` mendahului semua ini.
