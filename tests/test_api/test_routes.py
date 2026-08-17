from collections import Counter

import pytest

from src.main import app


def test_openapi_has_no_duplicate_operation_ids():
    """FastAPI sinh operationId mặc định dựa trên `route.methods` (một set) —
    route dùng chung 1 APIRoute cho nhiều method (vd GET+HEAD qua api_route)
    sẽ bị tính operationId MỘT LẦN rồi gán trùng cho mọi method, làm
    openapi.json chứa operationId trùng nhau. Route nào bị lỗi này phải tách
    thành 2 decorator riêng với operation_id tường minh."""
    schema = app.openapi()
    operation_ids = [
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
        if "operationId" in operation
    ]
    duplicates = [op_id for op_id, count in Counter(operation_ids).items() if count > 1]
    assert duplicates == []


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
