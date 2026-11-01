# Rancangan UI/UX v2 — Vektor Gauss

**Status:** rancangan (31 Okt 2026). Merevisi dan melengkapi `RENCANA-UI-UX.md` (5 Sep 2026), yang tetap berlaku untuk
prinsip dan batasan kepatuhan. Dokumen ini menambahkan: arsitektur navigasi, design system, inventaris layar
(sudah/belum), wireframe layar baru, dan urutan pengerjaan.

**Apa yang berubah sejak dokumen lama:**
- Dashboard superadmin sudah berjalan lokal (3 layar, FastAPI + Jinja2): `/` Forward-test, `/screener` Saham hari ini, `/ranking` Top/Ranking.
- Data broker kini **historis** (sampai 2020) dan **harian penuh** (~190 saham likuid/hari). Layar per-saham yang dulu mustahil sekarang mungkin.
- Hasil backtest: tidak ada tag broker yang jelas lolos biaya. UI harus membuat kesimpulan jujur ini terbaca, bukan tersembunyi.
- Celah data (3 hari harga bolong) baru ketahuan secara kebetulan. UI perlu **layar kesehatan data** agar celah terlihat sejak hari pertama.

---

## 1. Tujuan dan pengguna

| | **Operator (Anda)** — sekarang | **Pengguna publik** — nanti, gated |
|---|---|---|
| Pertanyaan utama | "Apakah data sehat? Pola mana yang terbukti? Saham apa yang kena pola hari ini?" | "Apakah rekam jejak jujur? Saham di watchlist saya kena apa?" |
| Perangkat | Desktop, layar lebar | HP dulu |
| Kepadatan | Tinggi, angka mentah boleh | Rendah, tenang |
| Status | Dibangun bertahap, tanpa gate | **Tunggu gate forward-test (~4 Nov)** |

Satu codebase, dua *shell* berbeda (tata letak berbeda, komponen sama). Jangan dicampur dalam satu admin panel.

## 2. Prinsip (tetap, ditambah tiga)

Tetap dari dokumen lama: rapor jujur, tanpa skor komposit, tanpa kata perintah beli/jual, probation tidak boleh tampak seperti sinyal terpercaya, makna tidak boleh hanya lewat warna, disclaimer di setiap layar.

Tambahan v2:
1. **Setiap angka membawa cakupannya.** Kolom "Top Bandar" harus selalu menampilkan "dari 190 saham", tidak pernah tampak se-pasar bila bukan. (Sudah diterapkan di `/ranking`; jadikan komponen baku.)
2. **Kesegaran data selalu terlihat.** Strip global di atas setiap layar: tanggal EOD, status harga, status broker, bulan KSEI. Jika ada yang basi atau bolong, strip berubah kuning/merah di semua layar.
3. **Kejujuran statistik dibuat visual.** Setiap tag menampilkan `n`, rentang waktu, dan edge setelah biaya berdampingan. Tag dengan n kecil tampil dengan label "n kecil" dan tidak boleh diurutkan di atas tag ber-n besar secara default.

## 3. Arsitektur informasi

```
Shell Operator
├── Beranda (Kesehatan)        BARU   ← pertanyaan: "ada yang perlu saya tangani?"
├── Pola (Tag)
│   ├── Forward-test           ADA (/)
│   └── Detail tag             BARU   ← tren edge dari waktu ke waktu + daftar sinyal
├── Saham
│   ├── Saham hari ini         ADA (/screener)
│   ├── Top / Ranking          ADA (/ranking)
│   └── Detail saham           BARU   ← harga, arus asing, inventory broker, riwayat tag, KSEI
├── Data
│   ├── Cakupan & celah        BARU   ← heatmap hari × sumber, mendeteksi bolong seperti 14/15/25 Sep
│   └── Riwayat koleksi        BARU   ← run terakhir tiap collector, error, durasi
└── Log mentah                 BARU   ← <pre> bergulir, tanpa dekorasi
```

Navigasi: sidebar kiri (desktop) dengan 4 grup di atas, menggantikan satu baris tab saat ini yang tidak muat jika layar bertambah. Strip kesegaran data menempel di atas konten.

## 4. Design system

### 4.1 Token warna (terang / gelap, semantik, bukan dekoratif)

| Token | Terang | Gelap | Dipakai untuk |
|---|---|---|---|
| `--surface` / `--panel` | #f7f7f8 / #fff | #0e0f12 / #17181c | latar halaman / kartu |
| `--text` / `--muted` | #1a1a1a / #6b7280 | #e5e7eb / #9ca3af | teks |
| `--pos` / `--neg` | #15803d / #b91c1c | #4ade80 / #f87171 | angka naik/turun (selalu dengan tanda +/−) |
| `--ready` | hijau | hijau | **hanya** tag LULUS (belum ada satu pun hari ini) |
| `--fail` | merah | merah | tag GAGAL (ditampilkan sama menonjol) |
| `--prob` | abu netral | abu netral | PROBATION, sengaja tidak berwarna |
| `--warn` | ambar | ambar | data basi / cakupan parsial |
| `--info` | biru | biru | catatan cakupan, tautan |

Aturan: hijau tebal dan ikon centang dicadangkan untuk `READY`. Probation tidak boleh memakai hijau.

### 4.2 Tipografi dan angka
- Font sistem (tanpa unduhan eksternal). Angka memakai `font-variant-numeric: tabular-nums`.
- Format Indonesia seperti di digest: titik ribuan, koma desimal, `+12,3B` / `+45Jt`. Fungsi `_idr`, `_pct`, `_price` yang sudah ada dipakai ulang.
- Skala: 12 (catatan) · 14 (isi) · 16 (judul kartu) · 20 (judul halaman). Padat, karena ini alat kerja.

### 4.3 Komponen baku

| Komponen | Isi | Catatan |
|---|---|---|
| **StatusBadge** | LULUS / GAGAL / PROBATION + teks | selalu teks, bukan warna saja |
| **CoverageNote** | "dari 190 saham · universe broker" | wajib di tiap tabel peringkat |
| **FreshnessStrip** | EOD · harga · broker · KSEI | global, berubah warna jika bermasalah |
| **EdgeCell** | angka bertanda, kelas pos/neg, `n` di tooltip | untuk semua metrik edge |
| **TagChip** | nama tag, label probation/weighted | klik → detail tag |
| **TickerLink** | kode saham tebal, ★ watchlist, 📌 dipegang | klik → detail saham |
| **Sparkline** | garis kecil tren edge | tanpa sumbu, nilai di tooltip |
| **DataTable** | header lengket, sortir, filter ketik | pola dari `/screener` |
| **Disclaimer** | footer wajib | tidak boleh hilang saat redesign |

### 4.4 Pola interaksi
- Filter ketik langsung (sudah ada di `/screener`), diperluas ke tag dan sektor.
- Toggle horizon 1D / 5D tersimpan di URL (`?h=5d`), supaya bisa dibagikan/disimpan.
- Klik tag atau saham di mana pun menuju halaman detailnya (drill-down konsisten).
- Tidak ada modal untuk data; semuanya halaman biasa (mudah di-refresh, mudah di-bookmark).

## 5. Wireframe layar baru

### 5.1 Beranda — Kesehatan
Pertanyaan: *"Ada yang perlu saya tangani hari ini?"*

```
[Strip: EOD 30 Okt ✅ · harga 962 ✅ · broker 196 ✅ · KSEI Sep ✅]

PERLU PERHATIAN (kosong = "Semua sehat")
 ⚠ Cakupan broker 16-30 Okt turun ke ~60/hari (cap radar)         [lihat Data]
 ⚠ 3 hari harga bolong terdeteksi (14, 15, 25 Sep)                  [lihat Data]

KARTU STATUS (merah/kuning/hijau, urut prioritas)
 [EOD hari ini] [Cakupan broker] [KSEI bulanan] [Disclosure] [Flag integritas] [Telegram]

RINGKASAN POLA (3 teratas)        TAG BARU HARI INI
 FLOAT-ASING-PADAT  n=34  PROBATION  ...   BANDAR-CANDIDATE  3 saham
```
Sumber: logika `digest.py` (ok_price/ok_index), `broker_flows`, `trading_calendar`. Kartu cron dan Telegram butuh tabel `collector_runs` / `notification_log` (lihat §7).

### 5.2 Detail saham — layar baru terpenting
Sekarang mungkin karena broker historis sudah ada.

```
BBCA  ★📌    6.075  +1,2%    🟢 AKUMULASI
[Harga 3 bulan, candle] ────────────────────────────
[Inventory broker kumulatif: 5 broker net buy teratas vs 5 net sell, di atas harga]
   filter: 2W 1M 3M 6M | Nilai/Lot | Semua/Asing/Domestik

ARUS ASING           BROKER (hari ini)        KSEI (bulanan)
 hari ini  +286B      net asing  +275B          %asing 3 bln ↑
 1 minggu  +186B      CR3 92%   top: YU         %ritel   ↓
 konsistensi 7/10

TAG HARI INI: BROKER-ASING-AKUM · BROKER-DOMISILI-KONFIRM · TIKET-BESAR-AKUM
RIWAYAT TAG (garis waktu 90 hari)   ▮▮ ▮ ▮▮▮ ...
```
Cakupan: inventory per broker hanya untuk saham likuid yang sudah di-backfill; saham lain menampilkan "data broker belum tersedia", bukan grafik kosong.

### 5.3 Detail tag
Menjawab *"apakah pola ini benar-benar bekerja?"*

```
BROKER-DOMISILI-KONFIRM            [PROBATION]
n 5.878 · 3 Jun → 30 Okt · edge H5 likuid +0,92% · biaya ~170bp → belum melewati

[Garis edge kumulatif dari waktu ke waktu + pita kepercayaan]   ← sparkline versi besar
[Tabel per bulan: n, edge H5, hit vs pasar]
[Sinyal terbaru: saham, tanggal, hasil 5 hari]
Catatan metodologi: apa yang dihitung, kapan tag ini terpicu.
```
Inilah komponen yang kelak menjadi **Rapor Publik** (versi tanpa panel operator).

### 5.4 Cakupan & celah
Heatmap: baris = hari bursa, kolom = sumber (harga, indeks, broker, sinyal). Sel dikodekan **jumlah baris relatif terhadap normal** (bukan sekadar ada/tidak). Hari 14/15/25 Sep akan langsung terlihat merah. Klik sel → daftar saham yang hilang + tombol salin perintah perbaikan (`vg collect-price-history …`).

### 5.5 Riwayat koleksi
Tabel run terakhir per collector (sumber, status, durasi, baris, pesan error). Murni persist hasil `CollectOutcome`.

## 6. Layar yang sudah ada — perbaikan

| Layar | Perbaikan |
|---|---|
| `/` Forward-test | Tambah sparkline tren edge per tag; kolom "span" jadi bar; tag n<50 diberi label "n kecil" |
| `/screener` | Tambah filter per tag dan sektor; ticker bisa diklik; kolom bisa disortir |
| `/ranking` | Panel dalam grid 2 kolom di desktop; tampilkan CoverageNote sebagai komponen baku |
| Semua | Sidebar + FreshnessStrip; tombol tema terang/gelap manual (saat ini hanya mengikuti OS) |

## 7. Kebutuhan backend

Sebagian besar layar hanya membaca tabel yang sudah ada. Yang perlu ditambah:
- `collector_runs` dan `notification_log` (sudah dirancang di dokumen lama §5) untuk Beranda dan Riwayat koleksi.
- Query agregasi cakupan per hari × sumber untuk heatmap (dari `price_bars`, `broker_flows`, `signal_components`).
- Query riwayat edge per bulan per tag untuk sparkline dan Detail tag (dari `signal_log` + outcome).
- Rekomendasi terpisah: jadikan koleksi broker harian **penuh** (bukan cap radar 60) supaya cakupan tidak turun lagi diam-diam.

## 8. Urutan pengerjaan (usulan)

| Prioritas | Pekerjaan | Alasan |
|---|---|---|
| P0 | Shell baru: sidebar + FreshnessStrip + token warna + komponen CoverageNote/StatusBadge | Dasar semua layar berikutnya; murah |
| P1 | **Cakupan & celah** (heatmap) | Mencegah celah seperti 14/15/25 Sep terulang tanpa terdeteksi |
| P2 | **Detail saham** | Nilai terbesar bagi pemakaian harian; datanya sudah ada |
| P3 | **Detail tag** + sparkline | Menjawab pertanyaan inti produk; dasar Rapor Publik |
| P4 | Beranda Kesehatan | Perlu tabel §7 dulu |
| P5 | Polesan: tema manual, mobile, perbaikan tabel | Setelah isi lengkap |
| Gated | Website komersial (watchlist pengguna, kalkulator, RADAR berbayar) | Menunggu hasil forward-test ~4 Nov |

## 9. Yang sengaja tidak dilakukan
- Tidak meniru Inventory Chart se-pasar milik NeoBDM: broker VG hanya untuk saham likuid. Detail saham menampilkan apa yang ada dan jujur soal yang tidak ada.
- Tidak ada skor 0–100, tidak ada narasi AI di atas angka (lihat memo no-AI-model).
- Tidak ada kata "beli/jual" di UI.
- Belum ada autentikasi: dashboard hanya `127.0.0.1`. Sebelum dibuka ke jaringan, pasang Cloudflare Access atau basic-auth.

## 10. Pertanyaan terbuka untuk Anda
1. **Bahasa antarmuka operator:** tetap Indonesia penuh (saat ini campur: "span", "edge", "hit")? Usul: istilah teknis tetap Inggris, kalimat Indonesia.
2. **Mulai dari mana:** P0 + P1 (shell + heatmap celah) lebih dulu, atau langsung Detail saham?
3. **Peta navigasi:** setuju sidebar kiri 4 grup, atau tetap tab atas?
4. **Hosting:** tetap lokal dulu, atau persiapkan deploy privat (Cloudflare Access) agar bisa dibuka dari HP?

---
*Rancangan ini turunan dari prinsip `RENCANA-FASE-2.md` §1.2 dan memperbarui `RENCANA-UI-UX.md`. Mockup visual: `mockup-ui-v2.html`.*
