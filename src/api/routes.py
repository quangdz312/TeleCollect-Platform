"""Router gốc — gom các router tài nguyên lại thành một.

`src/main.py` include router này dưới prefix `/api/v1`. Bản thân file không
định nghĩa endpoint nào; mọi endpoint sống trong module tài nguyên tương ứng.
"""

from fastapi import APIRouter

from src.api import auth, datasets, demos, tasks, teleop, training, users

router = APIRouter()

router.include_router(auth.router)
router.include_router(users.router)
router.include_router(tasks.router)
router.include_router(teleop.router)
router.include_router(demos.router)
router.include_router(datasets.router)
router.include_router(training.router)
