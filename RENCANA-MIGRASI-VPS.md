# Rencana Migrasi — WSL2 (PC rumah) → VPS ISP Indonesia

**Status: jembatan disiapkan sekarang, eksekusi ditunda sampai Anda sewa VPS-nya.** Tidak
menunggu gate forward-test (ini infra, bukan metodologi/produk) — beda dengan
`RENCANA-FASE-2.md`/`RENCANA-UI-UX.md`. Boleh dieksekusi kapan saja Anda siap.

Baca bersama memory sesi `vektor-gauss-vps` (bukti blokir cloud) dan
`INFRA-GITHUB-ACTIONS.md` (kenapa runner WSL2 tidak stabil — VPS asli justru
menyelesaikan masalah itu, lihat §5).

---

## ⚠️ 1. SEBELUM bayar apa pun — tes akses IDX dari VPS kandidat dulu

**Ini bagian terpenting dokumen ini.** "VPS Indonesia" **bukan jaminan** lolos dari
blokir Cloudflare IDX. Sudah dibuktikan gagal meski *secara geografis* di Indonesia:

| Yang dicoba | Lokasi fisik | Hasil |
|---|---|---|
| AWS `ap-southeast-3` (Jakarta) | Jakarta, ID | **403 Cloudflare 1020** — diblokir lebih keras dari Singapore |
| Oracle Cloud (Singapore) | Singapura | 403 Cloudflare 1020 |
| AWS `ap-southeast-1` (Singapore) | Singapura | 403 "Just a moment" (challenge JS) |

Polanya jelas: **yang diblokir adalah ASN datacenter/cloud, bukan lokasi geografis.**
AWS Jakarta secara fisik ada di Jakarta tapi tetap kena karena ASN-nya (AS16509 Amazon)
dikenali sebagai cloud, bukan ISP residensial/bisnis biasa. **Belum ada satupun VPS ISP
Indonesia (Biznet Gio, IDCloudHost, Dewaweb, Niagahoster, dll.) yang benar-benar dites** —
itu semua baru "kandidat di atas kertas" di memory riset lama.

### Protokol tes wajib sebelum komit ke paket bulanan/tahunan

1. Sewa **paket termurah/tercepat batal** dulu (harian/jam kalau ada, atau bulanan dengan
   masa refund) dari kandidat.
2. SSH masuk, jalankan tes yang sama persis seperti waktu AWS/Oracle dites:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" https://www.idx.co.id/en
   # kalau 403 → coba scripts/check_idx_access.sh dari repo (curl_cffi impersonation)
   ```
3. **Kalau 403 (apalagi "Cloudflare 1020")** → batalkan/jangan lanjut kontrak, sama
   seperti nasib AWS Jakarta & Oracle SG. Coba provider lain.
4. **Kalau 200** → baru lanjut ke checklist migrasi §4. Simpan hasil tes (nama provider,
   region datacenter, ASN dari `curl ipinfo.io`) ke memory/dokumen ini sebagai referensi.

**Petunjuk yang mungkin membantu memilih kandidat**: cari provider yang benar-benar
"ISP" (punya blok IP milik sendiri terdaftar sebagai ISP/telco, bukan reseller cloud
generic di atas datacenter asing) — biasanya provider lokal kecil-menengah dengan basis
pelanggan residensial/UMKM, bukan yang bersaing head-to-head dengan AWS/GCP secara
eksplisit. Ini dugaan berdasar pola blokir Cloudflare (per-ASN), **bukan jaminan** — tetap
wajib dites langsung.

---

## 2. Yang sudah portable (kabar baik)

Skema & arsitektur sengaja dibangun untuk migrasi ini sejak awal:

- **PostgreSQL native, bukan sesuatu yang terkunci ke WSL.** `pg_dump`/`pg_restore`
  standar. Ukuran saat ini: **~815 MB** (`SELECT pg_size_pretty(pg_database_size(...))`,
  dicek 2026-09-05) — kecil, transfer dalam hitungan menit lewat koneksi rumah biasa.
- **Semua konfigurasi lewat `.env`**, tidak ada path/host yang di-hardcode di kode
  (`config.py` baca `os.environ` dengan default masuk akal). Pindah environment = pindah
  satu file `.env`.
- **Kode Python + bash biasa**, tidak ada dependensi Windows/WSL-spesifik di
  `src/vg/*` atau `eod.sh`/`backfill.sh`. `curl_cffi` (impersonasi Chrome untuk lolos
  Cloudflare) jalan di Linux mana pun, sudah dites dari 3 VPS cloud berbeda (walau
  hasilnya diblokir — yang gagal itu jaringannya, bukan tooling-nya).
- **Raw artifacts** (`data/artifacts/`, JSON/zip mentah harian) = **~573 MB** saat ini,
  tumbuh perlahan (~harian kecil). Total checkout + data ≈ **693 MB**.
- **Satu-satunya bagian yang benar-benar Windows-spesifik**: Task Scheduler sebagai
  driver EOD. Di VPS Linux ini justru **jadi lebih sederhana** — `cron` biasa, tidak ada
  drama siklus hidup VM seperti WSL2 (lihat §5).

## 3. Yang TIDAK portable / perlu perhatian khusus

- **`.env` berisi secret** (token Telegram, kredensial DB) — jangan pernah lewat git.
  Transfer langsung via `scp`/manual copy-paste ke VPS baru.
- ~~**`git remote origin` saat ini menunjuk ke path lokal Windows**~~ **RESOLVED
  2026-09-05.** Repo privat lama `github.com/jaringan-bot/vektor-gauss` (dari eksperimen
  VPS session 2) masih ada dan dipakai kembali atas pilihan Anda. Windows `origin` sudah
  benar sejak awal; WSL `origin` diarahkan ulang ke URL yang sama
  (`git remote set-url origin https://github.com/jaringan-bot/vektor-gauss.git`) +
  `gh auth setup-git` supaya kredensial `gh` (akun `jaringan-bot`, token scope `repo`)
  jadi credential helper git. Windows sudah `git push` (`b4c4aaf..d5f6767`), WSL sudah
  `git fetch` bersih (`main...origin/main`, tidak ada divergensi). **VPS nanti tinggal
  `git clone https://github.com/jaringan-bot/vektor-gauss.git` + autentikasi `gh` di sana
  — tidak perlu `git bundle`/`rsync` kode lagi.**
- **Kalender/timezone**: pastikan VPS diset `Asia/Jakarta` (WIB) — cron 19:15 WIB harus
  cocok, jangan andalkan default UTC datacenter.

## 4. Checklist migrasi (jalankan setelah pre-flight §1 lolos)

1. **Pre-flight IDX** (§1) — sudah lolos, catat provider/region/ASN yang dipakai.
2. **Setup dasar VPS**: `apt install python3.12 python3.12-venv postgresql-16 git`,
   `timedatectl set-timezone Asia/Jakarta`.
3. **Bawa kode**: `git clone https://github.com/jaringan-bot/vektor-gauss.git`, lalu
   `gh auth login` (atau salin token) supaya push/pull berikutnya jalan — remote sudah
   siap sejak 2026-09-05 (§3), tidak perlu `git bundle`/`rsync` lagi.
4. **Bawa database**:
   ```bash
   # di WSL (sumber)
   pg_dump -Fc vektorgauss > vg_$(date +%F).dump   # ~815 MB, cek dulu ukuran aktual
   scp vg_*.dump user@vps-baru:/tmp/
   # di VPS (tujuan)
   createdb vektorgauss && pg_restore -d vektorgauss /tmp/vg_*.dump
   ```
5. **Bawa raw artifacts**: `rsync -avz ~/vektor-gauss/data/artifacts/ user@vps-baru:~/vektor-gauss/data/artifacts/` (~573 MB).
6. **Bawa `.env`**: copy manual (jangan via git) — DB url ganti ke `localhost` VPS baru,
   isi lain tetap sama (token Telegram, watchlist, dll).
7. **`pip install -e ".[dev]"` di venv VPS**, `pytest` — harus tetap 78/78 (angka saat
   ini, 2026-09-05) sebelum lanjut.
8. **Cron**, bukan Task Scheduler:
   ```cron
   15 19 * * 1-5  cd /home/vg/vektor-gauss && . .venv/bin/activate && bash vg-eod.sh >> eod.log 2>&1
   ```
   Jauh lebih sederhana dari `wsl.exe -d ... -- bash ...` — tidak ada lapisan VM yang
   perlu "dibangunkan" dulu.
9. **Masa paralel wajib, 3–5 hari bursa**: biarkan **KEDUA** sisi (PC rumah + VPS baru)
   jalan bersamaan, EOD sama-sama collect tapi **hanya satu yang boleh kirim ke Telegram**
   (matikan `VG_TELEGRAM_BOT_TOKEN` di salah satu sisi, atau beri flag `--dry-run` di sisi
   yang tidak aktif) — supaya kalau VPS ternyata diam-diam gagal (mis. diblokir setelah
   beberapa hari, bukan langsung), PC rumah masih jadi jaring pengaman dan data tidak
   bolong. **Jangan matikan PC rumah** sebelum yakin VPS stabil.
10. **Cutover**: setelah paralel run identik ≥3–5 hari bursa, matikan Task Scheduler di
    PC rumah, VPS jadi satu-satunya sumber.

## 5. Bonus: VPS asli membuka kembali opsi GitHub Actions

Percobaan runner GitHub Actions self-hosted (`vg-wsl-bogor`) gagal **khusus** karena
WSL2 VM mati sendiri di antara sesi (`INFRA-GITHUB-ACTIONS.md`, `HANDOFF.md`). Itu bukan
masalah "GitHub Actions"-nya — itu masalah WSL2. **Di VPS Linux asli (selalu nyala,
bukan VM yang tidur), pendekatan runner yang sama kemungkinan besar akan berhasil** —
worth dicoba ulang setelah migrasi, sebagai pengganti cron biasa kalau Anda mau riwayat
run/log yang lebih rapi di GitHub. Bukan wajib, cron polos di §4.8 sudah cukup.

## 6. Perkiraan biaya & waktu

- Biaya kandidat (dari riset lama, **belum divalidasi lolos-tidaknya**, lihat §1):
  Rp50.000–150.000/bulan untuk paket VPS entry-level lokal.
- Waktu transfer data (§4.4–4.5): ≈1,4 GB total, beberapa menit di koneksi rumah biasa.
- Waktu kerja migrasi murni (checklist §4, tanpa masa paralel): perkiraan setengah hari
  kerja sekali jalan, asal pre-flight §1 sudah lolos duluan.

---

## Pertanyaan terbuka (perlu keputusan Anda)

1. **Kandidat VPS mana yang mau ditest duluan?** Biznet Gio / IDCloudHost / Dewaweb /
   Niagahoster, atau ada yang lain di radar Anda?
2. ~~Remote GitHub~~ **RESOLVED 2026-09-05** — pakai `jaringan-bot/vektor-gauss` (repo
   lama), sudah diverifikasi jalan dari Windows & WSL.
3. Kapan target waktunya — sekarang sekadar disiapkan, atau ada tenggat migrasi konkret?

---

*Ditulis 2026-09-05. Angka ukuran DB/data/repo diambil langsung dari WSL box hari ini —
akan membesar seiring waktu, cek ulang sebelum eksekusi migrasi kalau sudah lama.*
