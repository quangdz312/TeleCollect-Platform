# TeleCollect frontend

Next.js frontend for the TeleCollect FastAPI backend.

## Run

Start the backend first from the repo root:

```bash
python -m uvicorn src.main:app --reload --port 8000
```

Then run the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000.

By default the frontend calls `http://localhost:8000`. To use another backend:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

On Windows PowerShell:

```powershell
$env:NEXT_PUBLIC_API_URL="http://localhost:8000"
npm run dev
```

## Seed Accounts

After running the backend seed scripts from the root README:

```bash
python -m scripts.create_admin --username admin --password Admin12345
python -m scripts.seed_tasks
python -m scripts.seed_demos --reset
```

You can sign in with:

| Username | Password | Role |
| --- | --- | --- |
| `admin` | `Admin12345` | admin |
| `seed_reviewer1` | `seedpassword1` | reviewer |
| `seed_operator1` | `seedpassword1` | operator |

## Current Backend Coverage

Connected to the backend:

- auth
- users
- tasks
- demos/review/playback
- datasets

Not implemented in the backend core build yet:

- live teleoperation WebSocket/session endpoints
- training and evaluation jobs
