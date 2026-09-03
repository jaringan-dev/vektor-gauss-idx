# Lanskap Kompetitor — Platform Bandarmologi Indonesia

Pendamping [ANALISIS-VALIDASI.md](ANALISIS-VALIDASI.md) & [INVENTARIS-SUMBER-DATA.md](INVENTARIS-SUMBER-DATA.md).
Survei per 3 Sep 2026 (landing page publik + sebagian dengan login). **Tidak ada kode.**

---

## 0. Ringkasan eksekutif

**Pasarnya padat, murah, dan tanpa moat data.**

- Minimal **7 platform web khusus bandarmologi** aktif + Stockbit/RTI/aplikasi broker + vendor data API.
- **Salah satu kompetitor (IDXAlpha) 100% gratis** dan sudah persis mengimplementasikan konsep "Vektor Gauss": 13 sinyal berbobot → skor komposit −2..+2, plus broker summary + foreign flow + kepemilikan KSEI + sentimen IHSG. Metodologinya dipublikasikan terbuka.
- **Plafon harga sangat rendah:** Rp21rb–130rb/bln untuk tool khusus; Stockbit Pro Rp200rb sebagai jangkar "premium". Hanya Smart Money Flow yang berani Rp300rb.
- **Semua pakai sumber data sama:** IDX EOD broker summary + foreign flow + kepemilikan KSEI. Tidak ada yang punya data eksklusif. Diferensiasi lewat data = mustahil.
- **Koreksi untuk C1:** IDXRadar membuktikan data kepemilikan **>5% / insider** memang tersedia **harian** dari KSEI (kewajiban lapor pemegang saham pengendali/>5%). Yang tetap tidak publik = saldo custody agregat Lokal/Asing per emiten harian (itu bulanan).

**Implikasi untuk Vektor Gauss:** membangun "platform bandarmologi ke-8" dengan metodologi yang sama, di pasar dengan pricing power ~nol dan kompetitor gratis, sulit dijustifikasi. Kalau tetap jalan, diferensiasi **wajib** bukan di "punya skor bandarmologi" (sudah komoditas) melainkan di sesuatu yang lain — dan blueprint Gemini belum punya itu.

---

## 1. Kompetitor langsung — platform web khusus bandarmologi

### 1.1 NeoBDM — `neobdm.tech`
| | |
|---|---|
| Model | Web-only, langganan. **Rp35rb/1bln**, Rp68rb/2bln, Rp200rb/6bln |
| Data | Broker summary EOD (flag D/F) + **kepemilikan KSEI bulanan** (XLSX, "Data per 31 aug 2026") + fundamental |
| Fitur | Screener "Top Akum Asing", Transaction Chart F/R/I/Z, Broker Summary, Money Flow Map, Balance Position (bulanan), Rotation, **Sultanmologi/Zombimologi** (label heuristik sendiri), Lot Sizing, alert Telegram, livestream/tutorial |
| Kekuatan | Sudah mapan (sejak 2021), komunitas + konten edukasi + livestream, harga termurah |
| Kelemahan | Tech lama (Django/AdminLTE/jQuery), UI padat, kepemilikan cuma bulanan, tidak ada nama PT |
| Update | EOD ("tergantung jam bursa membuka data") |

### 1.2 Terminal B — `terminalb.id` *(beta)*
| | |
|---|---|
| Model | Langganan. **Trial Rp21rb sekali**, PRO **Rp99rb/bln**, INTEL **Rp129rb/bln** (Rp349rb/3bln) |
| Data | **Broker summary EOD seluruh emiten BEI** ("data publik IDX, tertunda/teragregat") + insider + whale. Update hanya jam bursa Sen–Jum |
| Fitur | 4 modul: Emiten (11 panel — chart, broksum, orderbook, statistik, lapkeu, asing, insider, teknikal, narasi), **Radar Bandar** (skor AKUM/DIST 1–30H), **Screener bahasa natural** ("akum asing 5H, vol naik"), Sektor. "Broker Fingerprint", "Aliran Smart Money", "Gorengan Radar", narasi AI |
| Positioning | "Dibangun untuk periset, bukan penjudi." Transparansi: no signal, sumber selalu disebut, edukasi. Cita "Sesuai POJK 13/2025" |
| Kekuatan | Modern, UI bagus, transparan, narasi AI, harga rendah |
| Kelemahan | Masih beta, tim kecil/solo, coverage & histori belum matang |

### 1.3 IDXAlpha — `idxalpha.com` ⚠️ paling mirip rencana Anda
| | |
|---|---|
| Model | **100% GRATIS.** Login Google |
| Data | Broker summary IDX + foreign flow + **kepemilikan KSEI 955+ emiten** + makro (FRED, CNN Fear&Greed, CBOE VIX). Update EOD setelah tutup bursa |
| Metodologi | **Dipublikasikan terbuka:** skor komposit −2..+2 dari **13 sinyal berbobot** — Top-5 Broker Accumulation 23%, Broker Average Cost 18%, Broker Concentration 13%, Float Accumulation 13%, Foreign Flow 9%, Maker-Taker Delta 7%, Volume Profile 5%, Retail FOMO 3%, OBV Divergence 3%, Price Compression 3%, Relative Strength 2%, Bandar Reversal 1% |
| Fitur | Bandar Screener IDX250, IHSG Macro Intelligence (8 modul), **Beneficial Ownership / peta konglomerasi + insider alert**, Sector Rotation Heatmap, Portfolio Tracker |
| **Kenapa penting** | Ini **persis** konsep "Vektor Gauss" (skor 0–100 dari pilar berbobot + KSEI + makro + Telegram-style alert), **sudah jadi, dan gratis.** Bahkan mereka **tidak** klaim audit T+2 — KSEI mereka = beneficial ownership. |

### 1.4 Smart Money Flow — `smartmoneyflow.id`
| | |
|---|---|
| Model | Free / **Rp300rb/bln** / **Rp3,6jt/thn** |
| Tesis | **Menolak broker summary** ("Mitos Broker Summary! Mudah dimanipulasi, fake crossing, kode broker dihapus intraday"). Ganti dengan **analisis volume murni (SMI)** + **Wyckoff** (Akumulasi→Markup→Distribusi→Markdown) |
| Data | EOD. Volume/price action, "tekanan beli di offer", anomali volume |
| Fitur | Accumulation Screener, Sector Rotation, **AI Forecasting**, Distribution Warning, **Whale Network** (cross-ownership), Heatmap, 50+ metrik, Academy (Kyle Model 1985, order block, microstructure) |
| Klaim backtest | 71,2% train (2021–2025), 63,8% OOS, 30.108 sinyal sejak Jan 2024, 74,8% "combined" |
| Catatan | Marketing borderline signal-service ("PROFIT SECURED +24,6% H+5") meski klaim "bukan nasihat" |

### 1.5 IDXRadar — `idxradar.com` ⚠️ koreksi asumsi C1
| | |
|---|---|
| Model | **Rp50rb/bln**, satu paket |
| Data | **"Data kepemilikan saham HARIAN dari KSEI"** — aktivitas pemegang saham **>5%** (wajib lapor harian), peta kepemilikan **>1%** (Lokal/Asing, tipe investor), foreign flow, free float. "956 saham, 100% data resmi KSEI" |
| Fitur | Top Akumulasi/Distribusi harian (pemegang >5%), "Akumulasi Berulang" (pola lintas sesi), Peta Kepemilikan >1%, Foreign Flow (net + konsistensi 10H), **Volume Alert real-time** (09:00–16:15 WIB), **Free Float Screener** (4 tier likuiditas, 955 emiten) |
| **Kenapa penting** | Membuktikan data kepemilikan **>5%/insider TERSEDIA HARIAN** dari KSEI — bukan cuma bulanan. Ini melunakkan temuan C1: "audit T+2" penuh memang tidak ada, tapi **pergerakan pemegang besar bisa dilacak harian.** |

### 1.6 FlowMetrix — `flowmetrix.id` *(situs down saat dicek)*
Dari deskripsi: "Category Flow Logic" (pisah F vs D), **Broker Flow Heatmap** (intensitas beli-jual top player harian), **Inventory Tracker** (% barang broker yang masih nyangkut di harga bawah sebelum exit). Harga tidak ketemu.

### 1.7 IDXScreener — `idxscreener.com` *(situs error/down saat dicek)*
Dari deskripsi: screener AI 800+ saham, foreign flow, breakout, **ARA detector**, broker trend. Dari **Rp49rb**.

---

## 2. Vendor data (relevan untuk masalah sumber data Anda)

### 2.1 Index Alpha — `indexalpha.id` ⚠️ ini jawaban "vendor API" yang Anda cari
| | |
|---|---|
| Produk | **Broker summary + foreign flow IDX via API / CSV / Excel** |
| Endpoint | `/stocks/broker-summary` (per broker: buy/sell freq+volume+value, buy_avg/sell_avg), batch 50 ticker, `/foreign-flow`, `/news`. Param `investor=all` (bisa filter D/F) |
| Histori | **Sejak 2025-01-01** (hanya ~1,5 tahun — backfill pendek) |
| Harga | Free 5 req/hari · **Starter Rp200rb** (25rb req) · **Pro Rp350rb** (100rb req) · **Advanced Rp750rb** (250rb req) |
| Akses | Self-serve, sign-in Google, dok API, playground |
| Catatan risiko | Operasi kecil (kontak Telegram `@neon_scale`). Tidak menyebut status lisensi/redistributor IDX — kemungkinan besar men-scrape sendiri. Membangun produk komersial di atas API scraper lain = rantai risiko ToS berlapis. |

### 2.2 Lain (dari riset sebelumnya)
Invezgo (sebut "broker data, foreign flow"), Sectors.app, GOAPI.io, OHLC.dev. Redistributor resmi IDX: RTI Infokom, IQ Plus, IDX-STI, Antara/IMQ.

---

## 3. Platform besar dengan fitur bandarmologi (bukan spesialis)

| Platform | Fitur bandarmologi | Harga |
|---|---|---|
| **Stockbit** | **Bandar Detector** (broker summary, Pro-only), indikator **Bandar Movement / Bandar Value / Bandar Volume**, **Foreign Flow**, **Screener Bandarmology** (Big Accumulation, Foreign Flow Uptrend), Chartbit (100+ indikator, histori 10thn) | Stockbit Pro **Rp200rb/bln** |
| **RTI / RTI Business** | Bandarmology, broker summary + histori | Basic/Premium/Advance (bertingkat) |
| **IPOT** (IndoPremier) | Broker Summary | Gratis untuk nasabah |
| **Ajaib** | Broker summary | Gratis untuk nasabah |
| **MNC Trade New** | "Trader View" — bandarmology | Gratis untuk nasabah |
| **Mirae M-STOCK / HOTS, BIONS, MOST, POEMS** | Broker summary EOD standar | Gratis untuk nasabah |
| **Data Saham Indonesia** (Android, Kajo Kangin, sejak 2015) | Bandar detector, riwayat broker | 100rb+ pengguna Play Store |

---

## 4. Konten / edukasi (bukan tool)

Emtrade (Ellen May) — kursus + tools ringan · Bandarmology.net · datasahambei.com · emitennews · konten kreator (Jihad Brahmantyo/@Gieehad, dll). Ini pesaing *perhatian*, bukan produk.

---

## 5. Peta positioning

```
                 GRATIS
                   │
   IDXAlpha ●      │      ● IDXRadar (Rp50rb)
   (13 sinyal,     │      (KSEI >5% harian)
    KSEI, makro)   │
  ─────────────────┼───────────────────── HARGA →
   NeoBDM ●        │   ● Terminal B      ● Stockbit Pro
   (Rp35rb,        │   (Rp99–129rb,        (Rp200rb, ekosistem)
    komunitas)     │    modern, AI)
                   │                      ● Smart Money Flow
                   │                      (Rp300rb, anti-broksum,
              SPESIALIS                     Wyckoff+volume)
```

**Sumbu yang belum ramai / celah potensial:**
- **Data kepemilikan harian** (>5%/insider) digabung mulus dengan flow harian — IDXRadar fokus ke ownership, yang lain ke flow. Belum ada yang benar-benar mengawinkan keduanya + eksekusi bagus.
- **B2B / API + alert** untuk komunitas/grup trading (white-label) — kebanyakan B2C.
- **Backtest transparan & jujur** per sinyal (Smart Money Flow klaim, tapi marketing-y). Belum ada yang buka metodologi + hasil OOS secara kredibel.
- Kualitas **UX + kecepatan** (NeoBDM tertinggal; Terminal B baru mulai).

---

## 6. Kesimpulan untuk keputusan Anda

1. **"Skor bandarmologi dari pilar berbobot" sudah komoditas** — IDXAlpha memberikannya gratis, lengkap, dengan metodologi terbuka. Blueprint Gemini pada dasarnya = IDXAlpha yang belum dibangun.
2. **Pricing power ~nol.** Kompetitor Rp21rb–50rb dan gratis. Blueprint yang menargetkan Rp99rb/bln melawan arus.
3. **Tidak ada moat data.** Semua sumber sama. Yang membedakan pemain = eksekusi UX, komunitas, kredibilitas backtest, atau segmen (B2B/API).
4. **Koreksi C1:** kepemilikan >5%/insider **tersedia harian** (IDXRadar buktinya). "Audit T+2" penuh tetap tidak ada, tapi celah datanya lebih kecil dari perkiraan awal.
5. **Kalau tetap lanjut:** jangan bangun "spesialis bandarmologi ke-8". Pilih satu sudut tajam yang belum digarap (§5) dan bangun itu — bukan mereplikasi blueprint.

**Pertanyaan untuk Anda:** setelah lihat lanskap ini — masih mau bangun produk B2C bandarmologi, atau geser ke sudut lain (API/B2B, atau alat internal untuk trading Anda sendiri tanpa jual)? Jawaban ini menentukan apakah lanjut ke desain teknis atau berhenti.
