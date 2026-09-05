# Inventaris Sumber Data Resmi — IDX + KSEI

**Status:** Inventaris. **Tidak ada kode / scraper dibuat.** Endpoint dicantumkan sebagai fakta referensi, bukan instruksi eksekusi.
**Metode:** Pengecekan langsung via browser ke `idx.co.id`, `web.ksei.co.id` (3 Sep 2026) + pencarian web. Semua yang ditandai **[verified]** dilihat langsung sesi ini; **[perlu konfirmasi]** = belum dipastikan, hubungi sumber.

Baca bersama [ANALISIS-VALIDASI.md](ANALISIS-VALIDASI.md) — inventaris ini mengonfirmasi temuan C1 & C2 di sana. Ringkasan per-tier + peran tiap sumber dalam ekosistem aplikasi: [RENCANA-FASE-2.md](RENCANA-FASE-2.md) §2.

---

## 0. Ringkasan — apa yang tersedia untuk apa

| Kebutuhan Vektor Gauss | Sumber terbaik | Grain | Frekuensi | Akses | Biaya |
|---|---|---|---|---|---|
| Price bars (OHLCV) per emiten | IDX **Stock Summary** (publik) | ticker × hari | EOD ~17:00 WIB | Publik, tanpa login | Gratis |
| **Net foreign flow** per emiten | IDX **Stock Summary** (kolom `ForeignBuy`/`ForeignSell`) | ticker × hari | EOD | Publik | Gratis |
| Papan negosiasi terpisah | IDX Stock Summary (`NonRegular*`) | ticker × hari | EOD | Publik | Gratis |
| **Broker Summary per emiten** (beli/jual per broker) — inti bandarmologi | **Tidak dijual siap pakai** — agregasi sendiri dari IDX Equity & Derivative Log Data (Rp3jt/bln, ada kode broker, §6bis) **atau** vendor API | ticker × broker × hari | EOD (Log Data) / real-time (Order/ITCH) | **Berlangganan / lisensi** | **Berbayar, tidak publik — harga resmi kini diketahui, lihat §6bis** |
| Broker Summary agregat pasar (bukan per emiten) | IDX **Broker Summary** (publik) | broker × hari | EOD | Publik | Gratis — **tapi bukan yang dibutuhkan** |
| Kepemilikan Lokal vs Asing per emiten | KSEI **Kepemilikan Efek (Lokal-Asing)** | ticker × kategori investor × akhir bulan | **BULANAN** | Publik | Gratis |
| **Laporan kepemilikan >5% + direksi/komisaris** (POJK 4/2024) | IDX **GetAnnouncement** `keyword=LAPORAN KEPEMILIKAN` → form PDF KSEI | emiten × filing | ~harian (~13/hari bursa) | Publik (Cloudflare — IP residensial) | Gratis — **`vg collect-disclosure`** |
| Corporate action (split, dividen, HMETD, merger) | KSEI **List of CA Activities** + IDX pengumuman | emiten × event | Berkala | Publik | Gratis |
| Statik efek / master (listed shares, dsb) | KSEI **Master File Efek** / IDX | efek | Berkala | Publik | Gratis |
| Fundamental (lapkeu, rasio) | IDX **Financial Statements** / IDX Data Reference | emiten × kuartal | Kuartalan | Publik (PDF) / lisensi (terstruktur) | Gratis / berbayar |

**Kesimpulan cepat:**
- **Yang gratis & mudah:** harga EOD + net foreign flow per emiten + corporate action.
- **Yang bulanan (bukan harian):** seluruh data kepemilikan KSEI. Tidak ada feed settlement T+2 publik.
- **Yang berbayar & jadi penentu:** broker summary per emiten. Tanpa ini, "bandarmologi" tidak jalan.

---

## 1. IDX — sumber PUBLIK (tanpa login, gratis)

Situs `idx.co.id` = Nuxt SPA di belakang **Cloudflare**. Data tabel diambil dari endpoint JSON internal berpola `https://www.idx.co.id/primary/...`. Ada date picker + tombol **Download** (CSV/Excel) di sebagian besar halaman. **Catatan ToS:** IDX punya Syarat & Ketentuan penggunaan situs + lisensi data; pengambilan otomatis skala besar dari situs publik ada di wilayah abu-abu — jalur bersih untuk volume/historis adalah lisensi (§2).

### 1.1 Stock Summary — **[verified]** paling berguna & gratis
- **Halaman:** `idx.co.id/en/market-data/trading-summary/stock-summary`
- **Endpoint terlihat:** `GET /primary/TradingSummary/GetStockSummary?length=9999&start=0` (+ parameter tanggal) → JSON.
- **Isi per baris (per ticker, per hari):** `Date, StockCode, StockName, Previous, OpenPrice, FirstTrade, High, Low, Close, Change, Volume, Value, Frequency, Offer, OfferVolume, Bid, BidVolume, ListedShares, TradebleShares, WeightForIndex, ForeignSell, ForeignBuy, NonRegularVolume, NonRegularValue, NonRegularFrequency, IndexIndividual, DelistingDate, Remarks`.
- **963 saham** dalam satu respons.
- **Penting:**
  - `ForeignBuy` / `ForeignSell` = **volume (lembar)**, bukan nilai rupiah. Untuk nilai, perlu aproksimasi (× VWAP/harga rata-rata). **[perlu konfirmasi]** apakah angka ini sudah termasuk semua papan atau reguler saja.
  - `NonRegular*` = papan **negosiasi/tunai** dipisah — penting untuk memisahkan crossing/wash-sale dari flow reguler.
  - Ada tab **Margin** & **Short Selling** di halaman yang sama.
- **Historis:** date picker mundur; **[perlu konfirmasi]** seberapa jauh (biasanya beberapa tahun). Untuk backfill panjang → lisensi.

### 1.2 Broker Summary (publik) — **[verified]** agregat saja, BUKAN per emiten
- **Halaman:** `idx.co.id/en/market-data/trading-summary/broker-summary`
- **Isi:** 88 broker × { `Company Code, Company Name, Volume, Value, Frequency` } untuk **seluruh pasar dalam 1 hari**. Ada date picker + Download.
- **Tidak ada:** rincian per saham, tidak ada split beli/jual, tidak ada flag Domestik/Asing.
- **Implikasi:** ini **tidak bisa** dipakai untuk CR3 / net flow per emiten. Yang dibutuhkan Vektor Gauss ("broker X beli saham Y sekian miliar") **tidak ada di situs publik IDX** sejak kode broker intraday dibatasi (± 2021). Harus dari jalur berlisensi/vendor (§2, §4).

### 1.3 Halaman publik lain yang berguna
| Halaman | Isi | Guna untuk VG |
|---|---|---|
| **Trading Summary & Recapitulation** | Rekap volume/nilai/frekuensi per instrumen (Stock/Warrant/ETF/DIRE) | Sanity-check total pasar |
| **Index Summary** | Nilai indeks (IHSG, LQ45, IDX sektoral) harian | Rotation chart / sector activity |
| **Statistical Reports → Digital Statistic / Statistics** | Rekap bulanan/tahunan: net foreign, kapitalisasi, per sektor | Konteks makro, backfill agregat |
| **Financial Statements & Annual Report** (Listed Companies) | Lapkeu kuartalan (PDF/XBRL zip) per emiten | Fundamental (prioritas rendah) |
| **Announcement / Keterbukaan Informasi** | Aksi korporasi, RUPS, dsb | Filter event saat baca flow |

---

## 2. IDX — sumber BERLISENSI (IDX Data Services)

- **Halaman:** `idx.co.id/en/products/idx-data-services/` — **[verified]**
- **Model:** data system-to-system, kategori **real-time / delayed / end-of-day / log**. Lisensi dibedakan **Display** vs **Non-Display** (4 kategori pemakaian).

### 2.1 Produk EOD yang relevan
| Produk | Perkiraan isi | Untuk |
|---|---|---|
| **IDX Equity EoD** / **EoD Professional** / **EoD Basic** | Harga & ringkasan perdagangan EOD | Price bars resmi |
| **IDX Equity EoD Recapitulation** | Rekap perdagangan EOD — **kemungkinan besar termasuk rekap broker** | **[perlu konfirmasi]** apakah ini = broker summary per emiten |
| **IDX Equity EoD Indices / Indices Weight** | Konstituen & bobot indeks | Sektoral / rotation |
| **IDX ITCH** (Total View / Basic / Last Sale) | Feed order-by-order real-time | Order-level — tapi real-time, mahal, kode broker tetap dianonimkan intraday |
| **IDX-i Equity Delayed/EoD Summary** | Ringkasan (delayed/EOD) via redistributor | Kandidat broker/flow summary |
| **IDX Data Reference** | Lapkeu, aksi korporasi, disclosure >800 emiten (terstruktur) | Fundamental + CA terstruktur |

### 2.2 Cara akses
- **Direct:** leased line via **NSP** (Arthatel, CBN, Matrix, Biznet, Lintasarta, IPC, TNSI) → enterprise, mahal.
- **Indirect (lebih realistis):** lewat **Redistributor**:
  - **Lokal:** Perum LKBN **Antara / IMQ**, **PT IQ Plus Prima**, **PT RTI Infokom**, **PT IDX Solusi Teknologi Informasi (IDX-STI)**.
  - **Asing:** Bloomberg, Refinitiv, FactSet, Morningstar, ICE Data, SIX Financial, Baha, Active Financial Systems, FIS.
- **Biaya:** tabel fee di halaman produk **kosong** ("hubungi IDX / redistributor"), tapi **katalog harga resmi lengkap ditemukan terpisah dan publik** — lihat §6bis (RESOLVED 2026-09-05). Ini item anggaran yang tadinya hilang dari blueprint Gemini, sekarang terisi.
- **Kewajiban:** tanda tangan **License Agreement** + **Reporting** rutin pemakaian data ke IDX. Redistribusi (mis. tampilkan ke user publik) butuh lisensi **Display** + izin jadi Redistributor.

---

## 3. KSEI — sumber PUBLIK

Situs `web.ksei.co.id`. **Semua data publik KSEI bersifat bulanan / berkala — tidak ada feed harian per emiten.**

### 3.1 Kepemilikan Efek (Lokal-Asing) — **[verified]** — INI "data KSEI" yang dimaksud blueprint
- **Halaman:** `web.ksei.co.id/archive_download/holding_composition`
- **Isi:** posisi saldo kepemilikan **per efek**, dipecah **Lokal vs Asing** dan per **tipe investor** (individu, korporasi, institusi keuangan/bank, asuransi, dana pensiun, reksa dana, sekuritas, yayasan, lainnya — lihat "Panduan Klasifikasi Investor Institusi" di Data & User Guide).
- **Frekuensi:** **AKHIR BULAN** saja. Contoh entri live: `31 Agustus 2026, 31 Juli 2026, 30 Juni 2026, …`.
- **Arsip di situs:** minimal tahun **2023–2026** terlihat langsung. Lebih lama → **[perlu konfirmasi]** (permintaan ke KSEI / mirror komunitas).
- **Format:** unduhan file (ZIP berisi Excel/CSV). **[perlu konfirmasi]** layout kolom persisnya.
- **KONSEKUENSI (ulangi dari C1):** ini **bukan** "Scripless Balance Position harian T+2". **Audit wash-sale T+2 per emiten sebagaimana dirancang Gemini (Pilar 4) tidak bisa dibangun dari data publik.** Yang bisa: tren kepemilikan asing/institusi **bulanan** (lagging, kasar).

### 3.2 Data Statistik KSEI — **[verified]**
- **Halaman:** `web.ksei.co.id/publications/Data_Statistik_KSEI`
- **Isi:** PDF "Statistik Pasar Modal Indonesia" — agregat: jumlah SID, nilai aset C-BEST, demografi investor, kepemilikan per tipe investor level pasar.
- **Frekuensi:** **bulanan** (arsip Des 2020 – Jul 2026 terlihat).
- **Guna:** konteks makro, bukan sinyal per emiten.

### 3.3 List of CA Activities — **[verified]** — penting untuk adjustment
- **Halaman:** Data & User Guide → "LIST OF CA ACTIVITIES", pilih tahun (2001–2026).
- **File XLS:** Jadwal CA keseluruhan; Dividen Tunai; Dividen Saham/Bonus; List HMETD/Waran; RUPS/RUPO; **Stock Split / Reverse Stock**; Distribusi Bunga & Pelunasan Pokok; Merger.
- **Guna:** tabel `corporate_actions` untuk menyesuaikan deret harga & jumlah saham (celah §4 di ANALISIS-VALIDASI).

### 3.4 Lain-lain KSEI
| Item | Isi | Catatan |
|---|---|---|
| **Master File Efek** / **Data Statik Efek** (Saham/Obligasi/Waran/HMETD) | Master statik sekuritas (ZIP) | Dimensi `securities` |
| **Grafik / Graph** (`ksei.co.id/data/graph`) | Visual kepemilikan lokal-asing | Sumber angka = file bulanan §3.1 |
| **AKSes KSEI** (`akses.ksei.co.id`) | Cek kepemilikan **rekening pribadi** investor | Login personal — **tidak** untuk bulk/agregat |
| **ORCHiD / Penelitian Online** | Portal data untuk **riset akademik** (granular) | Perlu **pengajuan/izin**; bukan unduhan bebas. **[perlu konfirmasi]** granularitas & syarat |
| **S-INVEST** | Data reksa dana | Di luar scope BDM saham |

---

## 4. Vendor pihak ketiga (jalur "broker summary siap pakai")

Kalau jalur lisensi IDX langsung terlalu berat, broker summary per emiten + foreign flow bisa dari API komersial (mereka yang urus lisensi redistribusi):

| Vendor | Wilayah | Klaim relevan | Catatan |
|---|---|---|---|
| **PT RTI Infokom** | Lokal | Redistributor resmi IDX; broker summary, foreign flow | Sumber di balik banyak app lokal |
| **PT IQ Plus Prima / Antara-IMQ** | Lokal | Redistributor resmi IDX | Enterprise feed |
| **Sectors.app** | Lokal | API-first, "99% coverage IDX", lapkeu + LLM-ready | Cek apakah broker summary termasuk |
| **GOAPI.io** | Lokal | "API data saham Indonesia" real-time + indikator | Cek broker summary & histori |
| **Invezgo** | Lokal | Eksplisit sebut "**broker data, foreign flow**", REST + SDK | Kandidat kuat — **[perlu konfirmasi]** kedalaman & lisensi |
| **OHLC.dev** | — | IDX real-time + historis, Redis cache | Fokus harga |
| **Twelve Data** | Asing | XIDX EOD/intraday | Harga saja, bukan broker summary |
| Bloomberg / Refinitiv / FactSet / Morningstar | Asing | Redistributor resmi IDX | Mahal, untuk institusi |
| Wrapper GitHub / RapidAPI (mis. "IDX-API", "indexalpha-api") | — | Scrape situs IDX | **Jangan untuk produksi** — melanggar ToS, rapuh, tanpa lisensi |

**[perlu konfirmasi] untuk tiap vendor:** (a) broker summary **per emiten** dengan split beli/jual + flag D/F? (b) kedalaman histori (tahun)? (c) lisensi memperbolehkan **menampilkan ulang data turunan ke publik**? (d) harga.

---

## 5. Pemetaan ke metodologi Vektor Gauss

| Komponen metodologi | Sumber | Status kelayakan |
|---|---|---|
| Net Flow kasta (Asing vs Ritel vs Institusi) | IDX Stock Summary `ForeignBuy/Sell` (asing); broker summary + mapping (institusi vs ritel) | **Asing: layak & gratis.** Institusi vs ritel: butuh broker summary berbayar + mapping (hipotesis) |
| CR3 / konsentrasi broker | **IDX tidak menjual "broker summary" siap pakai** (§6bis) — harus diagregasi sendiri dari **IDX Equity & Derivative Log Data** (order/trade mentah + Kode Broker, Rp3jt/bln, historis) atau **IDX Equity Real Time Order** (Rp19,8–22jt/bln, live) | Layak **hanya jika** beli data mentah + bangun agregasi sendiri. EOD/historis via Log Data lebih murah dari semua vendor pihak ketiga yang dicek (§4) |
| Rolling window D/W | Turunan dari net flow + `trading_calendar` | Layak — bangun kalender dari tanggal-tanggal IDX Stock Summary |
| Sultan / Zombi tagging | Butuh order-size / kode broker | **Kode broker TIDAK dianonimkan di produk berlisensi** (Log Data & Real Time Order keduanya punya field `Brokers Code`/`Buyer Code`/`Seller Code`, §6bis) — anggapan lama ("dianonimkan sejak 2021") ternyata hanya berlaku untuk feed **publik gratis**. Layak **jika** beli Log Data; tetap tidak layak dari sumber gratis |
| Freq Analyzer / jejak HFT | Butuh tick/ITCH intraday | Layak secara data **jika** beli IDX ITCH (Rp14,4–44 juta/bln tergantung tier, §6bis — mahal tapi kode broker **tercantum**, bukan anonim seperti dugaan sebelumnya) |
| Audit KSEI T+2 (Pilar 4) | Tidak ada feed publik | **Tidak layak** — ganti dengan "tren kepemilikan bulanan" (kasar, lagging) |
| Corporate action adjustment | KSEI List of CA + IDX announcement | **Layak & gratis** |
| Rotation chart / sector activity | IDX Index Summary + sektoral + `ForeignBuy/Sell` agregat sektor | **Layak & gratis** |
| Fundamental | IDX Financial Statements (PDF/XBRL) atau IDX Data Reference | Layak, effort parsing tinggi |

---

## 6. Yang MASIH harus dikonfirmasi langsung (sebelum keputusan arsitektur)

1. **KSEI:** Adakah produk **lebih sering dari bulanan** untuk kepemilikan per emiten (via ORCHiD / permintaan resmi / berbayar)? Ini menentukan hidup-matinya "senjata utama".
2. **KSEI:** Layout kolom file "Kepemilikan Efek (Lokal-Asing)" + seberapa jauh arsip historisnya.
3. ~~**IDX Data Services:** Apakah "IDX Equity EoD Recapitulation" atau "IDX-i Equity EoD/Delayed Summary" = broker summary per emiten?~~ **RESOLVED — lihat §6bis.**
4. ~~**IDX:** Harga IDX Equity EoD + Recapitulation (Display & Non-Display), via redistributor lokal.~~ **RESOLVED — lihat §6bis.**
5. **IDX:** Lisensi mana yang mengizinkan **menampilkan metrik turunan ke user berbayar** (Display + Redistributor?) — §6bis menjawab sebagian (kategori Non-Display ada, tapi kombinasi persis untuk "tampilkan turunan ke user berbayar" belum eksplisit di katalog; masih perlu ditanyakan ke `idxdata@idx.co.id`).
6. **IDX Stock Summary publik:** kedalaman date picker (berapa tahun mundur) + apakah `ForeignBuy/Sell` mencakup semua papan.
7. **Vendor** (Invezgo / Sectors / GOAPI): jawab 4 pertanyaan di §4. **Invezgo dicek 2026-09-05** (lihat catatan vendor di §4) — dokumentasi API-nya sendiri membatasi semua endpoint maks. 2 tahun historis (bertentangan dengan klaim marketing "15+ tahun sejak 2009") dan **melarang eksplisit redistribusi/jual-ulang data ke pelanggan lain**.
8. **Legal lokal:** apakah men-scrape endpoint `/primary/` publik IDX untuk histori panjang melanggar ToS IDX secara material (vs. sekadar dilarang formal).
9. **Log Data via redistributor?** Katalog resmi (§6bis) hanya mencantumkan "IDX Equity & Derivative Log Data" sebagai **Direct Connection** (butuh leased line NSP, biaya terpisah dari Rp3jt/bln lisensinya). Tidak jelas apakah produk ini juga tersedia **Indirect** lewat RTI/IQ Plus/IDX-STI tanpa infra NSP sendiri — tanyakan langsung.

---

## 6bis. RESOLVED 2026-09-05 — IDX Data Service Catalogue & Pricelist (resmi, publik)

Ditemukan via web search biasa (bukan scraping login): **`idx.co.id/media/2qalvu4z/20240923_idx-data-catalogue-pricelist-2024.pdf`** — 49 halaman, PDF publik tanpa login, terbit 23 Sep 2024, masih linked aktif dari idx.co.id per pengecekan hari ini. Ini menjawab tuntas item #3, #4 di atas (yang sebelumnya dicatat "tabel fee KOSONG → hubungi").

**Temuan kunci:**
- **IDX tidak menjual produk bernama "broker summary per emiten".** Field data tiap produk dicek satu per satu — hanya dua bentuk yang ada: (a) data mentah per-order/per-trade dengan `Brokers Code`/`Buyer Code`/`Seller Code` (real-time atau log historis — kamu agregasi sendiri jadi broker summary), atau (b) rekap **agregat** Domestik vs Asing saja (`IDX Equity EoD Recapitulation`, TANPA breakdown per broker). Jadi **#3 di atas terjawab: EoD Recapitulation BUKAN broker summary**, dan tidak ada produk EoD lain yang lebih dekat — semua vendor pihak ketiga (NeoBDM, Invezgo, RTI, dst.) yang mengaku jual "broker summary" pasti mengagregasi sendiri dari data mentah ini; itu produk turunan mereka, bukan produk resmi IDX.
- **Kode broker TIDAK dianonimkan di produk berlisensi** — `IDX Equity Real Time Order`, `IDX Equity & Derivative Log Data`, dan seluruh varian `IDX ITCH` eksplisit mencantumkan `Brokers Code` per order dan `Buyer Code`/`Seller Code` per trade. Anggapan lama "kode broker intraday dianonimkan sejak ±2021" (dipakai untuk menyimpulkan Sultan/Zombi/Freq-Analyzer "tidak layak") ternyata **hanya berlaku untuk feed publik gratis di website IDX**, bukan untuk data berlisensi.
- **Harga bulanan (Non-Group, lokal/IDR — tier termurah untuk pihak non-anggota bursa):**

| Produk | Isi | Direct | Indirect (redistributor) |
|---|---|---|---|
| **IDX Equity & Derivative Log Data** (historis, "past trading data") | Order+Trade w/ kode broker | **Rp3.000.000/bln** | tidak dicantumkan di katalog (§6 #9) |
| IDX Equity Real Time Order | Sama, live | Rp22.000.000/bln | Rp19.800.000/bln (IDX-i) |
| IDX Equity EoD Recapitulation | Agregat D/F saja, bukan per broker | Rp700.000/bln | Rp650.000/bln |
| IDX Equity EoD Professional | OHLCV semua papan reguler | Rp3.000.000/bln | Rp2.700.000/bln |
| IDX Equity EoD Basic | OHLCV ringkas | Rp2.500.000/bln | Rp1.300.000/bln |
| IDX Data Reference (>800 emiten: lapkeu, CA, disclosure, **status suspend/unsuspend**) | — | Rp10.000.000/bln | Rp9.000.000/bln |
| IDX ITCH (Total View/Basic/Last Sale) | Order-level + broker code, real-time | Rp16–44jt/bln | Rp14,4–39,6jt/bln |
| IDX Non-Display Usage Kategori 2 (analisis investasi/kuantitatif — kemungkinan lisensi tambahan wajib untuk pemakaian internal) | — | Rp5.500.000/bln | Rp5.000.000/bln |

- **Catatan biaya tersembunyi:** harga di atas cuma lisensi data. Koneksi **Direct** secara teknis butuh leased line via NSP resmi (Biznet, Lintasarta, Artha Telekomindo, dll. — daftar lengkap di PDF hlm. 40), biaya jaringan terpisah dan tidak tercantum. **Indirect** (lewat RTI/IQ Plus/Antara-IMQ/IDX-STI) tidak butuh NSP tapi produk Log Data tidak terlihat punya varian Indirect di katalog ini.
- **Uang jaminan (deposit):** 300% dari biaya langganan bulanan, berlaku untuk semua produk. Free trial 1 bulan setelah subscribe.
- **Fee eksklusi relevan:** "Unit of Count Fee" (biaya tambahan per end-user untuk Real Time Data) **tidak berlaku** untuk "pemakaian internal untuk kepentingan sendiri" — jadi tool personal (bukan disebarkan ke banyak end-user) kemungkinan bebas dari fee ini.
- PDF disimpan lokal untuk referensi (tidak commit ke repo — file besar/turunan pihak ketiga): scratchpad sesi 2026-09-05.

**Masih belum terjawab (lanjutan §6):** kombinasi lisensi persis untuk kasus "tampilkan metrik turunan ke user berbayar" (item #5), ketersediaan Log Data via redistributor (item #9), dan penerapan biaya NSP riil untuk direct connection.

---

## 7. Rekomendasi jalur (tanpa kode)

**Skenario A — "Layak sekarang, gratis" (validasi metodologi dulu):**
Harga + net foreign flow per emiten (IDX Stock Summary publik) + corporate action (KSEI) + kepemilikan bulanan (KSEI). Cukup untuk menguji: apakah "harga vs net foreign flow + tren kepemilikan bulanan" punya daya prediksi? Kalau **tidak**, tidak perlu bayar broker summary.

**Skenario B — "Produk penuh":**
Skenario A + langganan **broker summary per emiten EOD** dari redistributor IDX atau vendor lokal (Invezgo/RTI/IQ Plus). Ini baru memungkinkan CR3, net flow institusi vs ritel, mapping broker. Anggarkan biaya lisensi (**angka harus didapat dari §6.4**) — kemungkinan jauh > biaya server.

**Jangan:**
- Bergantung pada scrape situs IDX untuk histori panjang / produksi komersial (ToS + rapuh).
- Menjanjikan fitur "audit T+2", "Sultan/Zombi", "Freq Analyzer" — datanya tidak ada di jalur publik.

**Langkah berikut yang saya sarankan (masih tanpa kode):**
kirim email/pertanyaan ke: (a) IDX Data Services, (b) 1–2 redistributor lokal, (c) KSEI (soal ORCHiD / frekuensi kepemilikan), (d) Invezgo + Sectors.app. Bawa daftar pertanyaan §6. Setelah jawaban masuk → putuskan Skenario A vs B → baru desain skema & pipeline.
