"""Stream một file trên đĩa với hỗ trợ HTTP Range (RFC 7233) + HEAD.

Trách nhiệm: logic dùng chung cho MỌI endpoint trả file lớn theo chunk —
`GET /demos/{id}/playback` (Bước 3b) và `GET /datasets/{id}/download`
(Bước 4). Tách ra đây để không nhân bản đúng một khối code Range giữa hai
router.
"""

from collections.abc import Iterator
from email.utils import formatdate
from pathlib import Path

from fastapi import Request, status
from fastapi.responses import Response, StreamingResponse

from src.services.ranges import RangeNotSatisfiableError, parse_range_header

STREAM_CHUNK_SIZE = 1024 * 1024  # 1MB — đọc đúng số byte trong range, không đọc cả file.


def _iter_file_range(path: Path, start: int, length: int) -> Iterator[bytes]:
    """Generator đọc chunk 1MB, chỉ đọc đúng `length` byte bắt đầu từ `start`.

    Mở file BÊN TRONG generator (không mở trước rồi truyền handle vào) và
    dùng try/finally để handle LUÔN được đóng — kể cả khi client ngắt kết nối
    giữa chừng (Starlette gọi `.close()` trên generator, ném `GeneratorExit`
    vào đúng điểm `yield` đang treo, `finally` vẫn chạy). Trên Windows, rò rỉ
    handle sẽ khiến các thao tác xoá file sau đó ném `PermissionError` — lỗi
    này KHÔNG xảy ra trên Linux nên rất dễ lọt qua CI chạy Linux rồi vỡ khi
    vận hành thật trên Windows.
    """
    f = open(path, "rb")
    try:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(STREAM_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        f.close()


def stream_file_range(
    path: Path,
    request: Request,
    media_type: str,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    """Trả `Response`/`StreamingResponse` phù hợp cho `path`, xử lý:

    - `HEAD`: trả header (Content-Length/Content-Range) không kèm body.
    - `Range` hợp lệ: `206 Partial Content` kèm `Content-Range`.
    - Không có `Range` hoặc multi-range không hỗ trợ: `200` full file.
    - `Range` ngoài kích thước file: `416 Range Not Satisfiable`.

    Giới hạn có chủ ý: chỉ hỗ trợ đơn-range (xem `src/services/ranges.py`).
    """
    file_stat = path.stat()
    file_size = file_stat.st_size
    base_headers = {
        "Accept-Ranges": "bytes",
        "Last-Modified": formatdate(file_stat.st_mtime, usegmt=True),
        "Cache-Control": "private, max-age=3600",
        **(extra_headers or {}),
    }

    try:
        resolved = parse_range_header(request.headers.get("range"), file_size)
    except RangeNotSatisfiableError:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={**base_headers, "Content-Range": f"bytes */{file_size}"},
        )

    if request.method == "HEAD":
        if resolved is not None:
            head_headers = {
                **base_headers,
                "Content-Range": resolved.content_range_header,
                "Content-Length": str(resolved.length),
            }
            return Response(status_code=status.HTTP_206_PARTIAL_CONTENT, headers=head_headers)
        return Response(
            status_code=status.HTTP_200_OK,
            headers={**base_headers, "Content-Length": str(file_size)},
            media_type=media_type,
        )

    if resolved is None:
        return StreamingResponse(
            _iter_file_range(path, 0, file_size),
            status_code=status.HTTP_200_OK,
            media_type=media_type,
            headers={**base_headers, "Content-Length": str(file_size)},
        )

    return StreamingResponse(
        _iter_file_range(path, resolved.start, resolved.length),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=media_type,
        headers={
            **base_headers,
            "Content-Range": resolved.content_range_header,
            "Content-Length": str(resolved.length),
        },
    )
