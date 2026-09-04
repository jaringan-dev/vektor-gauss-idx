# Prinsip Data Vektor Gauss — Sumber Resmi & Anti-Manipulasi

Prinsip yang mengikat semua fase. Pendamping [RENCANA-FASE-1.md](RENCANA-FASE-1.md), [INVENTARIS-SUMBER-DATA.md](INVENTARIS-SUMBER-DATA.md).

Dua ancaman berbeda yang keduanya harus ditangani:
1. **Provenance** — data harus dari sumber otoritatif, bukan feed tangan-kedua yang bisa keliru/diubah.
2. **Manipulasi pasar** — broker summary itu sendiri bisa "dicat" oleh bandar (wash sale, cross-broker, crossing pasar nego). Metodologi harus condong ke sinyal yang **sulit dipalsukan**.

---

## BAGIAN A — Provenance: tingkatan sumber data

| Tier | Definisi | Contoh | Boleh dipakai? |
|---|---|---|---|
| **Tier 0 — Otoritatif** | Langsung dari penerbit data | IDX (Stock Summary, Index Summary, disclosure/keterbukaan), KSEI (Komposisi Kepemilikan, CA), OJK filing | ✅ Selalu diutamakan |
| **Tier 1 — Redistributor berlisensi** | Kontrak resmi meneruskan data Tier 0 tanpa mengubah | RTI Infokom, IQ Plus, IDX-STI, Antara/IMQ, Bloomberg, Refinitiv | ✅ Setara Tier 0; wajib untuk broker summary komersial |
| **Tier 2 — Agregator komersial** | Perusahaan data yang mengolah/menyajikan, fidelitas tidak terjamin kontrak | Sectors.app (lebih kredibel), Index Alpha, GOAPI | ⚠️ Boleh, **wajib cross-validate ke Tier 0** |
| **Tier 3 — Scrape platform retail** | Ambil dari tampilan Stockbit/RTI Business/app sekuritas | — | ❌ **Tidak untuk produksi.** ToS + rapuh + tak bisa diverifikasi |

**Aturan:**
- Setiap baris data punya kolom `source` + `source_tier`.
- Data Tier 2 masuk **karantina** sampai lolos rekonsiliasi (Bagian C). Sinyal turunannya tetap di lapis "masa percobaan" (bobot 0) sampai tervalidasi silang.
- Simpan **raw artifact + SHA256** dari sumber, tiap hari. Jangan pernah percaya angka turunan yang tak bisa Anda hitung ulang dari raw.
- Kalau sumber Tier 2 dan Tier 0 memberi angka yang berbeda material untuk hal yang sama → percaya Tier 0, tandai hari itu `SOURCE_CONFLICT`.

**Untuk Vektor Gauss:**
- **Fase 1 = 100% Tier 0** (IDX Stock Summary + KSEI). Jalur paling tidak bisa dimanipulasi, foreign flow dihitung IDX sendiri, tidak ada tebak-tebakan kode broker.
- **Fase 1.5 / 2** (kalau tambah broker summary): **utamakan redistributor Tier 1**. Kalau anggaran memaksa Tier 2 (Index Alpha / Sectors) → berlakukan semua cek Bagian C, broker-level tetap lapis masa-percobaan sampai cocok dengan Tier 0.
- **Jangan pernah** scrape Stockbit/RTI untuk produksi.

---

## BAGIAN B — Manipulasi pasar: sinyal "bisa dicat" vs "sulit dipalsukan"

### Cara bandar mengecat broker summary EOD

| Teknik | Efek di broker summary | Kenyataan |
|---|---|---|
| **Wash sale / self-crossing** | 1 broker tampak net buy besar, frekuensi tinggi | Barang & uang muter di akun sendiri, posisi tak berubah |
| **Cross-broker** (transfer antar akun sendiri di 2 broker) | Broker B tampak "akumulator" | Barang pindah kantong kiri↔kanan entitas sama |
| **Crossing pasar negosiasi (NG / FoP)** | Volume besar, "akumulasi" | Tidak ada uang riil, tidak ada dampak harga |
| **Pecah order ke banyak broker retail** | Akumulasi institusi tersamar jadi "beli ritel" (atau sebaliknya) | 1 entitas, banyak pintu |

### Sinyal SULIT dipalsukan → kandidat lapis berbobot

| Sinyal | Kenapa sulit dicat | Sumber |
|---|---|---|
| **Jumlah pemegang tercatat (registered holders)** | Broker crossing dengan dirinya **tidak mengubah register**. Register melebar → distribusi riil ke ritel | KSEI / BAE (Tier 0) |
| **Komposisi kepemilikan KSEI** (lokal/asing/tipe investor) | Posisi ter-settle, tidak bisa dipalsu oleh churn intraday | KSEI bulanan (Tier 0) |
| **Disclosure pemegang >5% / insider** | Wajib lapor secara hukum (POJK 4/2024), nama jelas, saham & % sebelum/sesudah | IDX GetAnnouncement + form KSEI (Tier 0) — `vg collect-disclosure` |
| **Split D/F resmi IDX** (`ForeignBuy`/`ForeignSell` di Stock Summary) | Dihitung IDX dari domisili rekening, bukan dari tebakan kode broker | IDX Stock Summary (Tier 0) |
| **Partisipasi ritel versi IDX** (kalau tersedia) | Klasifikasi bursa sendiri | IDX |
| **Konsistensi net foreign flow multi-minggu** | Mengecat arah konsisten selama berminggu-minggu itu mahal | IDX (Tier 0) |
| **Dampak harga vs klaim flow** | "Akumulasi masif" tanpa harga bergerak = mencurigakan | turunan |

### Sinyal MUDAH dicat → tetap di lapis masa-percobaan / turunkan bobot / jadi flag

- Konsentrasi broker 1 hari (CR spike) — gampang dipentaskan
- Identitas "broker mana" saat itu broker retail-facing — bisa bandar menyamar
- Apa pun dari **pasar negosiasi** yang ditelan mentah
- "Sultan/Zombi" berbasis ukuran order — butuh data yang tak ada, dan mudah dimanipulasi

---

## BAGIAN C — Cek defensif yang WAJIB diimplementasi

1. **Pisahkan RG (reguler) dari NG (negosiasi) — selalu.**
   Hitung semua flow di **RG saja**. Simpan rasio `NonRegularValue / Value`; kalau tinggi → tag `CROSSING-TINGGI`, jangan percaya flow hari itu.

2. **Rekonsiliasi total ke angka resmi IDX.**
   Kalau pakai broker summary (Tier 1/2): `Σ semua broker buy_value` untuk 1 emiten harus ≈ IDX Stock Summary `Value` hari itu (toleransi pembulatan). Kalau tidak cocok → **tolak hari itu**, tag `RECON_FAIL`.

3. **Rekonsiliasi foreign.**
   `Σ broker(origin=foreign) net` harus ≈ IDX `ForeignBuy − ForeignSell` (dikonversi). Divergensi > ambang → flag kualitas data.

4. **Deteksi pola wash.**
   Net besar 1 broker **+** harga ~flat (|Δ| < X%) **+** frekuensi tinggi **+** `buy_avg ≈ sell_avg` di broker yang sama → tag `SUSPECT_WASH`, jangan jadikan sinyal akumulasi.

5. **Netting broker terafiliasi.**
   Pelihara tabel `broker_affiliation_groups` (broker milik sekuritas/grup yang sama, atau klaster bandar yang diketahui). **Net dulu antar-anggota grup** sebelum menghitung konsentrasi. (Ini "resep" yang perlu dikalibrasi manual — perlakukan sebagai hipotesis ter-versioned.)

6. **Ukuran tiket vs frekuensi.**
   Net volume sama tapi via sedikit tiket besar = positioning; via banyak tiket kecil = churn/ritel. Hitung `avg_ticket = value / frequency` per sisi.

7. **Validasi silang bulanan (T+).**
   Akumulasi net bulanan (dari flow) harus **searah** dengan Δ komposisi kepemilikan KSEI bulan itu. Divergensi persisten (flow bilang "asing akum", KSEI bilang "%asing turun") → flow-nya kemungkinan dicat → tag `KSEI-DIVERGEN`, turunkan keyakinan.

8. **Penyesuaian corporate action.**
   Split/bonus/HMETD memutus deret jumlah saham & harga. **Wajib** koreksi sebelum hitung flow/rolling — kalau tidak, matematikanya sampah (ini integritas data, bukan manipulasi, tapi sama fatalnya).

9. **Emiten disuspend / tidak diperdagangkan.**
   Baris volume 0 / all-zero → keluarkan dari analisa (jangan sampai menarik skor emiten lain).

---

## BAGIAN D — Konsekuensi desain

- Skema: tambah `source`, `source_tier` di semua tabel data; tambah `data_flags` (array: `RECON_FAIL`, `CROSSING-TINGGI`, `SUSPECT_WASH`, `KSEI-DIVERGEN`, `SOURCE_CONFLICT`) di `signal_components`.
- Tabel baru: `broker_affiliation_groups(group_id, broker_code, group_name, version, valid_from)`.
- Engine: jalankan Bagian C **sebelum** hitung sinyal. Baris dengan `RECON_FAIL` → tidak menghasilkan tag hari itu.
- Sistem 2-lapis (sudah ada di rencana): sinyal "sulit dipalsukan" (Bagian B) = kandidat lapis berbobot. Sinyal "bisa dicat" = tetap lapis masa-percobaan, atau jadi **flag konteks** bukan sinyal.
- Telegram digest: tampilkan flag di baris ticker (mis. `⚠ CROSSING-TINGGI`, `⚠ KSEI-DIVERGEN`) supaya Anda tahu kapan data hari itu tidak bisa dipercaya.
- Fase 2: kalau produk dijual, **jalur data harus bisa dijelaskan asal-usulnya** (Tier 0/1) — ini juga argumen legal (lihat [ANALISIS-VALIDASI.md](ANALISIS-VALIDASI.md) C6).

---

## Ringkas

> **Fase 1:** hanya IDX + KSEI resmi. Tidak ada broker summary → tidak ada permukaan manipulasi kode-broker sama sekali. Foreign flow dihitung IDX.
> **Kalau tambah broker summary nanti:** redistributor berlisensi > agregator komersial > (jangan) scrape. Semua broker-level lewat 9 cek Bagian C, tetap lapis masa-percobaan sampai cocok dengan angka resmi IDX + tren KSEI.
> **Metodologi selamanya condong ke sinyal yang bandar tidak bisa palsukan:** register pemegang, kepemilikan KSEI ter-settle, disclosure wajib, split D/F resmi IDX, konsistensi multi-minggu.
