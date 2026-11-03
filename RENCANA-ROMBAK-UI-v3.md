# Rencana Rombak UI v3 — "Bisa dipahami, bisa diklik"

**Status:** rencana, belum ada kode (3 Nov 2026). Menggantikan arah `RANCANGAN-UI-UX-v2.md` untuk *isi dan bahasa* layar;
shell, token warna, dan prinsip kepatuhan di v2 tetap dipakai. Pemicu: umpan balik langsung dari pemilik —
*"saya tidak mengerti data yang disajikan, saham tidak bisa diklik, tidak ada istilah atau informasi tambahan."*

## 1. Diagnosis (jujur)

Dasbor sekarang dibangun sebagai **alat kerja bagi pembuatnya**. Itu menjelaskan semua keluhan:

| Keluhan | Akar masalah di kode/tampilan sekarang |
|---|---|
| Tidak mengerti angkanya | Kolom memakai jargon riset tanpa penjelasan: `span`, `edge H5`, `uHit`, `nLiq`, `CR3`, `konsist.`, `asing 1mg`, `kuadran`, `KSEI`. Tidak ada tooltip, glosarium, atau kalimat "artinya apa". |
| Saham tidak bisa diklik | Tidak ada halaman detail saham; kode saham hanya teks di `screener.html` dan `ranking.html`. |
| Tidak ada info tambahan | Tidak ada jalur dari angka → makna → bukti. Layar utama (`/`) adalah tabel statistik 24 tag, yaitu pertanyaan seorang peneliti, bukan pertanyaan seorang pembaca. |
| Peringatan yang selalu menyala | Strip "Broker ⚠ parsial" kuning hampir tiap hari (pembandingnya ≈262, EOD harian ±200). Peringatan permanen = diabaikan. |

Kesimpulan: bukan soal warna atau tata letak. Yang kurang adalah **lapisan penerjemah** antara data dan pembaca.

## 2. Prinsip rombak

1. **Satu halaman, satu pertanyaan, dijawab dengan kalimat biasa di bagian atas.** Angka datang sesudahnya sebagai bukti.
2. **Bahasa Indonesia sehari-hari.** Nama istilah tetap ditampilkan, tetapi didampingi nama biasa:
   "arus asing bersih", bukan "net foreign flow"; "persentase dikuasai 3 broker teratas (CR3)".
3. **Setiap istilah bisa dijelaskan di tempat** (garis putus-putus → kartu kecil: arti sederhana, cara hitung satu baris, contoh angka, "baca selengkapnya").
4. **Setiap saham dan setiap pola bisa diklik** → halaman detail. Tidak ada jalan buntu.
5. **Kalimat ringkasan dibuat dari aturan, bukan AI.** Templat deterministik yang bisa diaudit
   ("Asing menjual Rp24,4M hari ini, tetapi sepekan terakhir masih beli bersih Rp486,6M."). Tetap sesuai jaminan *tanpa LLM di mesin*.
6. **Kejujuran tidak dikurangi.** `n`, rentang waktu, dan biaya transaksi tetap tampil; gagal sama menonjol dengan lulus;
   tanpa skor 0–100; tanpa kata perintah beli/jual; disclaimer OJK di tiap layar.
7. **Dilipat, bukan dibuang.** Angka mentah untuk peneliti (edge H1/H5/H10, uHit, dst.) tetap ada di panel "Lihat angka lengkap".

## 3. Peta layar baru (berbasis pertanyaan)

```
Bilah atas:  [Cari saham / istilah…]            [?  Panduan]   [strip kesehatan data — ringkas]
Sidebar:
  Hari ini        "Apa yang terjadi di pasar hari ini?"                     BARU (menggantikan / )
  Saham           daftar + cari + filter bahasa biasa  → Detail saham        PERBAIKI + BARU
  Pola            "Pola apa yang dilacak, dan terbukti?" → Detail pola       BARU
  Rapor           rekam jejak jujur (forward-test) — versi ringkas + lengkap PERBAIKI (isi /)
  Data            kesehatan & cakupan (untuk operator)                       ADA (/coverage)
  Panduan         cara membaca · glosarium · apa yang TIDAK bisa dilakukan   BARU
```

### 3.1 Hari ini (beranda baru)
Pertanyaan: *"Apa yang terjadi hari ini, dan seberapa bisa dipercaya?"*
- **Satu paragraf kondisi pasar** dari aturan: arah IHSG, total arus asing, jumlah saham naik/turun.
- **Saham yang layak dilihat** (maks. 5, kriteria eksplisit di bawah daftar, mis. "arus asing bersih terbesar di antara saham likuid").
  Tiap baris: kode (klik), satu kalimat alasan, satu lencana status.
- **Rapor singkat:** "Dari N pola yang dilacak, 0 terbukti melewati biaya. Ini hasil jujur, bukan kegagalan sistem." → tautan ke Rapor.
- **Kesehatan data satu baris** ("Data lengkap sampai 3 Nov" / "ada 1 masalah → lihat"), bukan empat lencana.

### 3.2 Detail saham (halaman yang dituju setiap klik)
Pertanyaan: *"Apa yang sebenarnya terjadi pada saham ini?"*
1. **Kepala:** kode, nama, sektor, harga, perubahan, kuadran **dalam kata** ("Akumulasi: harga turun tetapi pembeli besar menyerap").
2. **Ringkasan dalam kalimat** (3–4 kalimat templat).
3. **Grafik harga 3–6 bulan** (candle), penanda tanggal dividen/split dari `adj_factor`.
4. **Arus asing & broker:** batang harian arus bersih; broker pembeli/penjual terbesar. Untuk sisi broker memakai **batas pasti** (`broker_bounds`):
   "asing: antara −6,3% dan −0,1% dari nilai transaksi", dengan penjelasan mengapa berupa rentang dan bukan satu angka.
5. **Kepemilikan KSEI bulanan** (asing/ritel/institusi, tren 3 bulan).
6. **Pola yang kena:** tiap tag = lencana + satu baris arti + rekam jejak pola itu (n, edge) + tautan ke Detail pola. Garis waktu 90 hari.
7. **Kotak "Yang kami tidak tahu":** cakupan data (mis. "data broker belum tersedia untuk saham ini"), sehingga tidak ada grafik kosong yang menyesatkan.

### 3.3 Detail pola (tag)
Pertanyaan: *"Pola ini apa, kapan muncul, dan apakah pernah menguntungkan?"*
Definisi dalam kata → aturan pemicunya (diambil dari konstanta kode agar tidak melenceng) → rekam jejak (n, rentang, edge setelah biaya, tren per bulan) →
sinyal terbaru (klik ke saham) → catatan metodologi. Pola n kecil diberi label dan tidak diurutkan di atas pola ber-n besar.

### 3.4 Daftar saham (perbaikan `/screener`, `/ranking` dilebur)
- Kolom diganti nama biasa: `chg` → "Ubah hari ini", `asing 1mg` → "Arus asing 1 minggu", `broker net` → "Beli-jual bersih broker", `konsist.` → "Hari konsisten (dari 10)".
- **Filter dalam bahasa biasa** (chip): "Asing sedang masuk", "Asing sedang keluar", "Harga turun tapi diserap", "Hanya saham likuid".
- Kolom **"Mengapa muncul?"** satu kalimat; seluruh baris bisa diklik; urut kolom; di HP jadi kartu.
- Ranking menjadi tab di halaman yang sama (1H/5H tersimpan di URL), bukan layar terpisah.

### 3.5 Rapor (isi `/` sekarang, disederhanakan)
Atas: kalimat jujur + tiga angka (pola dilacak, pola lulus, pola gagal). Tengah: kartu per pola dengan lencana
LULUS/GAGAL/PROBATION. Bawah (dilipat): tabel lengkap yang sekarang. Setiap judul kolom punya TermHint.

### 3.6 Panduan
- **Cara membaca layar ini** (3 contoh bergambar: satu baris saham, satu kartu pola, satu strip data).
- **Glosarium** (dicari, per abjad): tiap istilah = nama biasa, arti, cara hitung, contoh, mengapa penting, batasannya.
- **Apa yang TIDAK bisa dilakukan alat ini:** bukan rekomendasi, bukan sinyal beli/jual, data gratis punya celah, hasil riset 3,5 tahun belum menemukan edge yang melewati biaya.

## 4. Komponen baru dan teknis

| Komponen | Isi | Catatan teknis |
|---|---|---|
| `glossary` (satu sumber kebenaran) | ±45 istilah: kolom, 26 tag, kuadran, status gate, istilah strip data | berkas `src/vg/web/glossary.py`; filter Jinja `{{ "edge"|term }}` |
| **TermHint** | teks bergaris putus → kartu (klik/fokus/hover; bisa dengan keyboard) | tanpa dependensi; tercakup aksesibilitas (WCAG) |
| **PlainSummary** | kalimat dari aturan per saham/pasar/pola | `src/vg/web/narrative.py`, fungsi murni + tes; **daftar kata terlarang** diuji otomatis (tidak ada "beli/jual" imperatif, "sebaiknya", "rekomendasi") |
| **TickerLink / TagChip** | tautan konsisten ke detail | satu makro Jinja dipakai di semua tabel |
| Grafik | harga, arus bersih, sparkline | usul: SVG sisi server untuk sparkline/batang; satu pustaka kecil yang di-*vendor* di `static/` untuk candle (tanpa CDN) — **butuh persetujuan** |
| Rute baru | `/stock/{ticker}`, `/tag/{name}`, `/panduan`, `/cari?q=` | pencarian ticker + istilah |
| Tes | tiap judul kolom/tag/kuadran punya entri glosarium (tes gagal bila ada istilah tanpa penjelasan); smoke-test semua rute; uji templat kalimat | menghentikan jargon baru menyelinap kembali |

## 5. Perbaikan kejujuran yang ikut

- Strip kesehatan data: ganti pembanding "normal ≈ 262" dengan ambang tetap (broker ≥150 saham = cukup; selaras syarat G3), agar kuning berarti ada masalah sungguhan.
- Setiap tabel peringkat membawa CoverageNote ("dari 834 saham dengan data"), seperti sekarang di Ranking, sebagai komponen baku.

## 6. Tahapan (tiap tahap berakhir dengan tinjauan Anda di browser)

| Tahap | Isi | Hasil yang bisa Anda lihat | Perkiraan (tebakan kasar) |
|---|---|---|---|
| **A. Bahasa** | glosarium + TermHint + halaman Panduan; ganti semua nama kolom/label; ambang strip data | Semua istilah di layar sekarang bisa diklik-jelaskan | ±1 sesi |
| **B. Klik-tembus** | `/stock/{ticker}` (tanpa grafik dulu) + TickerLink/TagChip di semua tabel + pencarian | Klik saham apa pun → halaman berisi | ±1–2 sesi |
| **C. Kalimat** | narrative.py + "Hari ini" + kolom "Mengapa muncul?" + ringkasan di Detail saham | Layar menjelaskan dirinya dengan kalimat | ±1–2 sesi |
| **D. Pola & Rapor** | `/tag/{name}` + Rapor ringkas (tabel lama dilipat) | Pertanyaan "apakah pola ini terbukti?" terjawab | ±1–2 sesi |
| **E. Grafik** | candle harga + batang arus + garis waktu pola | Detail saham lengkap | ±1–2 sesi (+ keputusan pustaka) |
| **F. Poles** | HP (kartu), tema terang/gelap manual, aksesibilitas (label, `scope`, kontras), tes screenshot 3 lebar | Nyaman di HP dan layar kecil | ±1 sesi |

Urutan sengaja **A → B → C**: bahasa dulu (menjawab "tidak mengerti"), klik-tembus kedua (menjawab "tidak bisa diklik"), kalimat ketiga.
Data tidak perlu diubah; hampir semua layar membaca tabel yang sudah ada (`price_bars`, `broker_flows`, `broker_levels`, `ownership_monthly`, `signal_log`).

## 7. Yang tidak dilakukan

- Tidak ada LLM/AI untuk menulis penjelasan; semua kalimat dari templat dan angka.
- Tidak ada skor tunggal, tidak ada ranking "terbaik", tidak ada kata perintah beli/jual.
- Belum membuka dasbor ke internet (I1–I3 di checklist tetap menunggu gate forward-test); tetap `127.0.0.1`.
- Tidak meniru fitur yang datanya tidak ada (mis. inventory broker se-pasar); saham tanpa data broker tampil jujur "belum tersedia".

## 8. Keputusan yang saya butuhkan

1. **Pembaca sasaran:** saya asumsikan *Anda sendiri, paham dasar saham tetapi bukan istilah bandarmology* (kalimat sederhana, istilah dijelaskan sekali di tempatnya). Betul, atau lebih pemula lagi?
2. **Bahasa:** Indonesia penuh untuk semua label, dengan istilah asli dalam kurung/TermHint. Setuju? (menutup J1)
3. **Grafik:** boleh memakai satu pustaka grafik kecil yang disimpan lokal (tanpa CDN)? Alternatifnya SVG buatan sendiri (lebih lambat dikerjakan, tanpa dependensi).
4. **Mulai:** Tahap A dan B dulu, lalu Anda tinjau sebelum C?

## 9. Tambahan (3 Nov 2026): pembelajaran NeoBDM dan "Rencana risiko"

**Arahan pemilik:** VG harus menjadi alat standar industri untuk *memilih saham dengan mengikuti analisa transaksi*, termasuk masukan angka stop loss dan target profit.

**Yang terlihat di NeoBDM (peninjauan tata letak lewat sesi login pemilik, hanya baca; belum ada kueri data yang dijalankan):**
- Ada kolom pencarian ticker di bilah atas *setiap* halaman; hampir semua layar berpusat pada satu ticker.
- Screener dapat dibangun sendiri (preset + kolom + filter + ekspor) di atas universe (mis. COMPOSITE), dengan cap waktu "Update".
- Dasbor kepemilikan bulanan berisi daftar bernama dalam bahasa biasa (mis. "Scripless Bertambah", "Lokal Individu Berkurang", "Potensi Buyback Institusi").
- Dasbor seasonality (peluang tinggi / ekspektasi tinggi per bulan) dan Rotation Chart dengan filter "Liquid Only / Hide Weak / Hide Abnormal".
- **Inventory Chart:** candle harga + garis arus bersih kumulatif untuk 5 broker net beli dan 5 net jual teratas; jendela 2W–1Y; nilai atau lot; filter jenis investor.
- Broker Stalker (pilih broker, jendela 2–60 hari, hanya asing) dan Broker Summary (ticker, net/gross, rentang tanggal, semua/asing).
- **Lot Sizing:** sampai 4 harga masuk, harga SL *milik pengguna*, risiko per transaksi 0,5–5% modal, fee. NeoBDM **tidak** memberi angka stop loss/target; ia menghitung ukuran posisi dari SL yang pengguna tentukan sendiri.
- Pendidikan adalah bagian produk: Panduan Member Baru, Seri untuk Pemula, FAQ, video. Ini sejalan dengan keluhan "tidak mengerti" pada VG.

**Yang bisa VG lakukan lebih baik (tanpa menyalin):** kejujuran rekam jejak, penjelasan di tempat, batas pasti sisi broker, dan rencana risiko yang diuji.

**Rencana risiko (mekanis, bukan prediksi) — halaman Detail saham:**
- Stop loss = harga acuan − k × ATR14 (usul awal k = 2), dibulatkan ke tick IDX; jarak risiko juga ditampilkan dalam %.
- Target = kelipatan risiko (1R, 2R, 3R) dari harga acuan; batas ARA/ARB dan fraksi harga IDX diperhitungkan.
- Ukuran posisi (lot) dari risiko % modal, biaya (±0,4% pulang-pergi per preset) dan jarak stop.
- **Wajib diuji dulu:** aturan stop/target ini di-backtest pada `signal_log` + `price_bars` per pola (porsi kena stop, kena target, ekspektasi per transaksi setelah biaya). Angka itu tampil di samping levelnya.
- **Label tetap jujur:** "level risiko mekanis berbasis volatilitas, bukan prediksi harga dan bukan jaminan". Karena belum ada pola yang melewati biaya ±170bp, level ini alat manajemen risiko, bukan bukti bahwa pola menguntungkan. Tanpa kata perintah beli/jual.

## 10. Progress (3 Nov 2026)

- **Language decision:** the user asked for **English** for the interface and for replies. All dashboard labels, the glossary, and the page text are now English; the database values and pattern codes (for example `AKUM-ASING`) are unchanged.
- **Tahap A (language) done in the worktree:** `web/glossary.py` (46 terms + 26 patterns, each with a plain meaning and the exact rule), explain-in-place cards (click or Enter on any dotted word), `/guide` page, plain pattern names everywhere, broker freshness threshold fixed (≥150 stocks is healthy).
- **Tahap B (click-through) done in the worktree:** `/stock/{ticker}` (plain-language summary, certain bounds, patterns with their forward-test verdict, mechanical risk plan with position size, last 10 sessions, biggest brokers, KSEI ownership, corporate actions), clickable tickers everywhere, global search (`/search`).
- **Risk plan:** `risk.py` (IDX tick sizes, stop = reference − 2×ATR rounded down, 1R/2R/3R targets, lots from capital × risk%). **Not yet done:** the backtest that measures how these stop/target rules perform per pattern (needed before the numbers can be called evidence).
- **Not in this change:** charts (Tahap E), pattern detail pages and the simplified track record (Tahap D), mobile polish (Tahap F). Dev preview ran on :8788; the live dashboard on :8787 still runs the old code until deployed.
