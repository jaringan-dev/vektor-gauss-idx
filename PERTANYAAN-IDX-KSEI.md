# Draft Pertanyaan untuk IDX & KSEI + Temuan Sumber Data NeoBDM

Pendamping [INVENTARIS-SUMBER-DATA.md](INVENTARIS-SUMBER-DATA.md). Tujuan: dapat jawaban faktual sebelum keputusan arsitektur (Gate 1). **Tidak ada kode.**

---

## BAGIAN 1 — Pertanyaan untuk IDX (IDX Data Services)

**Kirim ke:** IDX Data Services — cek alamat resmi di `idx.co.id/en/products/idx-data-services/` bagian *Contact Us* (umumnya `layanandata@idx.co.id`). Atau langsung ke redistributor lokal: **PT RTI Infokom, PT IQ Plus Prima, PT IDX Solusi Teknologi Informasi, Perum LKBN Antara/IMQ**.

**Konteks singkat yang perlu disampaikan:** "Kami membangun platform analitik kuantitatif pasar saham Indonesia (screener + notifikasi). Kami butuh data EOD, bukan real-time. Kami akan menampilkan *metrik turunan* (rasio, skor, agregasi) ke pengguna berbayar, bukan mendistribusikan ulang data mentah."

### Pertanyaan

**Update RESOLVED 2026-09-05:** poin 1, 3 (sebagian), 6, dan 9 sudah terjawab lewat katalog harga resmi publik `idx.co.id/media/2qalvu4z/20240923_idx-data-catalogue-pricelist-2024.pdf` (49 halaman, tanpa login) — detail lengkap di `INVENTARIS-SUMBER-DATA.md` §6bis. Ringkasan tiap poin ditambahkan inline di bawah; yang masih **belum terjawab** ditandai tetap terbuka.

1. ~~**Broker summary per emiten (EOD).**~~ **RESOLVED:** tidak ada produk resmi bernama itu. `IDX Equity EoD Recapitulation` cuma agregat Domestik/Asing (bukan per broker). Yang ada: `IDX Equity & Derivative Log Data` (historis, field `Brokers Code` per order + `Buyer Code`/`Seller Code` per trade, Rp3jt/bln, Direct) atau `IDX Equity Real Time Order` (live, sama field, Rp19,8–22jt/bln) — keduanya harus diagregasi sendiri jadi "broker summary". Masih terbuka: sample data riil belum diminta ke IDX.

2. **Stock summary EOD.** Apakah tersedia produk resmi berisi OHLCV per emiten per hari + **nilai (Rupiah) foreign buy/sell** (bukan hanya volume lembar) + pemisahan papan reguler vs negosiasi? Field list + sample. *(Katalog resmi menunjukkan `IDX Equity EoD Professional`/`Basic` punya OHLCV lengkap tapi field foreign buy/sell dalam Rupiah tidak eksplisit tercantum — masih perlu tanya field list persis.)*

3. **Data historis.** ~~Untuk kedua produk di atas: berapa tahun ke belakang...~~ **SEBAGIAN RESOLVED:** `IDX Equity & Derivative Log Data` dijual per-bulan-berjalan ("1 Bulan/1 Month", Rp3jt), bukan backfill sekaligus — tidak jelas apakah bisa beli banyak bulan historis sekaligus atau cuma mulai dari tanggal subscribe. **Masih terbuka:** konfirmasi ke IDX apakah backfill jauh ke belakang tersedia dan berapa biayanya.

4. **Skema lisensi.** Kami ingin: (a) menyimpan & mengolah data ini di server kami, dan (b) menampilkan **metrik turunan** (mis. rasio konsentrasi broker, skor akumulasi, sparkline) ke pengguna **berbayar** via web dan bot Telegram — **tanpa** menampilkan tabel broker summary mentah. Lisensi apa yang dibutuhkan: **Display** atau **Non-Display** (kategori yang mana)? Apakah kami perlu berstatus **Redistributor**? *(Katalog resmi mengonfirmasi ada "IDX Non-Display Usage Kategori 2: investment analysis/portfolio valuation/risk management/quantitative analysis", Rp5,5jt/bln Direct — kemungkinan cocok, tapi kombinasi persis untuk "tampilkan turunan ke user berbayar" tidak eksplisit di katalog. Masih terbuka.)*

5. **Menampilkan teaser gratis.** Apakah boleh menampilkan sebagian metrik turunan ke pengunjung non-pelanggan (mis. "3 saham akumulasi tertinggi hari ini") sebagai promosi, atau semua turunan wajib di balik paywall?

6. ~~**Biaya.**~~ **RESOLVED:** tabel harga resmi lengkap (Direct & Indirect, Group/Non-Group, lokal/asing) ada di `INVENTARIS-SUMBER-DATA.md` §6bis. Termurah relevan: Log Data Rp3jt/bln, EoD Professional Rp3jt/bln (Direct) / Rp2,7jt (Indirect), Data Reference Rp10jt/bln (Direct) / Rp9jt (Indirect). Uang jaminan 300% biaya bulanan berlaku semua produk; free trial 1 bulan.

7. **Jalur akses termudah untuk perusahaan kecil.** Direct via NSP (leased line) vs via redistributor — mana yang Anda sarankan, dan siapa PIC/kontak redistributor lokal yang relevan. *(Katalog resmi cuma cantumkan Log Data sebagai Direct-only — **masih terbuka**: tanyakan apakah tersedia Indirect lewat RTI/IQ Plus/Antara-IMQ/IDX-STI, karena Direct butuh leased line NSP terpisah yang biayanya tak tercantum.)*

8. **Kewajiban pelaporan.** Bentuk & frekuensi laporan pemakaian data (*reporting*) yang wajib kami kirim ke IDX sebagai licensee.

9. ~~**ITCH / order-level.** ... (Kami menduga kode broker tetap dianonimkan intraday sejak 2021 — mohon konfirmasi.)~~ **RESOLVED — dugaan itu SALAH.** Katalog resmi eksplisit mencantumkan `Brokers Code` sebagai field di `IDX Equity Real Time Order`, `IDX Equity & Derivative Log Data`, dan semua varian `IDX ITCH` (Total View/Basic/Last Sale). Kode broker **tidak dianonimkan** di produk berlisensi — anonimisasi cuma berlaku di feed publik gratis. Ini berarti analisa "ukuran order"/Sultan-Zombi **secara data mungkin dilakukan**, asal beli salah satu produk ini.

---

## BAGIAN 2 — Pertanyaan untuk KSEI

**Kirim ke:** KSEI — `helpdesk@ksei.co.id` / cek halaman *Hubungi Kami* di `web.ksei.co.id`. Untuk ORCHiD/riset: cari PIC "Penelitian Online / ORCHiD".

**Konteks singkat:** "Kami membangun platform analitik pasar saham. Kami sudah menggunakan file publik *Komposisi Kepemilikan Efek (Lokal-Asing)* bulanan. Kami ingin tahu apakah ada data kepemilikan yang lebih sering/granular, dan syarat penggunaannya."

### Pertanyaan

1. **Frekuensi.** Konfirmasi: file *Komposisi Kepemilikan Efek (Lokal-Asing)* yang diunduh publik hanya **snapshot akhir bulan**, atau ada versi lebih sering (dwi-mingguan / mingguan / harian)?

2. **Data lebih granular (berbayar/riset).** Apakah KSEI menyediakan — untuk keperluan komersial atau riset berbayar — salah satu dari:
   - kepemilikan per emiten dengan **frekuensi harian atau mingguan**;
   - data **mutasi / perpindahan kepemilikan (settlement T+2)** per kategori investor per emiten;
   - *Scripless Balance Position* harian per emiten.
   Jika ada: nama produk, syarat, format, dan biaya.

3. **Spesifikasi kolom.** Apakah ada dokumen resmi yang mendefinisikan setiap kolom pada file *Komposisi Kepemilikan* (kategori investor lokal & asing: IS/CP/IB/ID/MF/SC/FD/PF/OT, dll.), termasuk apakah angka = jumlah lembar atau nilai.

4. **Arsip historis.** File kepemilikan bulanan tersedia sampai tahun berapa ke belakang, dan bagaimana cara memperoleh arsip lama (situs hanya menampilkan ~2023–2026)?

5. **ORCHiD / Penelitian Online.** Data granular apa yang bisa diakses lewat kanal ini? Siapa yang berhak — akademisi saja, atau juga entitas komersial? Proses pengajuan, jangka waktu, dan biaya.

6. **Format terstruktur.** Apakah publikasi *Statistik KSEI* bulanan (kini PDF) tersedia dalam bentuk Excel/CSV/API?

7. **Ketentuan penggunaan.** Bolehkah kami mengolah data kepemilikan publik KSEI menjadi turunan (grafik tren, % asing, perubahan bulanan) lalu menampilkannya di platform **berbayar**? Ada kewajiban atribusi atau lisensi?

8. **Corporate action.** File *List of CA Activities* (stock split, reverse split, dividen saham, HMETD, merger) — apakah ini acuan resmi yang sah untuk penyesuaian data harga/jumlah saham historis? Ada format API/terstruktur atau hanya XLS per tahun?

---

## BAGIAN 3 — Temuan: dari mana NeoBDM mengambil data?

**Metode:** (a) publik — robots.txt, ToS, Privasi, Kontak, landing 2021 via Wayback, diskusi komunitas; (b) **sesi login Anda (3 Sep 2026)** — screener, stock_detail, broker_summary, FAQ. NeoBDM tidak menyebut nama vendor datanya, tapi granularitas & jadwal update-nya kini terkonfirmasi langsung. Pemeriksaan bersifat read-only, tanpa ekspor/harvest massal.

### 3.1 Fakta terkonfirmasi

| Aspek | Temuan |
|---|---|
| Teknologi | Django + AdminLTE + jQuery (MPA, bukan SPA), di belakang Cloudflare. Server **di Indonesia** (per Kebijakan Privasi). Web-only, **tidak ada** aplikasi mobile. |
| Operasi | Kecil. Kontak = `neobdm@gmail.com` + 1 nomor HP (+62 851-1130-4815). **Tidak ada nama PT/badan hukum** di situs. Branding "© 2017–2026" (rilis web Jan 2021). |
| Lisensi data | ToS Pasal 10: konten "milik Kami **atau pihak yang memberikan lisensi kepada Kami**" → mereka **memakai data pihak ketiga berlisensi**, tapi **tidak menyebut** siapa. |
| Posisi produk | ToS Pasal 3–4: murni "informasi & edukasi", bukan penasihat investasi, tidak ada fitur upload stockpick. |
| Harga | Basic Rp35rb/1bln, Plus Rp68rb/2bln, Pro Rp200rb/6bln. **Sangat murah.** |
| Fitur (landing 2021 + path URL) | Foreign Flow, Retailmologi, **Sultanmologi**, **Zombiemologi**, Market/Sectoral Summary, Transaction Chart, Money Flow Map, Volume Price Analysis, **Balance Position Analysis (peta kepemilikan)**, `broker_summary`, `broker_stalker`, `rotation_chart`, `sector_activity`, `stock_detail`, inventory chart, done detail, watchlist, koneksi Telegram, livestream/tutorial. |

### 3.2 KONFIRMASI dari dalam produk (login user, 3 Sep 2026)

Diverifikasi langsung dengan sesi login Anda. Semua "dugaan" sebelumnya kini **terkonfirmasi**:

| Yang dicek | Temuan langsung |
|---|---|
| **FAQ resmi** "Apakah NeoBDM menggunakan data realtime?" | *"Tidak. NeoBDM tidak menggunakan data realtime, jadi bersifat **end-of-day (EOD)**. Update data tergantung jam berapa bursa membuka data tersebut."* — pernyataan resmi mereka. |
| Screener "Screening Saham" | Label **"Update: 2 Sep 2026"** (hari kerja sebelumnya). Kolom: Symbol, %Fd0 (foreign flow D0), Close, Chg1. Ada tombol Export. |
| Halaman `stock_detail` (BBRI) | **"Last Updated: 2026-09-02"**. Transaction Chart dengan seri harian **F / R / I / Z** sejak 2026-06-23. Broker Summary chart seri **F & R** harian. |
| **"Analisa Kepemilikan"** di stock_detail | Tertulis eksplisit: **"Data per 31 aug 2026 XLSX"** + link unduh XLSX. → sumbernya **file XLSX bulanan KSEI** (Komposisi Kepemilikan), snapshot **akhir bulan**. Menampilkan holder besar (Danantara, INA/SWF, EPF, DJS Ketenagakerjaan) dgn kolom Scrip / Scripless. |
| **"BBRI Balance Position Chart"** | Deret waktu **BULANAN** (`2024-09-01` → `2026-08-01`, titik: Okt'24, Jan'25, Apr'25…). 18 seri Foreign/Local × {Asuransi, Korporat, Dapen, Bank, Individual, Reksadana, Sekuritas, Yayasan, Lainnya} + %Institution/%Retail/%Foreign. = **persis struktur file kepemilikan bulanan KSEI**. |
| **"Dashboard Kepemilikan"** di screener | Tab **bulanan**: Aug 2026 / Jul 2026 / Jun 2026. Panel: "Indikasi Nominee", "**Scripless Bertambah/Berkurang**", "Lokal Individu Berkurang". Semua turunan dari file KSEI bulanan. |
| Halaman `broker_summary` | Filter **All / F / D** (flag Domestik/Asing ada), toggle **Net/Val**, date range picker. Data 3 Sep (hari ini) = "Data tidak tersedia" → rilis EOD. |
| Rotation / Sector | RRG (Improving/Outperform/Weakening/Underperform), Daily & Weekly — komputasi dari data indeks/sektor. |
| Owner | Disebut **"pak Wira"** di FAQ. |

**Kesimpulan sumber data NeoBDM (kini pasti):**
1. **Broker summary + foreign flow per emiten — EOD** (harian, post-close). Punya flag D/F. Kemungkinan besar via **redistributor IDX** (RTI Infokom / IQ Plus / IDX-STI) atau agregator EOD — bukan feed real-time enterprise. NeoBDM tidak menyebut vendornya (ToS Pasal 10: "pihak yang memberi lisensi kepada Kami").
2. **Kepemilikan / "Balance Position" — file XLSX KSEI bulanan** (Komposisi Kepemilikan Lokal-Asing), snapshot akhir bulan. **Bukan harian, bukan T+2.**
3. **"Sultanmologi/Zombiemologi/Nominee" = label heuristik buatan NeoBDM** di atas (1) dan (2). Ini "resep", bukan sumber data.
4. **Fundamental** — dari filing/vendor.

### 3.3 Kesalahpahaman yang beredar — JANGAN dikutip sebagai fakta

Klaim populer di internet: *"NeoBDM mengambil data dari Stockbit (bid-offer, broker summary, broker flow) + RTI & KSEI."*

**Ini keliru diatribusikan.** Sumbernya = **satu kreator konten TikTok/Twitter** (akun `@ceritamarket` / materi Jihad Brahmantyo) yang menjelaskan **alur kerja pribadinya**: dia menarik data dari Stockbit/RTI/KSEI, lalu **menggabungkannya dengan ChatGPT + NeoBDM** untuk analisa. Ringkasan mesin pencari mencampuradukkan ini menjadi "sumber data NeoBDM". **Bukan pernyataan NeoBDM.** ("Menghubungkan broker ke Stockbit" yang juga sering disebut = fitur link akun sekuritas untuk pantau porto, bukan sumber data.)

### 3.4 Implikasi untuk Vektor Gauss

- NeoBDM **tidak punya sumber data rahasia** — kemungkinan besar: **broker summary EOD (via redistributor IDX) + kepemilikan KSEI bulanan + fundamental vendor**. Sama persis dengan jalur yang tersedia untuk Anda.
- Yang membedakan NeoBDM **bukan data**, tapi: (a) tabel mapping broker + threshold heuristik ("Sultan/Zombi") yang sudah dikalibrasi bertahun-tahun, (b) kurasi UI, (c) komunitas + konten edukasi + livestream, (d) harga sangat murah (Rp35rb) → sulit disaingi dari sisi harga.
- **C1 TERKONFIRMASI PENUH:** "Balance Position" NeoBDM = deret **bulanan** dari file KSEI (label harfiah "Data per 31 aug 2026 XLSX"). NeoBDM **tidak melakukan** "audit rekonsiliasi T+2 harian" yang jadi Pilar 4 / senjata utama blueprint Gemini. Yang NeoBDM lakukan: cross-check **perubahan scripless bulanan** vs flow harian ("Scripless Bertambah/Berkurang", "Lokal Individu Berkurang"). Blueprint Gemini menjanjikan fitur yang **produk aslinya pun tidak punya**.
- **Harga jual NeoBDM Rp35rb/bln** vs rencana blueprint Gemini "Rp99rb/bln" — asumsi pricing di blueprint perlu ditinjau ulang total.

### 3.5 Sisa yang belum pasti

- **Nama vendor** broker-summary EOD NeoBDM (redistributor mana). Tidak kritikal — jalurnya sama untuk Anda apa pun vendornya. Bisa ditanyakan langsung ke `neobdm@gmail.com` (pertanyaan pra-langganan).
- Delay persis update EOD NeoBDM (mereka bilang "tergantung jam bursa membuka data"). Dari observasi: data H-1 sudah tampil pagi H+1; data H belum ada.
- Kedalaman histori broker summary di NeoBDM (chart transaksi hanya tampil ~10 minggu; entah backend simpan lebih panjang).

---

## Langkah berikutnya

1. Kirim BAGIAN 1 ke 1 redistributor lokal (RTI/IQ Plus) + IDX Data Services.
2. Kirim BAGIAN 2 ke KSEI helpdesk + tanya ORCHiD.
3. Kumpulkan jawaban → putuskan Skenario A vs B (lihat INVENTARIS §7) → baru desain skema & pipeline.

**Catatan penting dari §3:** NeoBDM sendiri = **broker summary EOD + kepemilikan KSEI bulanan**. Kalau Anda cuma ingin *menyamai* NeoBDM, Skenario B (beli broker summary EOD) sudah cukup — tidak perlu mengejar data T+2 harian yang blueprint Gemini janjikan (dan yang NeoBDM pun tidak punya).
