# Inventaris Data di Database — apa yang kita punya, dan bisa jadi apa

**Dihitung langsung dari Postgres pada 2026-10-31** (bukan dari ingatan). Melengkapi `INVENTARIS-SUMBER-DATA.md`
(yang membahas *sumber* dan lisensi). Dokumen ini membahas *isi* database dan *olahan* yang mungkin.

Legenda status olahan: ✅ sudah ada di VG · 🟡 data lengkap, tinggal dibuat · 🔴 butuh data tambahan.

## 1. Tabel dan cakupannya

| Tabel | Baris | Ukuran | Rentang | Isi |
|---|---:|---:|---|---|
| `price_bars` | 1.393.793 | 611 MB | 2020-01-02 → 2026-10-30 | OHLC, volume, nilai, frekuensi, foreign buy/sell, nonreg, listed/tradable shares, bid/offer (~962 saham/hari) |
| `signal_components` | 1.224.445 | 526 MB | 2020 → 2026-10-30 | 38 fitur turunan per saham-hari (arus asing, RS, ATR, KSEI trend, broker, tiket, float, disclosure) |
| `signal_log` | 1.151.418 | 291 MB | 2020 → 2026-10-30 | 24 tag per saham-hari + hasil ret H1/H5/H10 setelah matang |
| `ownership_monthly` | 80.557 | 85 MB | 2018-12 → 2026-09 | KSEI bulanan: lokal individu/institusi, asing, `detail` per tipe investor, jumlah pemegang (1.038 saham × 94 bulan) |
| `broker_flows` | 411.819 | 84 MB | **2026-06-03** → 2026-10-30 | per broker per saham per hari: beli/jual nilai-volume-frekuensi, net, avg harga, asing/domestik (952 saham, 94 broker, 105 hari) |
| `paper_trades` | 66.240 | 24 MB | 2020 → 2026-10-29 | simulasi mekanis entry/SL/target per sinyal, hasil R-multiple (937 saham) |
| `index_bars` | 68.517 | 20 MB | 2020 → 2026-10-30 | **57 indeks** termasuk COMPOSITE, LQ45, IDX30, dan 11 indeks sektoral (IDXENERGY, IDXFINANCE, …) |
| `raw_artifacts` / `run_log` | 27.447 / 34.033 | 10 / 5 MB | — | arsip respons mentah + log koleksi |
| `disclosure_filings` | 1.332 | 3 MB | 2023-09 → 2026-10-30 | pengumuman kepemilikan >5% / insider (judul, pihak, PDF) |
| `disclosure_holdings` | 2.975 | 1 MB | 2023-09 → 2026-10-30 | hasil parse PDF: pelapor, jabatan, saham sebelum/sesudah, harga, arah (2.891 baris ber-arah, 242 saham) |
| `corp_actions` | 4.855 | 3 MB | 2015 → 2026-10-22 | dividen tunai 4.118 · perubahan jumlah saham 371 · rights 344 · dividen saham 22 |
| `broker_inventory` | 4.579 | 1,5 MB | 2026-06-03 → 2026-10-29 | kumulatif net per broker (top-6 per saham likuid) |
| `ticker_profile_history` | 1.059 | 0,3 MB | 2 snapshot (9 Sep, 3 Okt) | sektor, sub-sektor, industri, papan, status — **962 saham ber-sektor** |
| `market_breadth` | 1.635 | 0,3 MB | 2020 → 2026-10-16 | benchmark harian (rata-rata semua saham dan saham likuid) untuk menghitung edge |
| `trading_calendar` | 1.645 | 0,2 MB | 2020 → 2026-10-30 | hari bursa |
| `trading_halts` | 46 | 0,1 MB | 2026-07 → 2026-10-23 | suspend/halt per saham |
| `margin_securities` | 3.268 | 0,9 MB | 2026-09-07 → 2026-09-24 (12 hari) | daftar saham marjin + harga |
| `done_by_price` | 6 | — | 2026-09-24 (1 hari) | order flow per level harga (sekali tarik, mahal) |
| `broker_affiliation_groups` | **0** | — | — | **kosong** (rencana pengelompokan broker satu grup) |

## 2. Data → olahan

### A. Harga dan transaksi (`price_bars`, 2020→)
- ✅ ATR, RS vs IHSG 5/10/20d, vol surge, avg ticket + z, bid/offer imbalance, nonreg ratio, float ratio, seasonality, kalkulator posisi, screener, ranking.
- 🟡 Kapitalisasi pasar (close × listed_shares), peringkat likuiditas, breadth harian (advance/decline, % saham naik), deteksi ARA/ARB, 52-minggu tinggi/rendah, volatilitas, korelasi antar-saham, **grafik candle per saham**.

### B. Arus asing (foreign buy/sell di `price_bars`, ~834 saham/hari)
- ✅ NFF hari ini dan 1–4 minggu, konsistensi 10 hari, kuadran, tag AKUM/DISTRIBUSI-ASING.
- 🟡 Rotasi asing per sektor, divergensi harga vs arus asing, streak hari berturut, total net asing pasar.

### C. Broker (`broker_flows`, `broker_inventory`)
- ✅ Net asing broker d0/5d, CR1/CR3, top buyer, FOREIGN-INST / DOM-RETAIL / BANDAR-CANDIDATE, tag BROKER-*.
- 🟡 **Inventory Chart per saham** (kumulatif per broker di atas harga), **Broker Stalker** (satu broker → saham apa yang ia kumpulkan), **estimasi harga pokok bandar** (rata-rata harga beli broker teratas vs harga sekarang: apakah mereka "underwater"), peta konsentrasi, net per tipe broker.
- 🔴 Riwayat sebelum 3 Jun 2026 (sumber punya sampai 2020, belum ditarik); pengelompokan broker satu grup (tabel kosong).

### D. Kepemilikan KSEI bulanan (`ownership_monthly`, 2018→)
- ✅ Tren %asing/%ritel 3 bulan, KSEI-KONFIRM/DIVERGEN, KSEI-INST-AKUM, KSEI-RITEL-EXIT.
- 🟡 Balance Position (lokal/asing × tipe investor, dari `detail`), tren jumlah pemegang (`holders_count`), estimasi free float, indikasi nominee/scripless.

### E. Disclosure (`disclosure_filings`, `disclosure_holdings`)
- ✅ Tag INSIDER-AKUM/DIST, SUBSTANTIAL-AKUM/DIST, LAPOR-KEPEMILIKAN-RAMAI.
- 🟡 Halaman insider per saham (siapa, kapan, berapa, harga), papan peringkat insider paling aktif, uji apakah transaksi insider mendahului pergerakan harga.

### F. Aksi korporasi (`corp_actions`)
- ✅ Penyesuaian perubahan jumlah saham (`detect-ca`).
- 🟡 **Kalender dividen** dan dividend yield, peringatan ex-date untuk watchlist, riwayat dividen per saham, studi event di sekitar cum-date.

### G. Indeks dan sektor (`index_bars` 57 indeks + `ticker_profile_history`)
- ✅ IHSG sebagai pembanding RS.
- 🟡 **Rotasi sektor** (kekuatan relatif 11 indeks sektoral vs COMPOSITE), RS saham terhadap indeks sektornya (lebih adil daripada vs IHSG), breadth per sektor, filter sektor di screener.

### H. Sinyal dan evaluasi (`signal_log`, `signal_components`, `market_breadth`, `paper_trades`)
- ✅ Edge per tag (in-sample dan forward), paper trading, gate promosi 3 status.
- 🟡 Edge per sektor / per rezim pasar / per kelas likuiditas ("kapan tag ini bekerja"), kurva edge kumulatif per tag, peluruhan edge menurut horizon, uji konfluensi (dua tag bersamaan), walk-forward.

### I. Operasional (`run_log`, `raw_artifacts`, `trading_calendar`)
- 🟡 Layar riwayat koleksi, peta cakupan dan celah (heatmap hari × sumber), jejak asal setiap angka ("lihat respons mentah").

## 3. Temuan kualitas data (perlu tindak lanjut)

| # | Temuan | Dampak | Perbaikan |
|---|---|---|---|
| D1 | `broker_flows` baru mulai 3 Jun 2026 (105 hari); sumber punya sampai 2020 | Backtest broker hanya ~5 bulan | Backfill lebih dalam (kuota Popular 21k/hari cukup bertahap) |
| D2 | Koleksi broker harian EOD masih memakai batas radar ~60 saham | Cakupan turun lagi diam-diam setelah backfill manual | Jadikan koleksi harian penuh (universe likuid) |
| D3 | `margin_securities` berhenti 24 Sep (hanya 12 hari) | Data basi, tak ada di EOD | Pasang ke EOD atau hapus dari rencana |
| D4 | `ticker_profile_history` hanya 2 snapshot, bukan harian | Perubahan sektor/papan tak terlacak tiap hari | Snapshot berkala (mingguan) |
| D5 | `done_by_price` 6 baris (1 hari) | Order flow per level harga praktis tidak ada | Sengaja on-demand (mahal); tarik hanya untuk saham fokus |
| D6 | `broker_affiliation_groups` kosong | Tidak ada pengelompokan broker satu grup | Isi manual dari domain knowledge bila ingin "clean" ala NeoBDM |
| D7 | `market_breadth` berhenti 16 Okt, sedangkan data lain 30 Okt | Edge hari-hari terbaru belum bisa dihitung | Kemungkinan wajar karena butuh return 10 hari ke depan; **belum diverifikasi** |
| D8 | `raw_artifacts`/`run_log` memuat tanggal logis sampai 2026-12-31 dan 2015-12-31 | Tampak seperti tanggal penanda, bukan data | **Tidak diselidiki**; cek sebelum dipakai untuk UI riwayat koleksi |

## 4. Implikasi untuk UI
Data yang **lengkap tetapi belum punya layar**: sektor (G), kalender dividen (F), insider per saham (E), Inventory Chart / Broker Stalker (C), candle per saham (A). Layar-layar ini masuk daftar di `CHECKLIST-UI-UX.md`.
