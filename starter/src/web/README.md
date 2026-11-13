# Day 06 · Web chat (React + Vite)

Grounded RAG chat: **SSE streaming** (`POST /api/chat/stream`), IEEE cite chips → chunk, right panel **PDF** jumps to `#page=N` via `/api/corpus.pdf`.

## Dev

Terminal 1 — API:

```powershell
cd starter
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m src.app
```

Terminal 2 — FE:

```powershell
cd starter\src\web
npm install
npm run dev
```

Mở http://127.0.0.1:5173 (proxy `/api` → FastAPI `:8000`).

## Build (serve từ FastAPI)

```powershell
cd starter\src\web
npm run build
cd ..\..
python -m src.app
```

Rồi mở http://127.0.0.1:8000 nếu đã có `web/dist`.
