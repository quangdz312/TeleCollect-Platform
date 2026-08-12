# Chạy web local

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
