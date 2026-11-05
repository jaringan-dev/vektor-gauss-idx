# Analisis dan Checklist UI/UX — Vektor Gauss

**Baseline audit:** 2026-10-31 · dashboard lokal `vg serve` (main `db37921`) · 3 layar: `/`, `/screener`, `/ranking`.
Dibaca bersama `RANCANGAN-UI-UX-v2.md` (rancangan), `INVENTARIS-DATA-DB.md` (data yang tersedia), dan
`RENCANA-UI-UX.md` (prinsip dan kepatuhan).

## Cara memantau pekerjaan
- Centang `[x]` hanya bila **kriteria selesai** terpenuhi dan sudah diverifikasi (perintah/ukuran tertulis di tiap butir).
- Lihat progres kapan saja:
  ```bash
  python scripts/ui_checklist_progress.py            # ringkasan per kelompok
  python scripts/ui_checklist_progress.py --open     # hanya butir yang belum selesai
  python scripts/ui_checklist_progress.py --p P0     # hanya prioritas P0
  ```
- Format butir: `- [ ] **ID** (Prioritas·Usaha) Judul — selesai bila: …`. Usaha: S ≤ 0,5 hari · M ≈ 1–2 hari · L > 2 hari (perkiraan kasar).
- Prioritas: **P0** kejujuran data / performa / pemantauan · **P1** kualitas inti · **P2** kenyamanan · **P3** nanti · **G** menunggu gate forward-test (jangan dikerjakan dulu).
- Setiap perubahan status dicatat di bagian *Log perubahan* paling bawah.

---

## 1. Analisis mendalam (hasil audit 31 Okt 2026)

### 1.1 Metode
Pengukuran langsung pada server yang berjalan: waktu muat (curl, 3 kali ulang), ukuran halaman, audit DOM lewat browser
(kontras warna dihitung dari warna terkomputasi, label, heading, target sentuh), uji keyboard (tombol Tab sungguhan),
mode terang dan gelap, dan lebar HP 375 px. Bukan penilaian visual semata.

### 1.2 Yang sudah baik (terukur)
| Aspek | Hasil |
|---|---|
| Kontras teks (WCAG AA ≥ 4,5:1) | Terang: terendah 4,52:1 (footer), catatan 4,83:1, angka positif 5,02:1, negatif 6,47:1. Gelap: terendah 6,41:1 |
| Fokus keyboard | Terlihat (outline bawaan browser) |
| Target sentuh | 0 elemen di bawah 24×24 px |
| Struktur | `lang="id"`, meta viewport, 1×`h1`, landmark `nav` dan `main` ada |
| Meluap di HP (375 px) | `/` dan `/screener`: halaman tidak meluap horizontal (tabel dibungkus panel yang bisa digulir) |
| Kejujuran | Gate tiga status (LULUS/GAGAL/PROBATION), kegagalan tampil menonjol, disclaimer di tiap layar, tidak ada kata perintah beli/jual |
| Ketahanan | DB mati → panel galat, bukan halaman 500 |

### 1.3 Temuan (diurutkan menurut dampak)

| ID | Tingkat | Temuan | Bukti | Dampak | Butir perbaikan |
|---|---|---|---|---|---|
| F-01 | **Tinggi** | Halaman `/` lambat: **4,4–5,3 dtk** setiap muat | 3 kali ukur; `eval_tags()` seluruh riwayat = 2,9 dtk dan dijalankan ulang tiap muat, padahal hasilnya hanya berubah setelah compute/outcomes | Alat terasa macet; menghambat kebiasaan cek harian | B1 |
| F-02 | **Tinggi** | Catatan cakupan di `/ranking` memuat angka statis yang **salah**: "~ribuan saham" (sebenarnya ~834) dan "~puluhan saham" (sekarang ~190) | `ranking.html` baris 21–22 | Melanggar prinsip kejujuran: teks bertentangan dengan angka live di panel | E1 |
| F-03 | **Tinggi** | Tidak ada layar kesehatan data. Celah harga 14/15/25 Sep (5 baris dari 962) dan turunnya cakupan broker 16–30 Okt tidak terlihat sampai ditemukan kebetulan | Ditemukan manual saat kalibrasi; tak ada indikator kesegaran di UI | Keputusan bisa diambil dari data bolong tanpa peringatan | A2, F1, F2 |
| F-04 | Sedang | Kolom kuadran hanya **emoji dengan tooltip** (623 sel), tanpa teks | Audit DOM: 623 `td[title]` | Tidak terbaca pembaca layar; melanggar prinsip "makna tidak hanya lewat simbol" | C4 |
| F-05 | Sedang | Aksesibilitas formulir/tabel: 1 input tanpa label, 11 `<th>` tanpa `scope`, 1 tabel tanpa `caption`, tidak ada skip-link | Audit DOM | WCAG 1.3.1 / 4.1.2 / 2.4.1 | C1, C2, C3 |
| F-06 | Sedang | `/screener` mengirim **320 KB / 623 baris** sekaligus | curl: 319.690 B | Berat di koneksi lambat; sulit dipindai | B2 |
| F-07 | Sedang | Di HP, tabel screener selebar **1.618 px** dalam panel 341 px: hanya ~2 kolom terlihat, kolom **tag** tersembunyi, kolom saham tidak lengket saat digulir | Ukur DOM + tangkapan layar 375 px | Tidak bisa dipakai dari HP (operator desktop-first, tapi wajib untuk publik) | D1 |
| F-08 | Sedang | Tidak ada drill-down: kode saham dan nama tag tidak bisa diklik | Tinjau template | Alur berhenti di daftar; tidak ada jalan ke detail | F10, F3, F4 |
| F-09 | Sedang | Navigasi satu baris tab tidak skalabel (3 layar sekarang, rencana 8+) dan tak memuat penanda kesegaran | Tinjau `base.html` | Sesak saat layar bertambah | A1, A2 |
| F-10 | Sedang | Server dashboard **mati diam-diam** saat WSL restart (mati dua minggu tanpa terdeteksi) | Terjadi 31 Okt | Operator mengira UI rusak | A6 |
| F-11 | Rendah | Tracker tanpa tren; n kecil tidak diberi label khusus (FLOAT-ASING-PADAT n=34 tampak seperti hasil biasa) | Tinjau `forward_test.html` | Berisiko salah baca tag ber-n kecil sebagai terbukti | E2, F5 |
| F-12 | Rendah | Tema hanya mengikuti OS; tak ada tombol manual | Tinjau `base.html` | Kenyamanan | A3 |
| F-13 | Rendah | Istilah campur (span, edge, hit, uHit, konsist.) tanpa glosarium | Tinjau template | Beban kognitif | A5 |
| F-14 | Rendah | Tidak ada tanggal hitung terakhir / lag matang hasil di tracker (hasil terbaru tertinggal ±10 hari bursa) | `market_breadth` berhenti 16 Okt | Pengguna mengira data macet | E3 |

---

## 2. Checklist

### A. Fondasi dan navigasi
- [x] **A1** (P0·M) Shell baru: sidebar 4 grup, hamburger di bawah 860 px — selesai bila: semua rute ada di sidebar, bisa dipakai penuh dengan keyboard, tidak meluap di 375 px. ✔ 31 Okt (pratinjau, data asli): 1280 px → sidebar 220 px, strip menempel, urutan Tab *Lewati ke konten → Forward-test → Saham hari ini → Top/Ranking → konten*; 375 px → 3 halaman tanpa meluap, klik "Menu" membuka (`aria-expanded` true), Escape menutup dan mengembalikan fokus ke tombol. Tes pengait memastikan setiap rute HTML punya tautan sidebar. **Catatan:** baru **2 grup** (Pola, Saham), bukan 4 — grup Beranda/Data baru muncul bersama layarnya (F1, F2). Tautan di dalam menu HP belum diuji dengan keyboard (hanya klik + Escape).
- [x] **A2** (P0·S) FreshnessStrip global (EOD · harga · broker · KSEI), berubah kuning/merah bila basi atau parsial — selesai bila: tampil di semua layar; uji unit: hari dengan < 500 harga → merah. ✔ 31 Okt: tampil di 3 layar (nilai nyata: EOD 30 Okt ✓ · Harga 963 ✓ · Broker 196 ✓ · KSEI Sep 2026 ✓); 17 tes aturan (termasuk 5 dan 480 dari 962 → merah) + tes pengait: harga bolong membuat strip merah di semua halaman, kegagalan query tampil sebagai strip merah. Kontras keadaan bermasalah terukur: terang 6,80/6,37, gelap 5,80/7,86 (:1). **Keputusan dan batas:** broker dibatasi maksimal kuning (sumber sekunder, cakupannya wajar bervariasi) tetapi teksnya tetap menyebut "bolong"; ambang (parsial < 90%, bolong < 50% dari median **60** hari, KSEI 45/60 hari) adalah tebakan awal dan **belum disetel**; hari libur bisa memicu satu peringatan kuning palsu pada EOD. *Koreksi 31 Okt:* jendela acuan semula 20 hari, dan tangkapan layar menunjukkan penurunan 12 hari berubah menjadi "normal" sehingga peringatan lenyap diam-diam (terbukti: jendela 20 → ok, jendela 60 → warn); kini 60 hari.
- [x] **A3** (P1·S) Token warna semantik terpusat + tombol tema manual yang tersimpan — selesai bila: tema bertahan setelah muat ulang; tidak ada warna hard-coded di template. ✔ 5 Nov (rekonsiliasi): tombol Auto/Light/Dark menyimpan `vg-theme` di localStorage dengan skrip sebelum cat (`8a6d5b5`); `grep` warna hex di template di luar baris variabel `--x:` = kosong; palet gelap ditulis dua kali di `base.html` dan sebuah tes mengunci keduanya identik. Tombol ada di 12 dari 12 halaman yang diukur.
- [ ] **A4** (P1·S) Komponen baku sebagai Jinja macro (`_components.html`): StatusBadge, CoverageNote, EdgeCell, TagChip, TickerLink — selesai bila: ≥ 3 layar memakainya, tak ada markup badge ganda. **5 Nov, TETAP TERBUKA:** macro sudah ada tetapi bernama `_macros.html` (`term`, `th`, `ticker`, `tag_chip(s)`, `quad`, `chart`, `db_error`, `disclaimer`, `verdict`), dipakai 13 template; yang belum: `StatusBadge`/`CoverageNote`/`EdgeCell`, dan badge "small n" masih ditulis tangan di `forward_test`, `pattern`, `stock` (+ badge di `coverage_missing`, `stock`).
- [x] **A5** (P2·S) Glosarium istilah (span, edge, hit, uHit, CR3, NFF) + keputusan bahasa (J1) — selesai bila: tooltip atau halaman `/glosarium` tersedia. ✔ 3 Nov (dicentang 5 Nov): `web/glossary.py` (46 istilah + 26 tag, tes mengunci setiap tag mesin dan istilah template), halaman `/guide` (bukan `/glosarium`), kartu penjelasan di tempat. Bahasa: Inggris untuk UI (J1).
- [ ] **A6** (P1·S) Dashboard hidup otomatis setelah WSL/PC restart — selesai bila: restart WSL → `:8787` menjawab 200 tanpa intervensi. **5 Nov, TERBUKA:** tidak ada tugas terjadwal untuk `vg serve` (hanya EOD, DeepBackfill, Status); dashboard masih dijalankan manual dengan `setsid` dan mati bila VM WSL berhenti. `scripts/serve.sh` sudah ada sebagai pelari foreground, tinggal didaftarkan.
- [x] **A7** (P0·M) Tiga layar dasar berjalan: Forward-test, Saham hari ini, Top/Ranking — terverifikasi 31 Okt (semua 200, data asli).

### B. Performa
- [x] **B1** (P0·S) Cache hasil `eval_tags()` in-sample (TTL, diinvalidasi saat compute/outcomes) — selesai bila: `/` < 1 dtk pada 3 pengukuran berturut-turut (sekarang 4,4–5,3 dtk) **dan** angka tetap benar setelah `vg backfill-outcomes`. ✔ 31 Okt: 0,59 / 0,31 / 0,31 dtk (kode lama 2,36–2,68 dtk pada hari yang sama). Cache 30 menit + dipanaskan saat server start + token versi dari `market_breadth`; halaman menampilkan waktu hitung dan `?fresh=1`. **Batas yang perlu diketahui:** muat pertama setelah idle > 30 menit tetap ~2,7 dtk. Invalidasi oleh `backfill-outcomes` teruji lewat tes unit (token versi), **belum** diuji end-to-end dengan menjalankan `backfill-outcomes` sungguhan.
- [ ] **B2** (P1·M) Screener: tampilan awal terbatas (mis. 100 teratas) + "muat semua", atau paginasi — selesai bila: HTML awal < 100 KB (sekarang 320 KB) dan pencarian tetap menjangkau seluruh baris. **5 Nov, TERBUKA tipis:** masalah aslinya selesai (50 baris per halaman, urut/filter di server, 320 → 115 KB), tetapi HTML awal terukur 114,9 KB, masih di atas batas 100 KB.
- [ ] **B3** (P2·S) Skrip uji performa rute dijalankan setelah tiap perubahan UI — selesai bila: `scripts/ui_perf_check.sh` mencetak waktu tiap rute dan gagal bila > 1,5 dtk.

### C. Aksesibilitas (WCAG 2.2 AA)
- [x] **C1** (P1·S) Label untuk input pencarian — selesai bila: audit DOM "input tanpa label" = 0. ✔ 5 Nov: diukur pada HTML live 12 halaman, 0 input tanpa nama (3 input di halaman saham dibungkus `<label>`; `q-global` berlabel).
- [x] **C2** (P1·S) `scope="col"` pada semua `<th>` dan `<caption>` (tersembunyi secara visual) pada tiap tabel — selesai bila: audit "th tanpa scope" = 0 dan "tabel tanpa caption" = 0. ✔ 5 Nov: diukur pada HTML live 12 halaman (/, /track-record, /screener, /ranking, /coverage, /health, /research, /risk-test, /guide, /search, /stock/BBCA, /pattern/FOREIGN-INST): 245 `<th>`, 0 tanpa `scope`; 31 tabel, 0 tanpa nama. **Catatan:** nama tabel kini lewat `aria-label` pada sebagian tabel, bukan `<caption>` seperti kriteria awal; hasilnya sama untuk pembaca layar. (Koreksi: angka "21 `<th>` tanpa scope" yang saya sebut sebelumnya berasal dari `grep` teks template, yang tidak melihat `scope` yang diisi macro `th`; HTML hasil render bersih.)
- [x] **C3** (P1·S) Skip-link "Lewati ke konten" — selesai bila: Tab pertama memfokuskan skip-link dan berfungsi. ✔ 31 Okt: Tab pertama memfokuskan skip-link (terlihat); Enter → `#main`, fokus mendarat di `main`; Tab berikutnya langsung ke kotak cari, sidebar terlewati.
- [x] **C4** (P0·S) Kuadran tampil dengan teks (AKUMULASI, DISTRIBUSI, …) di samping emoji — selesai bila: 0 sel kuadran yang hanya emoji. ✔ 31 Okt: `/screener` 623 dari 623 sel berteks, 0 `td title=`; `/ranking` juga berteks; kuadran kosong tampil "—". Belum diuji dengan pembaca layar (C5).
- [ ] **C5** (P2·M) Uji pembaca layar dasar (NVDA atau VoiceOver) pada tiga layar utama — selesai bila: catatan hasil uji ditulis di bagian Log.
- [x] **C6** (P2·S) Naikkan kontras footer terang (sekarang 4,52:1, hampir batas) — selesai bila: ≥ 5:1. ✔ 4 Nov (audit a11y `e49029f`), dihitung ulang 5 Nov: `--muted` terang #5b6472 → 5,59:1 di latar #f7f7f8 dan 5,98:1 di panel putih; gelap 7,55:1.
- [x] **C7** (P0·S) Kontras AA terang dan gelap — terukur 31 Okt: terang min 4,52:1; gelap min 6,41:1.
- [x] **C8** (P1·S) Fokus keyboard terlihat — terverifikasi dengan tombol Tab sungguhan.
- [x] **C9** (P2·S) Target sentuh ≥ 24 px — 0 pelanggaran.
- [x] **C10** (P1·S) Angka naik/turun tidak hanya warna: selalu bertanda +/− — terverifikasi di tiga layar.

### D. Responsif dan HP
- [x] **D1** (P2·M) Screener HP: kolom saham lengket, urutan kolom diprioritaskan (saham, harga, chg, tag) atau berubah jadi kartu di bawah 640 px — selesai bila: pada 375 px kolom **tag** terlihat tanpa menggulir ke kanan. ✔ 3 Nov (`9adbf99`, dicentang 5 Nov): screener dan ranking menjadi kartu di bawah 640 px (peran ARIA dijaga). **Batas:** dicek di emulasi browser 375 px, BELUM di HP sungguhan.
- [ ] **D2** (P2·S) Ranking: grid 2 kolom di desktop, 1 kolom di HP — selesai bila: tak ada gulir horizontal di 375 px. **5 Nov, TERBUKA:** sisi HP beres (kartu), tetapi panel Ranking masih satu kolom di desktop (3 `.panel` bertumpuk, tanpa grid).
- [x] **D3** (P2·S) Ukur `/ranking` di 375 px (belum diukur) — selesai bila: hasil dicatat di Log. ✔ 31 Okt: halaman tidak meluap (scrollWidth 375); 4 dari 5 panel menggulir di dalam panelnya sendiri — D2 (grid 1 kolom) masih terbuka.
- [ ] **D4** (P3·S) Uji tablet 768 px pada semua layar — selesai bila: tangkapan layar dan catatan.
- [x] **D5** (P1·S) `/` dan `/screener` tidak meluap horizontal di 375 px — terukur 31 Okt.

### E. Kejujuran data dan konten
- [x] **E1** (P0·S) Hapus angka cakupan statis di `ranking.html` ("~ribuan", "~puluhan") dan komentar usang di `engine.py` ("~64 only"); andalkan CoverageNote live — selesai bila: `grep -rn "puluhan\|ribuan saham\|~64" src/vg` kosong. ✔ 31 Okt: teks diganti; cakupan live di panel kini 830 / 830 / 280 / 196 (teks lama keliru pada keduanya). Dijaga tes regresi `test_web_templates.py`.
- [x] **E2** (P0·S) Label "n kecil" untuk tag ber-n < 50 di tracker; urutan awal tidak menempatkan n kecil di atas — selesai bila: FLOAT-ASING-PADAT (n=34) berlabel dan tidak di baris teratas. ✔ 31 Okt: pada data forward asli 1 tag ber-n kecil (BROKER-ASING-DIST, n=12) berlabel dan paling bawah; n besar terurut menurun. Catatan: FLOAT-ASING-PADAT kini n=63 (≥ 50) sehingga **tidak** berlabel — sesuai aturan, bukan kelalaian.
- [ ] **E3** (P1·S) Tampilkan tanggal hitung terakhir dan lag matang hasil di tracker — selesai bila: terlihat di halaman dan menjelaskan mengapa edge terbaru tertinggal ±10 hari bursa. **5 Nov, TERBUKA separuh:** waktu hitung ("Calculated … server time", asal cache, `?fresh=1`) sudah tampil di `/track-record`; yang belum ada: tanggal sinyal matang terakhir dan kalimat yang menjelaskan lag ±10 hari.
- [x] **E4** (P0·S) Gate tiga status jujur (LULUS/GAGAL/PROBATION), kegagalan sama menonjol — `web/status.py`, 7 tes.
- [x] **E5** (P0·S) CoverageNote live di setiap panel peringkat ("dari N saham") — ada di `/ranking`.
- [x] **E6** (P0·S) Disclaimer di semua layar — ada di footer tiga template.
- [x] **E7** (P0·S) Tidak ada kata perintah beli/jual — grep 31 Okt: hanya deskripsi data.

### F. Layar baru
- [x] **F1** (P0·M) **Cakupan dan celah**: heatmap hari × sumber (harga, indeks, broker, sinyal) berkode jumlah relatif terhadap normal — selesai bila: 14/15/25 Sep tampil merah pada data uji tanpa perbaikan; klik sel menampilkan saham yang hilang. ✔ 31 Okt, `/coverage` + `/coverage/missing`: **data uji = insiden tersuntik pada schema `scratch` yang dibaca lewat SQL asli** (produksi tidak disentuh, schema sudah dihapus): Harga/Indeks/Sinyal merah tepat pada 14/15/25 Sep (3 sel masing-masing), Broker merah 11 hari (16–30 Okt); klik Harga 15 Sep → "5 saham tersedia (1%), acuan 16 Sep, hilang 958 dari 963" + 3 perintah perbaikan. Aksesibel (caption, `scope`, 0 sel bermasalah tanpa angka, kontras 6,49/6,80:1). Setiap perintah perbaikan di-parse oleh CLI sungguhan di tes. **Pada data produksi nyata** halaman menemukan celah yang belum pernah terlihat (indeks bolong pada 14/15/25 Sep — lihat G10). **Batas:** hari acuan dibuat "normal dan tidak anomali besar" (≤ 1,5× normal) setelah klik Broker 30 Okt sempat menunjuk hari 952 saham; hari kelebihan cakupan sendiri masih tampil ✓ (F12).
- [ ] **F2** (P1·L) **Beranda Kesehatan**: kartu status + daftar "perlu perhatian" — selesai bila: kartu EOD, broker, KSEI, disclosure aktif; kartu cron dan Telegram menunggu G1. **5 Nov, TERBUKA sebagian:** `/health` (dari `aabd975`) sudah ada — tabel pemeriksaan, kemajuan K7, run terakhir tiap job, jadwal notifikasi — tetapi bukan "Beranda" (beranda sekarang `/` = Today), belum berbentuk kartu EOD/broker/KSEI/disclosure, dan belum ada daftar "perlu perhatian".
- [ ] **F3** (P1·L) **Detail saham**: candle, inventory broker kumulatif (saham likuid), arus asing, KSEI, riwayat tag — selesai bila: BBCA tampil lengkap; saham tanpa data broker menampilkan "data broker belum tersedia", bukan grafik kosong. **5 Nov, TERBUKA sebagian:** `/stock/{ticker}` sudah memuat candle 90 sesi + stop/R, arus asing, rentang broker pasti, net flow per kelompok, tag hari ini + rekornya, rencana risiko, KSEI bulanan, corporate action; saham tanpa broker menampilkan "Broker data is not available…". **Sengaja belum ada: inventory broker kumulatif per broker** (alasan: `broker_flows` hanya 20 pembeli bersih teratas; keputusan saya, belum Anda setujui). Belum ada: riwayat tag per saham dari waktu ke waktu.
- [ ] **F4** (P1·M) **Detail tag**: edge kumulatif dari waktu ke waktu + tabel per bulan + sinyal terbaru — selesai bila: tag di tracker dan di Detail saham menaut ke sini. **5 Nov, TERBUKA sebagian:** `/pattern/{tag}` (`98e8004`) sudah ada dan ditautkan dari tracker, halaman saham, guide, dan pencarian; isinya aturan, rekor forward vs histori, "Is the edge real?", per bulan, stop/target, frekuensi sinyal, sinyal hari ini, tag terkait. **Yang belum: grafik edge KUMULATIF dari waktu ke waktu** (yang ada grafik frekuensi sinyal), yang butuh G2.
- [x] **F5** (P2·S) Sparkline tren edge di tracker — selesai bila: tiap baris tag memuat tren; butuh query per-bulan (G2). ✔ 5 Nov (BELUM di-commit/deploy): kolom "5d trend" di kedua tabel `/track-record`. `engine.tag_monthly_edge(since)` (definisi edge sama dengan `eval_tags`, per bulan), `charts.sparkline` + `charts.spark_scale`, istilah `edge_trend` di glosarium. Aturan kejujuran: skala vertikal SAMA untuk semua baris dalam satu tabel (persentil ke-90 dari |edge| bulan ber-n ≥ 30, bukan maksimum — satu tag langka dengan bulan +9,5% sempat meratakan semua garis); bulan ber-n < 30 = titik KOSONG yang tidak disambung garis; bulan tanpa data = celah, bukan interpolasi; nilai di luar skala digambar di tepi kotak dengan angka aslinya di teks/tooltip; < 3 bulan = "—". Pada data asli: tabel histori penuh 26 dari 26 tag bergaris; **tabel forward semua "—"** karena baru 2 bulan matang (Sep, Okt; Nov belum ada hasil 5 hari) — otomatis terisi mulai ±awal Des. Diverifikasi pada data produksi lewat instans pratinjau (kode worktree): desktop 1280 + HP 375 (tak meluap, 0 id ganda, 26 dari 26 svg bernama); 8 tes baru, suite penuh 620 lulus di WSL. Biaya: +1,0 dtk pada hitung dingin (di-cache 30 menit, dipanaskan saat start). Belum dicek dengan pembaca layar (C5).
- [ ] **F6** (P2·M) **Rotasi sektor** (11 indeks sektoral vs COMPOSITE) dan filter sektor di screener — data lengkap (`index_bars`, `ticker_profile_history`).
- [ ] **F7** (P2·M) **Kalender dividen** dan dividend yield dari `corp_actions` — data lengkap.
- [ ] **F8** (P3·M) **Insider per saham** dari `disclosure_holdings` (242 saham ber-arah).
- [ ] **F12** (P3·S) Heatmap: tandai hari **kelebihan cakupan** (> 1,5× normal, mis. broker 952 pada 15 Okt) sebagai informasi, bukan hanya ✓ — saat ini tampil normal dan hanya dikecualikan sebagai hari acuan.
- [ ] **F9** (P3·M) **Broker Stalker** (satu broker → saham yang dikumpulkan) — butuh F3 selesai dan backfill broker lebih dalam (G4).
- [x] **F10** (P2·S) Ticker dan tag bisa diklik di semua tabel (drill-down konsisten) — selesai bila: 0 kode saham/tag yang bukan tautan. ✔ 5 Nov: diukur pada HTML live — di `/`, `/track-record`, `/ranking`, `/research`, `/risk-test`, `/stock/BBCA`, `/health` tidak ada kode tag di luar tautan; `/pattern/FOREIGN-INST` hanya menampilkan kodenya sendiri sebagai judul; 50 dari 50 baris `/screener` berkolom saham bertaut; `grep` template: ticker dan tag selalu lewat macro `ticker()`/`tag_chip()` atau `<a>` langsung (kode broker bukan ticker, memang tak bertaut).
- [ ] **F11** (P3·S) Layar Riwayat koleksi dan Log mentah — butuh G1.

### G. Backend dan data pendukung
- [ ] **G1** (P1·M) Tabel `collector_runs` dan `notification_log` + pencatatan di collector — selesai bila: tiap run EOD menulis baris; Beranda memakainya.
- [ ] **G2** (P1·M) Query agregasi: cakupan per hari × sumber (untuk F1) dan edge per bulan per tag (untuk F4/F5) — selesai bila: dua fungsi di `engine.py` dengan tes. **5 Nov, separuh:** `engine.tag_monthly_edge` ada (dipakai F5; diuji lewat sparkline, SQL-nya diverifikasi pada DB produksi, belum punya tes SQL sendiri); fungsi cakupan per hari × sumber hidup di `web/coverage.py` (diuji di `test_web_coverage.py`), bukan di `engine.py`.
- [ ] **G3** (P0·S) Koleksi broker harian **penuh** (universe likuid), bukan batas radar 60 — selesai bila: 5 hari EOD berturut-turut menghasilkan ≥ 150 saham/hari tanpa backfill manual. **Kode selesai (G8), bukti produksi BELUM ada:** EOD malam ini masih memakai kode lama (`--radar`) sampai perubahan ini di-merge ke klon WSL; 5 hari berjalan baru terhitung sesudah itu. **5 Nov, progres terukur dari DB:** nama broker per hari — 26–29 Okt 226–238 (pra-`--full`), 30 Okt 196, **2 Nov 186, 3 Nov 171, 4 Nov 177**. Kode `--full` berjalan sejak deploy 2 Nov, tetapi 3 Nov diisi ulang MANUAL setelah insiden tabel basi, jadi tidak boleh dihitung "tanpa backfill manual". Rantai otomatis dihitung dari 4 Nov; jadi paling cepat terbukti setelah EOD 10 Nov (4, 5, 6, 9, 10 Nov), dengan syarat tak ada hari yang dibantu tangan.
- [x] **G8** (P0·M) Kode koleksi broker penuh: `vg collect-broker-summary --full [--plan] [--date D | --backfill A B]` (universe = watchlist + holdings + semua saham likuid > Rp5 M + ekstra radar; idempoten; jeda feed `VG_FEED_DELAY_SECONDS` 1 dtk terpisah dari jeda IDX; berhenti setelah 10 galat beruntun), `vg collect-price-history --all`, `eod.sh` memanggil `--full`. ✔ 31 Okt: 34 tes baru (`tests/test_broker_universe.py`); pass nyata pada DB produksi berjalan benar — `--plan` untuk 2026-09-14 menunjukkan 69 nama yang belum ada; pass 16 Okt dan 27–29 Okt **mengambil 167 pasangan (hari, saham) yang memang hilang**. Tes penjaga: `eod.sh` tidak boleh kembali ke `--radar`.
- [ ] **G11** (P1·S) **Backup mingguan otomatis GAGAL diam-diam.** `scripts/backup.sh` (dipanggil `eod.sh` tiap Minggu dengan `|| true`) memakai repo bawaan `jaringan-bot/vektor-gauss` yang sudah mati; EOD Minggu 1 Nov mencatat `HTTP 404 … gh release create failed` (dump 169 MB sempat dibuat di `/tmp`). Temuan 1 Nov saat memantau G9. Perbaikan: ganti default `REPO` (baris 27) dan komentar baris 16 menjadi `jaringan-dev/vektor-gauss-idx`, atau set `VG_BACKUP_REPO` di `.env` WSL — selesai bila: Minggu berikutnya menghasilkan release baru yang bisa dilihat. **2 Nov: kode DIPERBAIKI** (default `REPO` di `backup.sh` dan `VG_BACKUP_REPO` di `.env.example` → `jaringan-dev/vektor-gauss-idx`; +3 tes `test_backup_script.py`; `gh` terverifikasi ADMIN di repo baru, repo lama tak ditemukan). **2 Nov 00:40: skrip dari klon dijalankan manual dan BERHASIL** — rilis `backup-2026-11-02` (dump 335 MB + artifacts 177 MB) terlihat di repo baru, jadi jalur unggah terbukti. **Sengaja belum dicentang:** perbaikan klon WSL sudah diperbarui (9fc8b38); butir ini baru selesai bila EOD Minggu 8 Nov menghasilkan release `backup-2026-11-08`. Backup manual `data-20261031` tetap ada tetapi mendahului semua perbaikan data 31 Okt–1 Nov.
- [x] **G9** (P1·M) ✔ 1 Nov: 6.554 dari 6.557 pasangan terisi (3 gagal sementara: STAA 14 Okt, BJTM 19 Okt, FUTR 20 Okt — error jaringan; ulangi `--backfill` bila ingin 100%); lalu `compute` 105 hari + outcomes 8.924 baris. **Hasil: edge tag broker MENYUSUT, tidak ada yang melewati ±170 bp** (BROKER-DOMISILI-KONFIRM likuid H5 +1,69% → +1,33% forward; DOM-RETAIL +0,35% → −0,21%). Proses sempat terbunuh restart VM 10:45; dilanjutkan di sesi tertahan. Isi ulang broker per "likuid pada HARI ITU" untuk Jun–Okt: `vg collect-broker-summary --full --backfill 2026-06-03 2026-10-30`, lalu `compute` + `backfill-outcomes`. Temuan 31 Okt: backfill kalibrasi saya memakai himpunan likuid **tetap** (hari terakhir), padahal himpunan likuid tiap hari berbeda (157–217 nama) — empat hari yang dicek kekurangan 22–70 nama (±15%). Perkiraan ±3.700 panggilan (~1,2 jam), muat kuota Popular. **Belum dikerjakan**; mengubah n dan angka edge tag broker.
- [x] **G10** (P0·S) Celah **indeks** 14/15/25 Sep (0 baris; IHSG hilang) ditemukan oleh heatmap F1 di data nyata dan ditambal: `collect-index --date` ×3 (45 baris Tier-0 per hari), lalu `compute` 14 Sep–30 Okt (35 hari) + `backfill-outcomes` (14.377 baris). ✔ NULL `rs_ihsg` turun dari ±830 baris per hari pada 6 hari (efek jendela 5 dan 20 hari) menjadi **6 dari 29.094** baris. **Dampak ke edge belum bisa dipisahkan dari efek kematangan outcome** (snapshot "sebelum" gagal tersimpan); n forward naik (mis. BROKER-DOMISILI-KONFIRM 1.434 → 2.521) karena lebih banyak baris matang, bukan karena perbaikan ini.
- [ ] **G4** (P2·M) Backfill broker lebih dalam (sumber punya sampai 2020; saat ini mulai 3 Jun 2026) — selesai bila: `broker_flows` ≥ 2 tahun untuk universe likuid. **5 Nov, SEDANG BERJALAN:** `vg broker-deep` (K7, 15 ribu panggilan per malam, terbaru ke terlama) sudah menarik `broker_flows` mundur dari 3 Jun 2026 ke **8 Des 2025** (±11 bulan dari target 2 tahun); tidak dicentang sampai ≥ 2 tahun.
- [ ] **G5** (P2·S) Tambal data basi: `margin_securities` (berhenti 24 Sep) dipasang ke EOD atau dihapus dari rencana — selesai bila: keputusan dicatat.
- [ ] **G6** (P3·S) Snapshot `ticker_profile_history` berkala (mingguan) — selesai bila: ≥ 4 snapshot beruntun.
- [x] **G7** (P0·M) Cakupan broker seragam Jun–30 Okt (~190/hari) dan celah harga 14/15/25 Sep tertambal — dikerjakan 31 Okt; DB di-backup ke GitHub Release `data-20261031`.

### H. Kualitas dan uji
- [ ] **H1** (P1·M) Tes untuk fungsi view baru (`_screener_view`, `_ranking_view`, fungsi cakupan) dengan data tiruan — selesai bila: `pytest` hijau, ≥ 1 tes per fungsi view.
- [x] **H2** (P1·S) Skrip audit aksesibilitas otomatis (JS di §1.1) disimpan di repo dan dijalankan sebelum merge — selesai bila: `scripts/ui_a11y_audit.js` ada dan hasil dicatat. ✔ 4 Nov (`e49029f`, dicentang 5 Nov): skrip ada sebagai `scripts/a11y_audit.js` (nama berbeda dari kriteria); hasil dicatat: 12 halaman, 0 temuan di tema gelap dan terang; dijaga juga oleh `tests/test_web_a11y.py` (nama, zoom/outline, 24 px, 16 pasangan palet). **Batas:** dijalankan lewat browser (tidak otomatis di CI); belum mencakup urutan fokus/baca dan pembaca layar.
- [ ] **H3** (P2·S) Tangkapan layar acuan (terang/gelap, desktop/HP) untuk pembanding perubahan — selesai bila: 3 layar × 2 tema × 2 lebar tersimpan.
- [x] **H6** (P1·S) Pytest **penuh** di WSL wajib hijau sebelum commit/push — selesai bila: langkah ini tertulis di prosedur rilis dan dijalankan pada commit berikutnya. ✔ 5 Nov: ditulis di `README.md` (di bawah "Run tests"); sejak 3 Nov setiap commit (`9c4964b`, `5166f4e`, `45f22f6`, `1002282`) didahului pytest penuh (600 → 605 → 612 lulus). Pelajaran 31 Okt: `db37921` memecahkan 2 tes (`fetch` selalu memanggil `_download(ticker, on_date=…)`) karena suite penuh tidak dijalankan sebelum push.
- [x] **H7** (P1·M) Tes untuk cache, aturan n-kecil, regresi template (E1/C4/E2), dan jalur tanggal `fetch` — 26 tes baru; suite penuh 143 lulus di WSL pada kode worktree (31 Okt).
- [x] **H4** (P1·S) Tes unit logika gate — 7 lulus (`tests/test_web_status.py`).
- [x] **H5** (P1·S) Verifikasi live tiap perubahan UI (status 200 + isi) — dilakukan pada tiap rilis 16 Okt.

### I. Gated — jangan dikerjakan sebelum gate forward-test (~4 Nov) lolos
- [ ] **I1** (G·L) Rapor Publik (komponen F4 tanpa panel operator).
- [ ] **I2** (G·L) Landing, daftar/masuk, watchlist pengguna, kalkulator posisi, RADAR berbayar.
- [x] **I3** (G·M) Autentikasi (Cloudflare Access atau basic-auth) sebelum dashboard dibuka ke luar `127.0.0.1`. ✔ 3 Nov (`9d80d78`, dicentang 5 Nov): basic-auth + kunci setelah percobaan gagal berulang + header keamanan; server menolak berjalan di alamat selain loopback tanpa kata sandi ≥ 12 karakter (`tests/test_web_auth.py`), `scripts/serve.sh`. Dikerjakan atas permintaan akses HP, bukan menunggu gate. **Batas:** sisi Windows/Tailscale adalah urusan Anda dan belum diuji.

### J. Keputusan terbuka (perlu jawaban Anda)
- [x] **J1** Bahasa antarmuka operator: istilah teknis tetap Inggris dengan kalimat Indonesia, atau penuh Indonesia? ✔ Dijawab 3 Nov: UI dalam bahasa Inggris (kode tag dan nilai DB tidak diubah); balasan chat dalam bahasa Indonesia sejak 4 Nov.
- [ ] **J2** Urutan mulai: A1–A2 + E1/B1/C4 (perbaikan cepat) dulu, atau langsung F3 Detail saham?
- [ ] **J3** Navigasi: sidebar kiri 4 grup, atau tetap tab atas?
- [ ] **J4** Hosting: tetap lokal, atau siapkan deploy privat (Cloudflare Access) agar bisa dibuka dari HP?

---

## 3. Urutan pengerjaan yang disarankan
1. **Perbaikan cepat berdampak tinggi (≈ 1 hari):** B1 (performa), E1 (teks salah), C4 (kuadran), E2 (n kecil), C1–C3 (aksesibilitas dasar).
2. **Pemantauan data (≈ 2–3 hari):** G3 → G2 → A1/A2 → F1 (heatmap celah).
3. **Layar inti (≈ 1–2 minggu):** F10 → F3 → F4 → G1 → F2.
4. **Polesan dan data tambahan:** D1–D3, F5–F7, H2–H3, G4.
5. **Gated:** I1–I3 setelah hasil forward-test.

## 4. Log perubahan
| Tanggal | Perubahan | Oleh |
|---|---|---|
| 2026-10-31 | Dokumen dibuat dari audit baseline; 13 dari 62 butir dicentang sesuai kondisi terukur (dihitung oleh `scripts/ui_checklist_progress.py`) | Claude |
| 2026-10-31 | F1 selesai (heatmap `/coverage` + detail `/coverage/missing`, terbukti pada insiden tersuntik di schema `scratch` lewat SQL asli), G8 selesai (koleksi broker penuh + CLI `--date/--backfill/--plan`, `collect-price-history --all`, `eod.sh --full`), G10 selesai (celah indeks nyata ditambal). G3 sengaja TIDAK dicentang (butuh 5 hari EOD nyata setelah deploy). Menambah G9 (isi ulang broker per likuid-hari-itu), F12. Mengoreksi jendela acuan strip 20 → 60 hari dan hari acuan heatmap (≤ 1,5× normal). **Mengubah data produksi:** +167 pasangan broker, +135 baris indeks, sinyal 14 Sep–30 Okt dihitung ulang, outcomes diperbarui. +88 tes (182 → 270 lulus di WSL). Belum di-commit | Claude |
| 2026-10-31 | A1, A2, C3, D3 selesai dan terverifikasi (pratinjau port 8788, data asli, desktop 1280 px + HP 375 px, keyboard sungguhan, kontras keadaan bermasalah). Menambah `web/freshness.py`, shell baru di `base.html`, `_render`/`_NAV`/`_strip_ctx` di `app.py`, +39 tes (suite 182 lulus di WSL). Tes pengait baru memastikan setiap rute HTML terdaftar di sidebar. Belum di-commit; server live 8787 masih kode lama | Claude |
| 2026-10-31 | B1, C4, E1, E2 selesai dan terverifikasi pada data asli lewat instans pratinjau (port 8788, kode worktree); H7 ditambah dan dicentang, H6 ditambah (terbuka). Menemukan dan memperbaiki regresi `fetch` dari `db37921` (2 tes). Belum di-commit; server live 8787 masih kode lama | Claude |
| 2026-11-01 | G9 selesai (6.554/6.557 pasangan; 3 error jaringan sementara). Sebelum/sesudah, forward sejak 4 Sep, edge H5 likuid: BROKER-DOMISILI-KONFIRM +1,69→+1,33%, BROKER-ASING-AKUM +0,72→+0,70%, FOREIGN-INST +1,16→+1,02%, DOM-RETAIL +0,35→−0,21%, BANDAR-CANDIDATE +2,16→+1,75% (n=206); tidak ada yang melewati biaya ±170 bp. Menambah G11 (backup mingguan gagal 404). Mengubah data produksi: broker_flows, signal_components, signal_log. Belum di-commit | Claude |
| 2026-11-02 | G11: default repo backup diperbaiki ke `jaringan-dev/vektor-gauss-idx` + 3 tes; menunggu deploy ke klon WSL dan bukti Minggu 8 Nov (belum dicentang). | Claude |
| 2026-11-05 | **Rekonsiliasi dengan kondisi nyata** (kode + HTML live 12 halaman + DB). Dicentang: A3, A5, C1, C2, C6, D1, F10, H2, H6, I3, J1 (11 butir; bukti di tiap butir). Ditandai TERBUKA dengan alasan terukur: A4 (macro bernama `_macros.html`, badge "small n" masih ganda), A6, B2 (114,9 KB vs 100 KB), D2, E3 (separuh), F2/F3/F4 (sebagian jadi, bagian yang belum disebut), G3 (progres 2 dari 5; 3 Nov manual), G4 (mulai 8 Des 2025). Catatan: layar yang dibuat di luar daftar ini (Today, `/risk-test`, `/research`, `/health`, `/guide`, `/search`) belum punya butir sendiri. Tidak ada kode aplikasi diubah; README +2 baris (H6) | Claude |
| 2026-11-05 | F5 selesai (sparkline tren edge bulanan di `/track-record`; tabel forward masih "—" sampai ≥ 3 bulan matang). Sebagian G2 ikut jadi (`engine.tag_monthly_edge`); G2 tetap terbuka karena fungsi cakupan ada di `web/coverage.py`, bukan `engine.py`, dan F4 butuh edge KUMULATIF. +8 tes (suite 620). Belum di-commit | Claude |
| 2026-11-02 | Deploy: main = 9fc8b38 (273 tes), klon WSL diperbarui, dashboard 8787 dimulai ulang. Backup manual `backup-2026-11-02` berhasil (jalur G11 terbukti; otomatis Minggu 8 Nov belum). | Claude |
