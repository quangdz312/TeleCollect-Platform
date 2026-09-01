"""Push a collected batch to the shared server.

The app signs itself into its own SQLite database as `local-desktop`, and that
account has nothing to do with the team's server: different database, different
`JWT_SECRET`, so a token from one is meaningless to the other. Making them one
account would mean either shipping the server's signing secret to every laptop
or letting the app's self-issued token authenticate against shared data.

So this is a second, separate session — the way a machine has a local user but
still needs its own credentials to push to a remote. The person signs in once
with their real server account; from then on the app holds tokens.

Tokens, never the password. A password on disk is readable by anyone who
borrows the laptop and works everywhere that account works; an access token
expires on its own, and a refresh token can be revoked server-side without
changing the person's password.

Upload reuses `POST /raw/batches/{id}/import`, which already rebuilds the
collection files, keeps episode ids intact and carries the rendered videos —
the same endpoint the web accepts a hand-zipped batch through.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_app.config import _load_settings, _save_settings

#: Máy chủ của dự án. App đóng gói cho một dự án nên địa chỉ này là hằng số,
#: không phải câu hỏi dành cho người dùng: bắt gõ tay thì mỗi lần đăng nhập là
#: một cơ hội gõ sai, và người thu dữ liệu không có lý do gì để biết nó.
#: Đặt `TELECOLLECT_SERVER` khi cần trỏ sang máy chủ khác (staging, bản dựng
#: riêng) mà không phải sửa mã.
DEFAULT_SERVER = os.environ.get("TELECOLLECT_SERVER") or "https://telecollect.io.vn"

#: Long enough for a multi-gigabyte batch over a slow connection.
UPLOAD_TIMEOUT_S = 3600
#: Short: these are small JSON calls, and a hung one should not freeze the UI.
API_TIMEOUT_S = 30


class SyncError(RuntimeError):
    """Anything the person needs to read and act on.

    `status` là mã HTTP khi lỗi đến từ máy chủ, `None` khi lỗi là mạng hoặc do
    chính app. Có nó thì bên gọi phân biệt được "token hết hạn, thử lại đi" với
    "máy chủ từ chối nội dung này", thay vì gửi lại cả gói vài MB một cách vô ích.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SyncSession:
    server: str
    username: str
    role: str

    def as_dict(self) -> dict[str, str]:
        return {"server": self.server, "username": self.username, "role": self.role}


def _normalise(server: str) -> str:
    """Accept what a person would type and return an origin with no trailing slash."""

    value = server.strip().rstrip("/")
    if not value:
        raise SyncError("Nhập địa chỉ máy chủ, ví dụ https://telecollect.io.vn")
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value


def _request(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    timeout: int = API_TIMEOUT_S,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except (ValueError, OSError):
            detail = ""
        if exc.code in (401, 403):
            raise SyncError(
                detail or "Máy chủ từ chối: đăng nhập lại", status=exc.code
            ) from exc
        if exc.code == 507:
            raise SyncError(
                detail or "Máy chủ hết dung lượng lưu trữ", status=exc.code
            ) from exc
        raise SyncError(detail or f"Máy chủ trả lỗi {exc.code}", status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise SyncError(f"Không kết nối được tới máy chủ: {exc.reason}") from exc
    return json.loads(body) if body else {}


# --- stored session ----------------------------------------------------------


def _store(**values: str | None) -> None:
    settings = _load_settings()
    for key, value in values.items():
        if value is None:
            settings.pop(key, None)
        else:
            settings[key] = value
    _save_settings(settings)


def current_session() -> SyncSession | None:
    settings = _load_settings()
    server = settings.get("sync_server")
    username = settings.get("sync_username")
    if not isinstance(server, str) or not isinstance(username, str) or not server:
        return None
    if not settings.get("sync_token"):
        return None
    return SyncSession(server=server, username=username, role=str(settings.get("sync_role") or ""))


def sign_in(server: str, username: str, password: str) -> SyncSession:
    """Exchange credentials for tokens, then forget the password."""

    origin = _normalise(server)
    body = urllib.parse.urlencode({"username": username, "password": password}).encode()
    payload = _request(
        f"{origin}/api/v1/auth/login",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    token = str(payload.get("access_token") or "")
    if not token:
        raise SyncError("Máy chủ không trả về token")

    profile = _request(
        f"{origin}/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"},
    )
    _store(
        sync_server=origin,
        sync_username=str(profile.get("username") or username),
        sync_role=str(profile.get("role") or ""),
        sync_token=token,
        sync_refresh=str(payload.get("refresh_token") or ""),
    )
    session = current_session()
    if session is None:  # pragma: no cover - only if settings failed to persist
        raise SyncError("Không lưu được phiên đăng nhập")
    return session


def sign_out() -> None:
    _store(sync_token=None, sync_refresh=None, sync_username=None, sync_role=None)


def _refresh() -> str | None:
    """Trade the refresh token for a new access token, or give up quietly."""

    settings = _load_settings()
    server, refresh = settings.get("sync_server"), settings.get("sync_refresh")
    if not isinstance(server, str) or not isinstance(refresh, str) or not refresh:
        return None
    try:
        payload = _request(
            f"{server}/api/v1/auth/refresh",
            data=json.dumps({"refresh_token": refresh}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    except SyncError:
        return None
    token = str(payload.get("access_token") or "")
    if token:
        _store(sync_token=token, sync_refresh=str(payload.get("refresh_token") or refresh))
    return token or None


# --- uploading ---------------------------------------------------------------


def _pack_from_review(workspace: Path, batch: dict[str, Any], destination: Path) -> int:
    """Dựng gói từ `review/datasets`, nơi dữ liệu thu bằng app thật sự nằm.

    Đợt thu có hai chỗ chứa tập. Thư mục `batches/<tên>/episodes/` là chỗ cũ,
    một thư mục cho mỗi tập. Còn màn hình thu dữ liệu ghi vào `review/datasets/`
    dưới dạng một file HDF5 nhiều demo, vì cả phần chấm điểm lẫn phần duyệt đọc
    ở đó — chấm điểm so tương đối trên toàn kho nên không tách theo đợt thu được.

    Push trước đây chỉ quét thư mục thứ nhất, nên mọi tập thu bằng app đều không
    lọt vào gói: đợt thu mới thì gói rỗng, đợt thu cũ thì gói chỉ có phần đã
    import từ trước và máy chủ trả về "đã có hết rồi".

    Mỗi file mang sẵn `telecollect_collection_batch_id`, nên biết được demo nào
    thuộc đợt thu nào mà không cần tra thêm ở đâu. Ở đây tách ngược mỗi demo
    thành một thư mục đúng định dạng máy chủ đòi — nghịch đảo của `_rebuild`
    bên `src/labeling/batch_import.py`.
    """

    import h5py

    from src.labeling.workspace import Workspace, video_filename

    space = Workspace(workspace / "review")
    if not space.datasets_dir.is_dir():
        return 0

    batch_id = str(batch.get("id") or "")
    task = str(batch.get("task") or "")
    written = 0

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("batch.json", json.dumps(batch, ensure_ascii=False, indent=2))
        for source in sorted(space.datasets_dir.glob("*.hdf5")):
            with h5py.File(source, "r") as handle:
                if "data" not in handle:
                    continue
                data = handle["data"]
                for demo in sorted(data.keys()):
                    node = data[demo]
                    # Demo thắng file: `_rebuild` đóng dấu ở cả hai mức, và một
                    # mã cũ sót lại trên file sẽ xếp tập vào nhầm đợt thu.
                    owner = str(
                        node.attrs.get("telecollect_collection_batch_id")
                        or data.attrs.get("telecollect_collection_batch_id")
                        or ""
                    )
                    if owner != batch_id:
                        continue

                    episode_id = f"{source.name}::{demo}"
                    folder = f"episodes/{source.stem}__{demo}"

                    single = destination.parent / f"{uuid.uuid4().hex}.hdf5"
                    try:
                        with h5py.File(single, "w") as output:
                            group = output.create_group("data")
                            for key, value in data.attrs.items():
                                group.attrs[key] = value
                            handle.copy(node, group, name="demo_0")
                            group.attrs["total"] = int(node.attrs.get("num_samples", 0))
                        archive.write(single, f"{folder}/trajectory.hdf5")
                    finally:
                        single.unlink(missing_ok=True)

                    archive.writestr(
                        f"{folder}/meta.json",
                        json.dumps(
                            {
                                "format_version": 1,
                                "episode_id": episode_id,
                                "task_name": str(node.attrs.get("telecollect_task") or task),
                                "source": "scripted",
                                "num_steps": int(node.attrs.get("num_samples", 0)),
                                "requested_quality": str(
                                    node.attrs.get("telecollect_requested_quality") or "",
                                ),
                                "recorded_success": bool(node.attrs.get("success", False)),
                                "original_source": source.name,
                                "original_demo": demo,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )

                    video = space.videos_dir / video_filename(episode_id)
                    if video.is_file():
                        archive.write(video, f"{folder}/review.mp4")
                    written += 1
    return written


def pack_batch(workspace: Path, batch: dict[str, Any], destination: Path) -> Path:
    """Đóng gói mọi tập của một đợt thu, lấy từ `review/datasets`.

    Không quét `batches/<tên>/episodes/` nữa, kể cả khi thư mục đó có sẵn tập.
    Nó chỉ chứa những gì import từ zip lúc trước, còn tập thu bằng app thì
    không, nên gói dựng theo nó vừa thiếu tập mới vừa lặp lại tập máy chủ đã có
    — đúng cảnh `can` đẩy lên và nhận về "đã có hết rồi" trong khi tập vừa thu
    chưa từng lọt vào gói.
    """

    if _pack_from_review(workspace, batch, destination) == 0:
        raise SyncError("Đợt thu này chưa có tập nào để đẩy lên")
    return destination


def _multipart(fields: dict[str, str], archive: Path) -> tuple[bytes, str]:
    """Build a multipart body. Small batches only — see `upload_batch`."""

    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="archive"; '
        f'filename="{archive.name}"\r\n'
        f"Content-Type: application/zip\r\n\r\n".encode()
    )
    parts.append(archive.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def upload_batch(workspace: Path, batch: dict[str, Any]) -> dict[str, Any]:
    """Send one batch to the server, refreshing the token once if it expired."""

    session = current_session()
    if session is None:
        raise SyncError("Chưa đăng nhập máy chủ")

    batch_id = str(batch.get("id") or "")
    if not batch_id:
        raise SyncError("Đợt thu không có mã")

    scratch = Path(workspace) / ".sync"
    scratch.mkdir(parents=True, exist_ok=True)
    archive = scratch / f"{uuid.uuid4().hex}.zip"
    try:
        pack_batch(Path(workspace), batch, archive)
        body, content_type = _multipart(
            {"name": str(batch.get("name") or batch_id), "overwrite": "false"}, archive,
        )
        url = (
            f"{session.server}/api/v1/raw/batches/"
            f"{urllib.parse.quote(batch_id, safe='')}/import"
        )

        def send(token: str) -> dict[str, Any]:
            return _request(
                url,
                data=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": content_type,
                },
                method="POST",
                timeout=UPLOAD_TIMEOUT_S,
            )

        token = str(_load_settings().get("sync_token") or "")
        try:
            result = send(token)
        except SyncError as exc:
            # Chỉ 401 mới đáng thử lại. Trước đây bắt mọi SyncError, nên một lỗi
            # 422 ("đợt thu này đã có trên máy chủ") cũng khiến app đẩy lại trọn
            # gói vài MB lần nữa rồi báo đúng lỗi cũ.
            if exc.status != 401:
                raise
            # Và chỉ khi refresh thật sự cho token mới: gửi lại token đã hết hạn
            # thì cũng hỏng y như lần đầu.
            renewed = _refresh()
            if not renewed:
                raise
            result = send(renewed)
    finally:
        archive.unlink(missing_ok=True)

    _store(**{f"sync_pushed_{batch_id}": str(result.get("episodes", 0))})
    return result
