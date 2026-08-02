"""Xác thực và phân quyền.

Trách nhiệm: cấp JWT khi đăng nhập và cung cấp dependency kiểm tra vai trò
cho các router khác. Dự án yêu cầu tối thiểu hai vai trò:

    operator  — mở phiên teleop, thu demo, xem demo của chính mình
    reviewer  — xem mọi demo, duyệt / từ chối, gom dataset, chạy huấn luyện

Ranh giới này là thứ làm human-in-the-loop có ý nghĩa: operator không được tự
duyệt demo của mình vào tập huấn luyện.

Endpoint dự kiến:
    POST /auth/login    (form: username, password) -> TokenResponse
    POST /auth/refresh  (refresh_token: str)       -> TokenResponse
    GET  /auth/me       ()                         -> UserResponse

Dependency dự kiến:
    async def current_user(token: str) -> UserResponse
    def require_role(*roles: UserRole) -> Callable
"""

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])
