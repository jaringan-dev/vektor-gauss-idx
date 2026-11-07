# CHECKPOINT — catatan harian (terbaru di atas)

Dibaca PERTAMA kali setiap sesi baru dibuka. Tujuannya: kalau sesi tertutup / kehabisan limit, kita tahu persis harus mulai dari mana.
Clone yang hidup = **WSL `/home/vg/vektor-gauss`** (bukan clone Windows). Buka: `wsl -d Ubuntu-24.04` → `cd /home/vg/vektor-gauss` → `claude`.

---

## 2026-11-07 (Sabtu)

### ▶ Mulai dari sini
Badge (⚡ 📊 🏦), BPI 5 sesi, recorder iceberg-proxy, dan reminder Telegram SELESAI. Uji ke belakang ⚡+📊 SELESAI: tidak ada edge.
Berikutnya (pilihan user): audit `value` mentah · keputusan kuota arjum (≈ 6 Des) ·
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
