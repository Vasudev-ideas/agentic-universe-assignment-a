from solution.normalization import IST, normalize_header, normalize_phone, parse_date, parse_decimal, parse_timestamp


def test_phone_formats_and_invalid_values():
    assert normalize_phone("+91 77187 50036") == "+917718750036"
    assert normalize_phone("07718750036") == "+917718750036"
    assert normalize_phone("517718750036") is None


def test_date_decimal_and_timestamp():
    assert parse_date("01-Dec-2026") == "2026-12-01"
    assert parse_date("31/02/2026") is None
    assert parse_decimal("Rs. 1,234.5") == "1234.50"
    assert parse_decimal("-1") is None
    assert parse_timestamp("2026-10-03 10:01:00").tzinfo == IST
    assert parse_timestamp("2026-10-03T04:31:00Z").hour == 10


def test_header_normalization():
    assert normalize_header("  Policy__  Expiry ") == "policy expiry"
