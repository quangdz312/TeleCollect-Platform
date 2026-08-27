"""Router gốc — gom các router tài nguyên lại thành một.

`src/main.py` include router này dưới prefix `/api/v1`. Bản thân file không
định nghĩa endpoint nào; mọi endpoint sống trong module tài nguyên tương ứng.

Đây cũng là chỗ duy nhất phân biệt hai sản phẩm dựng trên `src/`. App đóng gói
và bản web deploy chạy CÙNG một API; khác nhau ở chỗ app còn thu dữ liệu, còn
web chỉ nhận dữ liệu đã thu. Ranh giới đó là `settings.collection_enabled`, và
nó nằm ở đây thay vì rải trong từng endpoint để đọc một lần là biết bản này hở
ra những gì.
"""

from fastapi import APIRouter

from src.api import (
    auth,
    datasets,
    demos,
    integrations,
    labeling,
    raw,
    tasks,
    teleop,
    training,
    users,
)
from src.config import get_settings

router = APIRouter()

router.include_router(auth.router)
router.include_router(users.router)
router.include_router(tasks.router)
router.include_router(integrations.router)
if get_settings().collection_enabled:
    # Thu dữ liệu trực tiếp: phiên teleop và upload demo thủ công.
    router.include_router(teleop.router)
    router.include_router(demos.router)
router.include_router(datasets.router)
router.include_router(labeling.router)
router.include_router(raw.router)
router.include_router(training.router)
