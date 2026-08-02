"""Băm mật khẩu và ký JWT.

Trách nhiệm: nơi duy nhất biết `settings.jwt_secret`. Router `auth` gọi vào
đây; không module nào khác đọc secret trực tiếp.
"""


def hash_password(plain: str) -> str:
    """Băm mật khẩu để lưu vào CSDL."""
    raise NotImplementedError


def verify_password(plain: str, hashed: str) -> bool:
    """So khớp mật khẩu người dùng nhập với bản băm đã lưu."""
    raise NotImplementedError


def create_access_token(user_id: str, role: str, expires_in_s: int = 3600) -> str:
    """Ký JWT chứa user_id và vai trò."""
    raise NotImplementedError


def decode_token(token: str) -> dict[str, str]:
    """Giải và kiểm tra JWT; ném lỗi nếu hết hạn hoặc chữ ký sai."""
    raise NotImplementedError
