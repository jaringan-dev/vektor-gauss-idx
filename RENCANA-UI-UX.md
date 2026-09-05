# Rencana Pra-Produksi — UI/UX Dashboard Superadmin & Website Komersial

**Status: dokumen perencanaan, GATED.** Sama seperti seluruh `RENCANA-FASE-2.md`, tidak ada
yang di bangun dari sini sampai gate forward-test lolos (lihat `HANDOFF.md` § "Status at a
glance" — target baca ulang **~2026-11-04**). Dokumen ini menangkap keputusan desain
*sebelum* ada kode, supaya saat gate lolos tidak mulai dari nol.

Turunan dari permintaan 2026-09-05: dashboard superadmin untuk memantau infra/data/website,
plus website komersial untuk pengguna lain. Dibaca bersama `RENCANA-FASE-2.md` §1
(konsep produk) dan §6 (skema database) — dokumen ini **tidak mengulang** keputusan
arsitektur/skema di sana, fokus ke lapisan UI/UX & informasi yang ditampilkan.

---

## 1. Dua audiens, dua produk, jangan dicampur

| | **Dashboard Superadmin** | **Website Komersial** |
|---|---|---|
| Untuk siapa | Operator (Anda) saja | Pengguna berbayar/gratis (Fase 2) |
| Tujuan | Pastikan mesin & data sehat sebelum ada orang lain bergantung padanya | Baca rapor tag, pantau watchlist, hitung posisi |
| Akses | Privat, tidak publik, tidak untuk didemokan | Publik (landing + rapor) → auth (dashboard) |
| Kapan mulai berguna | **Sebelum** ada pengguna lain (prasyarat, bukan fitur pengguna) | Setelah gate lolos + ada niat komersial serius |
| Nada visual | Teknis, padat informasi, boleh "berisik" (angka mentah, log) | Tenang, rapi, jujur — selaras janji produk (§1.1 RENCANA-FASE-2) |

Kesalahan yang harus dihindari: jangan bangun satu "admin panel" generik yang mencoba
melayani keduanya. Superadmin butuh *kecepatan mendeteksi masalah*; pengguna butuh
*kepercayaan pada rapor*. Prioritas dan kepadatan informasi keduanya bertolak belakang.

---

## 2. Prinsip desain (turunan dari filosofi produk)

Semua turunan langsung dari `RENCANA-FASE-2.md` §1.2 — UI hanyalah permukaan dari
prinsip yang sudah ditetapkan di metodologi, bukan keputusan baru:

1. **Rapor jujur di atas segalanya (§1.2.B, §1.2.E).** Tidak ada skor komposit 0–100 di
   layar manapun. Tiap tag tampil dengan angka edge forward-nya, termasuk yang gagal.
   Kegagalan sebuah tag ditampilkan **sama menonjolnya** dengan keberhasilan — ini
   pembeda dari NeoBDM/IDXAlpha, jangan dilunturkan demi "UI yang enak dilihat."
2. **Tidak ada verb aksi langsung (§1.2.F, kepatuhan OJK).** UI tidak pernah menulis
   "BELI"/"JUAL" sebagai perintah. Sudah diselesaikan di layer teks (`digest.py`
   `_action()` → WATCH/BUY/HOLD sebagai label netral + disclaimer); pola yang sama
   berlaku di web — tombol aksi (kalau ada) berbunyi "Lihat kalkulator", bukan "Beli
   sekarang".
3. **Probation vs weighted harus visually berbeda, bukan sekadar label.** Tag bobot-0
   (probation) tidak pernah punya elemen UI yang menyerupai "sinyal terpercaya" (badge
   hijau tebal, ikon centang) — reservasi elemen visual "terpercaya" itu murni untuk
   tag yang sudah promosi ke `weighted` (belum ada satupun hari ini).
4. **Mobile-first untuk pengguna, desktop-first untuk superadmin.** Basis pengguna
   Indonesia baca notifikasi di HP (persis kenapa Telegram jadi kanal utama); tapi
   superadmin memantau log & tabel — itu lebih nyaman di layar lebar. Jangan paksa satu
   layout responsif untuk keduanya menyerap kompleksitas yang sama.
5. **Konsisten dengan bahasa visual digest Telegram** (lihat `digest.py`, redesign
   2026-09-05): emoji status per-quadrant (📈🟢🔻📉▪️), `✅`/`⚠️` untuk anotasi
   KSEI-KONFIRM vs KSEI-DIVERGEN, format `_idr()`/`_pct()`/`_price()` Indonesia (titik
   ribuan, koma desimal). Pengguna yang sama melihat Telegram *dan* web — jangan
   pecahkan bahasa visualnya.

---

## 3. Dashboard Superadmin — Informasi Arsitektur

### 3.1 Tujuan konkret
Jawab satu pertanyaan tiap kali dibuka: **"apakah ada sesuatu yang butuh saya tangani
hari ini?"** — bukan laporan estetik, tapi radar masalah. Prioritas: staleness data >
kegagalan cron > kesehatan disclosure/KSEI > uptime web > kuota vendor (Fase 1.5+).

### 3.2 Layar 1 — Ringkasan (landing superadmin)
Kartu status per komponen, merah/kuning/hijau, urut prioritas:

| Kartu | Merah kalau… | Sumber data |
|---|---|---|
| **EOD hari ini** | `price_bars`/`index_bars` max(session_date) ≠ hari bursa terakhir | sama seperti cek `ok_price`/`ok_index` di `digest.py:170` — reuse logic yang sudah ada |
| **Cron/job run** | run terakhir gagal / tidak jalan (Task Scheduler atau GitHub Actions) | log job (belum ada tabel — lihat §5 "yang perlu dibangun") |
| **KSEI bulanan** | `ownership_monthly` lebih dari ~40 hari dari akhir bulan berjalan | `max(month_end)` |
| **Disclosure collector** | 0 filing tertangkap dalam N hari bursa terakhir (indikasi collector diam-diam mati, bukan memang sepi) | `disclosure_filings` |
| **Data-integrity flags** | lonjakan `RECON_FAIL` / `CROSSING-TINGGI` / `KSEI-DIVERGEN` di atas baseline | `signal_components.data_flags`, `signal_log` |
| **Telegram delivery** | `send()` gagal (lihat `telegram.py`) — exception terakhir | log kirim (belum ada tabel) |
| **Web uptime** (kalau website Fase 2 sudah ada) | health-check endpoint gagal | uptime monitor eksternal (mis. UptimeRobot/Cloudflare) |
| **Kuota vendor data** (Fase 1.5+) | request/bulan mendekati limit tier | tergantung vendor terpilih |

### 3.3 Layar 2 — Detail per-collector
Klik kartu → tabel run history: tanggal, sumber (`idx_stock_summary`/`idx_index_summary`/
`ksei_ownership`/`idx_disclosure`), status (`ok`/`blocked`/`error`), durasi, jumlah baris,
pesan error mentah. Ini murni membaca ulang pola yang sudah ada di `sources/base.py`
(`FetchStatus`, `FetchResult`) — **tidak perlu skema baru untuk collector Tier-0**, cukup
persist hasil `fetch()` yang selama ini cuma di-log ke stdout/`eod.log`.

### 3.4 Layar 3 — Forward-test / gate tracker
Bacaan langsung dari `vg eval --since`, disajikan sebagai tabel + sparkline tren edge per
tag dari waktu ke waktu (bukan snapshot statis) — supaya terlihat kapan tepatnya sebuah
tag "lolos" ambang n≥50/2 bulan/edge-setelah-biaya (§1.2.A `RENCANA-FASE-2.md`), tanpa
harus baca ulang log Telegram mingguan secara manual. Ini **satu-satunya** bagian dashboard
superadmin yang boleh dilihat pengguna lain kelak (jadi "Rapor Publik", §4.2) — desain
komponennya sekali, pakai dua kali (internal dulu, public-facing setelah gate).

### 3.5 Layar 4 — Log & alert mentah
Tail log terakhir (`eod.log`, error Telegram, exception Python) tanpa styling — ini bukan
tempat untuk desain, cukup `<pre>` scrollable. Prioritaskan *kecepatan cek*, bukan
keindahan.

### 3.6 Akses & keamanan
Single-operator untuk saat ini — tidak butuh sistem role/permission dulu. **Tapi**:
jangan taruh di endpoint publik tanpa auth sama sekali, walau cuma satu orang yang tahu
URL-nya (data infra bisa bocorkan pola operasi ke kompetitor). Minimal: Cloudflare Access
(gratis untuk 1 user) di depan endpoint, atau basic-auth kalau masih di rumah sendiri.

---

## 4. Website Komersial — Informasi Arsitektur (Fase 2, gated)

Ini memperluas §1.3–1.4 `RENCANA-FASE-2.md` (alur pengguna) ke level layar/komponen.

### 4.1 Peta layar
```
Landing (publik)
 └─ Rapor Publik (publik) ──────────────┐
 └─ Daftar/Masuk (email+password, Turnstile)
      └─ Dashboard (auth)
           ├─ Watchlist (harga, NFF, konsistensi asing, quadrant, tag, ⚠)
           │    └─ Detail Ticker
           │         ├─ Rapor tag (edge historis tag ini utk ticker ini)
           │         └─ Kalkulator posisi (entry/SL → lot & R:R, PRINSIP-DATA §F)
           ├─ RADAR (tier pro saja — kandidat di luar watchlist)
           ├─ Alert rules (tier pro saja — hanya utk tag `weighted`, §1.2.A)
           └─ Pengaturan akun / billing
```

### 4.2 Rapor Publik — layar paling penting secara strategis
Ini **pembeda produk** (§1.1, §1.4 RENCANA-FASE-2: "lihat rekam jejak sebelum bayar") —
harus publik, tanpa login, dan jadi halaman pertama yang dibagikan orang. Isi: tabel tag ×
{n, edgeH1/H5/H10, hit-rate vs universe, status probation/weighted}, dipisah in-sample vs
forward (persis format `_eval_table()` di `digest.py`), plus catatan tanggal mulai forward
per tag. **Desain ulang minimal dari yang sudah ada** — komponen ini secara harfiah sudah
punya versi teks (`build_weekly_eval()`), tinggal di-web-kan.

### 4.3 Watchlist (layar utama pengguna login)
Kartu per ticker, format visual **identik** dengan blok watchlist digest Telegram
(§2 prinsip #5) — emoji quadrant, harga+% bold, baris flow "Asing bersih — hari ini X ·
1 minggu terakhir Y", anotasi ✅/⚠️. Pengguna yang sudah biasa baca Telegram tidak perlu
belajar bahasa visual baru di web.

### 4.4 Kalkulator Posisi
Form: entry (atau ambil dari `close` hari ini), SL manual atau turunan ATR (radio
toggle), target R. Output: lot, risk Rp & % akun, R:R — langsung panggil `calc.plan()`
yang sudah ada, tidak ada logika baru. **Tidak pernah** menyimpan/menyarankan angka akun
pengguna tanpa mereka masukkan sendiri (privasi finansial).

### 4.5 Non-functional untuk website komersial
- **Aksesibilitas dasar**: kontras warna cukup untuk quadrant emoji (jangan cuma andalkan
  warna untuk makna — sertakan label teks selalu, seperti sudah dilakukan di digest).
  Tema terang & gelap.
- **Bahasa**: Indonesia sebagai default (basis pengguna), Inggris opsional kalau target
  bergeser ke API B2B (§1.5).
- **Kepatuhan OJK**: setiap layar yang menampilkan `aksi`/kalkulator wajib bawa disclaimer
  yang sama seperti footer digest ("bukan nasihat investasi... mekanis, bukan prediksi").
  Tidak boleh hilang saat redesign visual.

---

## 5. Yang perlu dibangun di backend supaya dashboard ini mungkin

Dashboard superadmin (§3) butuh sedikit persistensi baru yang **belum ada**, tapi kecil
dan tidak menyentuh metodologi sinyal:

- Tabel `collector_runs` (atau serupa): satu baris per eksekusi `fetch()`/`collect_*`,
  {source, session_date, status, duration_ms, rows, error_message, ts}. Sumbernya cuma
  bungkus tipis di atas `FetchResult`/`CollectOutcome` yang sudah dikembalikan tiap
  fungsi collect — bukan instrumentasi baru, cuma persist yang sudah dihitung.
- Tabel `notification_log` kecil untuk histori kirim Telegram (sukses/gagal + alasan) —
  bungkus tipis di atas `TelegramSender.send()`.
- Tidak perlu tabel baru untuk data-integrity flags (`data_flags` sudah ada di
  `signal_components`) atau forward-test tracker (`signal_log` + `vg eval` sudah cukup).

Ini kerja **kecil dan bisa dimulai kapan saja tanpa menunggu gate**, karena murni
observability infra — tidak menambah sinyal baru, tidak menyentuh keputusan trading, dan
berguna baik dashboard-nya jadi dibangun atau tidak (mempermudah debug EOD harian
sekarang juga, lewat query SQL manual sebelum ada UI-nya).

---

## 6. Pertanyaan terbuka (perlu keputusan Anda, bukan asumsi saya)

1. **Hosting dashboard superadmin**: Cloudflare Pages/Workers (selaras §3.3 RENCANA-FASE-2
   "GitHub + Cloudflare, ~$0") atau cukup halaman lokal yang dibuka manual di PC/WSL
   (tidak perlu online 24/7 kalau cuma Anda yang pakai)?
2. **Prioritas rasa** dashboard superadmin: mulai dari Layar 1 (ringkasan) dulu, atau
   langsung Layar 3 (forward-test tracker) karena itu yang paling sering Anda cek manual
   sekarang tiap minggu?
3. **Kapan boleh mulai** bagian §5 (tabel `collector_runs`/`notification_log`)? Ini tidak
   gated secara metodologi, tapi tetap kerja tambahan di luar radar item Fase-1-kritis
   (parse disclosure PDF, tracker paper-trading) yang sudah disepakati lebih prioritas.
4. Website komersial (§4) eksplisit **menunggu gate** — tidak perlu keputusan sekarang,
   dicatat di sini supaya tidak hilang.

---

*Ditulis 2026-09-05, sesi setelah redesign digest Telegram + riset harga IDX Data
Service. Baca bersama `RENCANA-FASE-2.md` (konsep produk & skema DB) dan `HANDOFF.md`
(status gate).*
