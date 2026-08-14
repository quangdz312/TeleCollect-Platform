# Chạy web local

> Phiên bản môi trường (Python 3.12.6, robosuite 1.5.2, MuJoCo 3.8.1, Node 22.18,
> Next 16) và cách chọn GPU trên Windows: xem [`README.md`](README.md).

Mở PowerShell tại thư mục project:

```powershell
cd D:\VIN_AI_TC\Project\P-111
.\.venv\Scripts\Activate.ps1
```

Terminal 1 — backend:

```powershell
python -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

Terminal 2 — frontend:

```powershell
cd frontend
npm run dev
```

Mở [http://localhost:3000](http://localhost:3000). Backend/API ở [http://localhost:8000/docs](http://localhost:8000/docs).

Dừng server: nhấn `Ctrl+C` ở từng terminal.
