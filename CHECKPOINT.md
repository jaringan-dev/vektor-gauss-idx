# CHECKPOINT — catatan harian (terbaru di atas)

Dibaca PERTAMA kali setiap sesi baru dibuka. Tujuannya: kalau sesi tertutup / kehabisan limit, kita tahu persis harus mulai dari mana.
Clone yang hidup = **WSL `/home/vg/vektor-gauss`** (bukan clone Windows). Buka: `wsl -d Ubuntu-24.04` → `cd /home/vg/vektor-gauss` → `claude`.

---

## 2026-11-07 (Sabtu)

### ▶ Mulai dari sini
Badge (⚡ 📊 🏦), BPI 5 sesi, recorder iceberg-proxy, dan reminder Telegram SELESAI. Uji ke belakang ⚡+📊 SELESAI: tidak ada edge.
Audit `value` mentah SELESAI (catatan 🔀 crossing). Berikutnya (pilihan user): keputusan kuota arjum (≈ 6 Des) ·
`is_confirmed()` · Senin 9 Nov cek otomatis via Telegram.

### Langkah
- [x] 1. `src/vg/web/patterns.py`: `compression()` (price_bars) + `persistent_net_buyer()` (broker_flows), pure + `load()` read-only
- [x] 2. Badge di `/` (di bawah ticker, warna netral, tooltip berisi angka bukti) + tes — commit di bawah.
      Data 6 Nov: ⚡ 1 dari 169 saham; 🏦 37 dari 173 (setelah syarat net ≥ 10% nilai transaksi 5 sesi; tanpa itu 121 = noise).
- [ ] 3. Ambang (konstanta `COMP_*`, `NET_MIN_*` di `patterns.py`, sementara/PROVISIONAL) → **keputusan user**
- [x] 4. Recorder: tabel `trade_lots_daily` (fill ≥ 50 lot per harga/ukuran/sisi per hari), tabel SUDAH dibuat (`vg init-db` 7 Nov),
      mulai terisi Senin 9 Nov 08:55. Badge 🧊 BELUM dibuat — tunggu beberapa minggu data.
- [x] 5. Dashboard :8787 di-restart & dicek dengan data asli; ARCHITECTURE.md diperbarui.
- [ ] 6. **Senin 9 Nov setelah 16:05**: cek `trade_lots_daily` terisi + ukurannya (baris/hari) dan ringkasan recorder di `run_log`
      (`size-count rows ...`). Kalau terlalu besar → naikkan `LOT_MIN` di `src/vg/tradeflow.py`.

### Badge 📊 "volume ramai dari biasanya" (selesai)
- [x] 7. `volume_surge()` di `src/vg/web/patterns.py`: nilai pasar REGULER hari ini ÷ median 20 sesi sebelumnya, ≥ 2× dan ≥ Rp1 M.
      Pakai rupiah (bukan lembar → kebal split) dan buang pasar negosiasi (NATO 6 Nov: 1.060× karena 1 crossing → 6,9× reguler).
      Catatan: kolom `value` IDX kadang termasuk negosiasi, kadang tidak (BYAN 7 Okt) → `regular_value()` menangani keduanya.

### Uji ke belakang badge ⚡ compression + 📊 volume ramai (#1) — SELESAI
HASIL (2020-01-02..2026-10-23, return 5 hari v2, log `/home/vg/research-patterns-20261107.log`): **tidak ada yang punya edge setelah biaya.**
P-SURGE 122k event: raw −0,45% vs pasar, netral ukuran×likuiditas +0,08% [−0,01;+0,16], net biaya −1,89%. Naik/turun sama saja.
P-COMPRESSION 30k: netral −0,02%, net −5,41% (kebanyakan saham tidak likuid). P-COMP+SURGE 3,4k: netral +0,42% [+0,19;+0,67]
= nyata tapi kecil, net −0,94%, positif hanya di 14% fold setengah tahun → tidak konsisten. Badge tetap DESKRIPTIF.
- [x] 8. Dipakai ulang: `ret2_daily` (return v2 tiap saham-hari sejak 2020) + mesin `research.py` (bootstrap, q-value, netral
      ukuran×likuiditas, fold setengah tahun, regime, biaya per sinyal). `research.py` kini menerima `source`.
- [x] 9. `src/vg/pattern_research.py` + `vg research-patterns` + tabel `pattern_events` — commit `1779041`. Run 2 menit, exit 0.
- [x] 10. Dilaporkan ke user.
- [x] 11. Hasil riset ditulis di tooltip tiap badge (`TESTED` di `patterns.py`; perbarui kalau riset diulang). 🏦: "belum diuji".
- [ ] 12. Uji 🏦 net buy + BPI 5 sesi setelah riwayat broker K7 cukup (cek `vg broker-deep --plan`).

### Audit `price_bars.value` mentah (pasar negosiasi) — SELESAI
- [x] 13. Dampak ke ambang liquid Rp5 M: hanya 1,29% saham-hari liquid (3.477/269.678 sejak 2020) yang liquid karena crossing → DIBIARKAN
      (mengubahnya menyentuh eval/sinyal v1; manfaatnya kecil).
- [x] 14. BPI `day_value` HARUS tetap total: ladder broker TERMASUK negosiasi (TLKM 2 Okt: beli broker Rp314,7 M ≈ value Rp327,6 M).
      Masalah sebenarnya: di hari crossing, BPI & net broker didominasi 1 transaksi blok (0,9% saham-hari, sering di saham besar).
- [x] 15. Solusi = UNGKAPKAN, bukan buang: catatan 🔀 "Crossing X% of value" (≥ 50% nilai = negosiasi) di baris saham, dan
      "· N crossing day" di sel BPI 5 sesi. Data 6 Nov: NATO 99%, SMGR 88%, 4 saham lain punya 1 hari crossing di jendela.

### Hitungan kuota arjum (7 Nov) — bahan keputusan sebelum ≈ 6 Des (kuota 41k → 1k/hari)
EOD per hari sekarang ≈ **1.750 call**: broker summary 367 + tabel broker lengkap 367 + done-details ≈ 1.000 (5 saham watchlist,
cap 200 halaman/saham) + inventory/price-history/status ≈ 15. Universe per lookback: 1 → 164, 5 → 238, 10 → 295, 20 → 367.
K7 backlog 194.575 call @ 37,5k/40,5k per malam → selesai ≈ 12–13 Nov (aman sebelum 6 Des). Sisa kuota 7 Nov siang: 15.185/41.000.
Opsi ≤ 1k/hari: (E) buang done-details, ganti dengan data recorder WebSocket (gratis, sudah merekam HAKA/HAKI 810 saham) ≈ 750 ·
(D) lookback 5 + tanpa done-details ≈ 490 · (C) tanpa tabel lengkap & done-details ≈ 375 · atau perpanjang paket.
Syarat E: verifikasi dulu bahwa field `c` recorder = sisi agresor, dibandingkan done-details beberapa hari (selagi kuota masih besar).
- [x] 16. Alat pembanding SIAP: `vg compare-flow` (`src/vg/flow_check.py`), read-only. Hanya saham-hari LENGKAP yang dinilai
      (≥ 95% volume reguler DAN ≥ 95% jumlah transaksi; done-details difilter board RG — ia juga memuat NG). Verdict otomatis
      ikut di Telegram checkpoint setiap malam mulai 9 Nov. Data 6 Nov belum bisa dinilai (recorder baru jalan sore).
- [x] 17. Selama verifikasi, eod.sh: `collect-done-details --watchlist-only --max-pages 400` (default 200; BBCA/BBRI/TLKM 214–326
      halaman/hari). +~500 call/hari. Target 10 saham-hari lengkap ≈ 2 hari bursa (≈ 10 Nov).
- [ ] 18. Setelah verdict: kalau "recorder can replace done-details" → matikan done-details di eod.sh (EOD ≈ 750 call, muat 1k/hari),
      ganti sumber Aggressor Ratio ke recorder. Kalau tidak → pilih opsi D/C/perpanjang paket. Kembalikan `--max-pages` ke default.

### Sedang dikerjakan: dashboard "keputusan 3 detik" — versi JUJUR (rencana disetujui user 7 Nov)
Rencana lengkap: `~/.claude/plans/http-localhost-8787-bisakah-diperkaya-ta-bubbly-castle.md` (Windows: C:\Users\NUC 11ATKC4\.claude\plans\).
User memilih: label deskriptif (BUKAN "High Conviction/Avoid"), chart SVG server (BUKAN TradingView CDN).
- [x] 19. `src/vg/web/evidence.py` (5 tanda akumulasi + 3 peringatan + TESTED_NOTE) + tes
- [x] 20. `/`: kolom Evidence, klaster "Most agreement" & "Warnings", view=agree/warn, sort=evidence. Data 6 Nov: 33 agree, 38 warning.
- [x] 21. `/stock`: `charts.bpi_bars` sejajar candles (BPI 90 sesi dari broker_levels via `bpi_window.daily_bpi`) + tes
- [x] 22. `/stock`: Executive summary = `narrative.broker_summary` (+ verdict riset dari snapshot + win rate H+5 mentah), lolos FORBIDDEN.
      `stock.broker_reading` membangun jendela 5 sesi, net buyer, HAKA, divergensi, wash, crossing dari baris yang sudah dimuat.
- [x] 23a. pytest 1325 lulus, curl /stock/BTPS·NATO·ASII·ABDA(tanpa broker) & / & /?view=agree → 200, ARCHITECTURE.md.
- [x] 23b. Cek visual di Chrome (desktop + lebar HP): klaster, chip, chart BPI sejajar candles (ASII: BPI merah Agu–Sep, hijau sejak Okt).
      Ditambah: kalau > 2 pola, baris riset digabung jadi 1 kalimat (ASII 5 pola → 1 baris). Pushed 9ddbe48.
- [x] 24. Laporan user: "?view=agree tidak terbuka" = tabel memang terfilter, tapi bagian atas sama → "see all" kini lompat ke
      `#stocks` (+ scroll-margin), kotak aktif diberi garis + "clear". Klaster jadi 4 kotak di grid KPI (4 kolom, 2 di ≤760px):
      + 📊 Volume surge (35) & 🏦 Broker net buy (86), view=surge/net. Dicek di Chrome. Belum push.

### Sedang dikerjakan: panel "Broker map" di /stock (permintaan user 7 Nov, poin dari /btw)
- [x] 25. `src/vg/web/brokermap.py`: % F/R/L beli & jual, top 5 net buyer/seller (+avg), clean buyer/seller (sisi lawan ≤ 5%),
      tektokan (dua sisi dalam 20%), semua butuh ≥ 1% nilai. HANYA `broker_flows_full` (lengkap); tanpa itu panel bilang terus terang.
- [x] 26. Panel "Broker map" di /stock setelah Executive summary; dicek Chrome (ASII: foreign 89% beli, retail 30% jual, clean buyer KZ).
      Data 6 Nov: clean buyer di 134/164 saham, clean seller 133, tektokan 137 (BBCA HD 18,68 M beli = 18,68 M jual). Belum push.

### Reminder Telegram
`vg remind-checkpoint` jalan otomatis di akhir setiap EOD (sekali per sesi): cek otomatis + langkah `- [ ]` dan bagian
"Menunggu keputusan user" dari hari TERBARU di file ini. Jadi: jaga dua bagian itu tetap benar. Kirim ulang manual: `vg remind-checkpoint --force`.

### Sudah selesai hari ini (commit)
- `2eb817c` eod.sh exec bit permanen di git (record_trades.sh juga dipulihkan) — **pushed**
- `d5abaa1` universe broker "sticky": liquid di salah satu dari 20 sesi terakhir (`VG_BROKER_LIQUID_LOOKBACK`), 173 → ~367 saham/hari — **pushed**
- `89b94a6` kolom **BPI 5 sessions** di `/` (`src/vg/web/bpi_window.py`)
- `3213199` CHECKPOINT.md ini
- `0999a36` badge ⚡ compression + 🏦 persistent net buyer (`src/vg/web/patterns.py`)
- `b2ffac1` recorder → `trade_lots_daily` (proxy iceberg, forward-only)
- Semua di atas **sudah di-push** (`d5abaa1..fab254e`, 7 Nov). Push berikutnya tetap hanya kalau user bilang.

### Sedang berjalan / setengah jadi
- (tidak ada) — isi lubang data broker 2026-10-12..11-06 SELESAI: 20/20 hari ok, 0 error, ~370 saham/hari;
  cakupan 5/5 sesi: 333 saham (sebelumnya 104). Log: `/home/vg/holefill-20261107.log`. Sengaja TANPA `vg compute` (B13).

### Menunggu keputusan user
- `is_confirmed()` di `src/vg/web/bpi_window.py` — aturan "akumulasi terkonfirmasi" (TODO user).
- Kuota arjum turun ke 1k/hari ≈ 6 Des 2026: perpanjang paket / turunkan `VG_BROKER_LIQUID_LOOKBACK` / matikan `collect-broker-full`.
- Senin 9 Nov: EOD pertama dengan universe baru + FREEZE_SINCE + tag v2 → cek hasilnya.
- Kandidat audit: angka lain yang memakai `price_bars.value` MENTAH (ambang liquid Rp5 M universe broker, `LIQUID_VALUE_IDR`
  di engine/eval, BPI `day_value`) ikut kena crossing pasar negosiasi → pertimbangkan pakai `regular_value()` juga. Belum dikerjakan.
