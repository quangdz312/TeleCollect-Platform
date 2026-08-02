"""Schema Pydantic (biên API) và model CSDL (lớp lưu trữ).

Hai lớp này cố tình tách rời: schema là hợp đồng với frontend, model là bố
cục bảng. Trộn chúng làm một sẽ khiến mọi thay đổi lược đồ CSDL rò rỉ thẳng
ra API.

Module:
    enums    — trạng thái và vai trò dùng chung cho cả hai lớp
    schemas  — request/response Pydantic
    db       — bảng CSDL (SQLAlchemy)
"""
