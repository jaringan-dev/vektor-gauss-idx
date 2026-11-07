# CHECKPOINT — catatan harian (terbaru di atas)

Dibaca PERTAMA kali setiap sesi baru dibuka. Tujuannya: kalau sesi tertutup / kehabisan limit, kita tahu persis harus mulai dari mana.
Clone yang hidup = **WSL `/home/vg/vektor-gauss`** (bukan clone Windows). Buka: `wsl -d Ubuntu-24.04` → `cd /home/vg/vektor-gauss` → `claude`.

---

## 2026-11-07 (Sabtu)

### ▶ Mulai dari sini
Sedang mengerjakan **badge deskriptif di halaman utama**: ⚡ Vol/price compression (#1) dan 🏦 Persistent net buy (#2),
lalu menyiapkan rekaman histogram lot untuk 🧊 Iceberg proxy (#3, badge-nya menyusul setelah data terkumpul).
Lihat daftar langkah di bawah; langkah pertama yang belum dicentang = lanjutkan dari situ.

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

### Sudah selesai hari ini (commit)
- `2eb817c` eod.sh exec bit permanen di git (record_trades.sh juga dipulihkan) — **pushed**
- `d5abaa1` universe broker "sticky": liquid di salah satu dari 20 sesi terakhir (`VG_BROKER_LIQUID_LOOKBACK`), 173 → ~367 saham/hari — **pushed**
- `89b94a6` kolom **BPI 5 sessions** di `/` (`src/vg/web/bpi_window.py`)
- `3213199` CHECKPOINT.md ini
- `0999a36` badge ⚡ compression + 🏦 persistent net buyer (`src/vg/web/patterns.py`)
- `b2ffac1` recorder → `trade_lots_daily` (proxy iceberg, forward-only)
- ⚠ 4 commit + commit checkpoint ini **belum di-push** (push hanya kalau user bilang)

### Sedang berjalan / setengah jadi
- (tidak ada) — isi lubang data broker 2026-10-12..11-06 SELESAI: 20/20 hari ok, 0 error, ~370 saham/hari;
  cakupan 5/5 sesi: 333 saham (sebelumnya 104). Log: `/home/vg/holefill-20261107.log`. Sengaja TANPA `vg compute` (B13).

### Menunggu keputusan user
- `is_confirmed()` di `src/vg/web/bpi_window.py` — aturan "akumulasi terkonfirmasi" (TODO user).
- Kuota arjum turun ke 1k/hari ≈ 6 Des 2026: perpanjang paket / turunkan `VG_BROKER_LIQUID_LOOKBACK` / matikan `collect-broker-full`.
- Senin 9 Nov: EOD pertama dengan universe baru + FREEZE_SINCE + tag v2 → cek hasilnya.
