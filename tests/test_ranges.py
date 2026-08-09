import pytest

from src.services.ranges import RangeNotSatisfiableError, parse_range_header

TOTAL = 1000  # kích thước file giả định cho mọi test dưới đây, trừ khi ghi rõ khác


def test_no_header_returns_none():
    assert parse_range_header(None, TOTAL) is None


def test_empty_header_returns_none():
    assert parse_range_header("", TOTAL) is None


def test_simple_range_0_1023():
    r = parse_range_header("bytes=0-1023", 2000)
    assert r is not None
    assert r.start == 0
    assert r.end == 1023
    assert r.length == 1024
    assert r.total == 2000
    assert r.content_range_header == "bytes 0-1023/2000"


def test_open_ended_range_from_500():
    """'bytes=500-' — dạng trình duyệt gửi nhiều nhất, không được bỏ sót."""
    r = parse_range_header("bytes=500-", TOTAL)
    assert r is not None
    assert r.start == 500
    assert r.end == TOTAL - 1
    assert r.length == TOTAL - 500


def test_suffix_range_last_500_bytes():
    """'bytes=-500' — 500 byte CUỐI file, không phải từ 0 tới 500."""
    r = parse_range_header("bytes=-500", TOTAL)
    assert r is not None
    assert r.start == TOTAL - 500
    assert r.end == TOTAL - 1
    assert r.length == 500


def test_suffix_range_larger_than_file_clamps_to_whole_file():
    r = parse_range_header("bytes=-5000", TOTAL)
    assert r is not None
    assert r.start == 0
    assert r.end == TOTAL - 1
    assert r.length == TOTAL


def test_end_beyond_filesize_clamps_not_416():
    """end vượt kích thước file -> CLAMP về filesize-1 rồi trả 206, KHÔNG phải 416."""
    r = parse_range_header("bytes=0-99999999", 500)
    assert r is not None
    assert r.start == 0
    assert r.end == 499
    assert r.length == 500


def test_start_beyond_filesize_raises_416():
    with pytest.raises(RangeNotSatisfiableError) as exc_info:
        parse_range_header("bytes=99999999-", 500)
    assert exc_info.value.total == 500


def test_start_equal_filesize_raises_416():
    with pytest.raises(RangeNotSatisfiableError):
        parse_range_header("bytes=500-", 500)  # start == total (500) -> ngoài file


def test_start_greater_than_end_raises_416():
    with pytest.raises(RangeNotSatisfiableError):
        parse_range_header("bytes=100-50", TOTAL)


def test_malformed_header_missing_bytes_prefix_returns_none():
    assert parse_range_header("items=0-100", TOTAL) is None


def test_malformed_header_non_numeric_returns_none():
    assert parse_range_header("bytes=abc-def", TOTAL) is None


def test_malformed_header_no_dash_returns_none():
    assert parse_range_header("bytes=12345", TOTAL) is None


def test_empty_spec_returns_none():
    assert parse_range_header("bytes=-", TOTAL) is None


def test_multi_range_not_supported_returns_none():
    """Multi-range -> không hỗ trợ multipart/byteranges, trả full file."""
    assert parse_range_header("bytes=0-99,200-299", TOTAL) is None


def test_single_byte_range():
    r = parse_range_header("bytes=0-0", TOTAL)
    assert r is not None
    assert r.start == 0
    assert r.end == 0
    assert r.length == 1
