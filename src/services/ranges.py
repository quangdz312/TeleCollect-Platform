"""Parse và giải quyết HTTP `Range` header (RFC 7233) — tách riêng khỏi
router để test độc lập, không cần dựng HTTP client.

Giới hạn có chủ ý: KHÔNG hỗ trợ multi-range
(`bytes=0-99,200-299` -> trả `multipart/byteranges`). Chỉ đơn-range, đủ cho
mọi player video thật (trình duyệt luôn gửi 1 range mỗi request khi tua).
Multi-range hoặc header sai cú pháp -> coi như không có Range, trả full file.
"""

from dataclasses import dataclass


@dataclass
class ResolvedRange:
    """Range đã được clamp về giới hạn file thật — sẵn sàng để đọc/trả response.

    `start`/`end` là chỉ số byte, INCLUSIVE cả hai đầu (đúng ngữ nghĩa RFC 7233
    Content-Range). `length = end - start + 1`.
    """

    start: int
    end: int
    total: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @property
    def content_range_header(self) -> str:
        return f"bytes {self.start}-{self.end}/{self.total}"


class RangeNotSatisfiableError(Exception):
    """start >= total, hoặc start > end — RFC bắt buộc trả 416 kèm
    `Content-Range: bytes */<total>`."""

    def __init__(self, total: int):
        self.total = total
        super().__init__(f"Range not satisfiable cho file {total} byte")


def parse_range_header(header: str | None, total: int) -> ResolvedRange | None:
    """Trả `None` nếu nên phục vụ full file (không có header, sai cú pháp,
    hoặc multi-range không hỗ trợ). Raise `RangeNotSatisfiableError` nếu
    range hợp lệ về cú pháp nhưng nằm ngoài file.

    `total` là kích thước file tính bằng byte, phải > 0.
    """
    if not header:
        return None
    if not header.startswith("bytes="):
        return None

    spec = header[len("bytes=") :].strip()
    if "," in spec:
        # multi-range — không hỗ trợ, coi như không có Range.
        return None

    if "-" not in spec:
        return None

    start_str, _, end_str = spec.partition("-")
    start_str = start_str.strip()
    end_str = end_str.strip()

    if start_str == "" and end_str == "":
        return None

    if start_str == "":
        # Suffix range: "bytes=-500" -> 500 byte CUỐI file.
        try:
            suffix_length = int(end_str)
        except ValueError:
            return None
        if suffix_length <= 0:
            return None
        start = max(0, total - suffix_length)
        end = total - 1
    else:
        try:
            start = int(start_str)
        except ValueError:
            return None

        if start >= total:
            raise RangeNotSatisfiableError(total)

        if end_str == "":
            # Open-ended: "bytes=500-" -> từ 500 tới hết file.
            end = total - 1
        else:
            try:
                end = int(end_str)
            except ValueError:
                return None
            if end < start:
                raise RangeNotSatisfiableError(total)
            # end vượt kích thước file -> CLAMP về total-1, KHÔNG phải 416.
            end = min(end, total - 1)

    return ResolvedRange(start=start, end=end, total=total)
