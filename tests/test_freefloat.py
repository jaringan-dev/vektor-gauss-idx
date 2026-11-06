"""vg.freefloat: parsing the free-float table of the monthly registry report (LBRE), on real report text."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from vg import freefloat as ff

WIB = ZoneInfo("Asia/Jakarta")

# BBCA, LBRE for September 2026 (announced 7-Oct-2026), as pdfplumber extracts it.
BBCA = """Informasi Saham Free Float
Keterangan Bulan Sebelumnya Bulan Ini
Jumlah saham non-warkat dimiliki oleh seluruh pemegang
52.453.691.120 52.453.691.120
saham kurang dari 5%
Jumlah saham tresuri non-warkat kurang dari 5% 433.298.700 433.298.700
Jumlah saham Free Float 51.823.837.380 51.820.646.928
Jumlah saham tercatat di Bursa per akhir bulan 122.042.299.500 122.042.299.500
% Saham Free Float 42,46 42,46
Jumlah Pemegang Saham Nasabah Pemilik SID
Bulan Sebelumnya Bulan Sekarang Perubahan
767.281 759.569 7.712
Posisi per 30 September 2026"""


def test_bbca_september_2026():
    f = ff.parse_lbre(BBCA)
    assert f.free_float_shares == 51_820_646_928 and f.listed_shares == 122_042_299_500 and f.free_float_pct == 42.46
    assert f.prev_free_float_shares == 51_823_837_380 and f.holders_sid == 759_569 and f.consistent


def test_a_report_whose_percentage_contradicts_its_own_share_counts_is_flagged():
    # MPIX (Oct-2026 sample): 517,574,308 / 1,562,574,308 = 33.1%, but the report says 28.24%.
    text = BBCA.replace("51.820.646.928", "517.574.308").replace("122.042.299.500 122.042.299.500", "1.562.574.308 1.562.574.308") \
               .replace("42,46 42,46", "28,24 28,24")
    f = ff.parse_lbre(text)
    assert f is not None and not f.consistent


def test_no_table_is_none_not_a_guess():
    assert ff.parse_lbre("Laporan Bulanan Registrasi Pemegang Efek\nPerihal ...") is None


def test_the_report_month_comes_from_the_text_and_falls_back_to_the_month_before_the_announcement():
    assert ff.stated_month(BBCA) == date(2026, 9, 30)
    assert ff.stated_month("no month here") is None
    assert ff.report_month(datetime(2026, 10, 7, 18, 14, tzinfo=WIB)) == date(2026, 9, 30)
    assert ff.report_month(datetime(2027, 1, 6, 9, 0, tzinfo=WIB)) == date(2026, 12, 31)
