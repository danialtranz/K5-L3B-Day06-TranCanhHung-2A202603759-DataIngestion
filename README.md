# Day 06 · Data Ingestion Engineering — Bài nộp

**Học viên:** Trần Cảnh Hưng · **MSSV:** 2A202603759 · **Lớp:** L3B  
**Repo:** https://github.com/danialtranz/K5-L3B-Day06-TranCanhHung-2A202603759-DataIngestionEngineering  
**Thông tin nộp:** [STUDENT.md](STUDENT.md) · **Báo cáo:** [report/individual/2A202603759_TranCanhHung.md](report/individual/2A202603759_TranCanhHung.md)

Lab cá nhân: đọc PDF → chuẩn hóa → chunk (A/B/C) → embedding thật → Chroma → benchmark Hit@1 / Evidence Hit@3 + audit thủ công.

Tài liệu đề bài gốc (rubric, GUIDE, …): [GUIDE.md](GUIDE.md), [BENCHMARK.md](BENCHMARK.md), [SUBMISSION.md](SUBMISSION.md), [RUBRIC.md](RUBRIC.md). Stack kỹ thuật: [TechStacks.md](TechStacks.md).

---

## Stack đã chạy

| Thành phần | Giá trị |
|---|---|
| Embedding | `sentence-transformers/all-MiniLM-L6-v2` (384-d, local) |
| Vector store | Chroma persistent, cosine |
| Metadata / quan hệ | SQLite (`starter/data/lab.db`, gitignored) |
| PDF parser | PyMuPDF |
| Chiến lược | **A** fixed 500/80 · **B** heading+paragraph max 1000/min 200 · **C** = B + filter `section` khi câu có filter |
| Chi phí API embedding | 0 (local) |

---

## Đường dẫn dữ liệu và artifacts

| Mục | Đường dẫn |
|---|---|
| Manifest (nguồn, quyền, số trang) | [data/manifest.json](data/manifest.json) |
| Corpus PDF | **Không commit** (bản quyền Wiley). Đặt file PDF Chapter 5 vào `assets/` hoặc set `PDF_PATH` trong `starter/.env`. |
| Gold questions (25 câu, 20 dev / 5 test) | [starter/data/questions.json](starter/data/questions.json) |
| Experiment log A/B/C | [artifacts/experiment_log.csv](artifacts/experiment_log.csv) |
| Trace + summary (run đã nộp) | `starter/results/traces_20261113T212140Z.json`, `summary_*.json` |
| Nhãn audit thủ công | `starter/results/audit_*.csv`, `audit_summary.json` |
| Báo cáo cá nhân | [report/individual/2A202603759_TranCanhHung.md](report/individual/2A202603759_TranCanhHung.md) |
| Code pipeline | `starter/src/` (`ingestion`, `chunking`, `embeddings`, `store`, `evaluation`, `run`) |

Người chấm có thể đối chiếu evidence trong `questions.json` + top-3 trong trace mà **không cần** PDF. Muốn tái tạo index đầy đủ thì cần PDF local hợp pháp.

---

## Setup

Yêu cầu: Python **3.11–3.14**, mạng lần đầu để tải model MiniLM.

```bash
cd starter
python -m venv .venv
```

Kích hoạt venv:

- Windows PowerShell: `.\.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

```bash
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# macOS/Linux: cp .env.example .env
```

Trong `.env`:

- Để trống `PDF_PATH` nếu PDF nằm trong `../assets/*.pdf` (tự chọn file đầu tiên), **hoặc**
- Ghi đường dẫn tuyệt đối tới PDF Chapter 5.
- Embedding local **không cần** API key. Key chat chỉ cần nếu chạy demo UI (`python -m src.app`).

**Không Docker** cho stack đã nộp (SQLite + Chroma). `docker-compose.yml` trong starter chỉ là tùy chọn pgvector của đề, không dùng cho bài này.

---

## Chạy pipeline và benchmark

Mọi lệnh dưới đây chạy từ thư mục `starter/` với venv đã bật.

### 1) Ingest + chunk + embed + index A/B/C (+ benchmark nếu có questions)

```bash
python -m src.run
```

Chỉ ingest/index, bỏ qua benchmark:

```bash
python -m src.run --skip-benchmark
```

Tùy chọn: `python -m src.run --pdf "D:\path\to\chapter5.pdf"`

Kết quả ghi vào `starter/results/` (`chunk_stats.json`, `index_timings.json`, và nếu chạy benchmark: `traces_*.json`, `summary_*.json`). SQLite: `starter/data/lab.db`. Chroma: `starter/data/chroma/` (gitignored — phải chạy lại ingest trên máy mới).

### 2) Chỉ chạy lại benchmark trên index Chroma đã có

```bash
python scripts/run_benchmark_only.py
```

### 3) (Tùy chọn) Demo chat/retrieval UI

```bash
python -m src.app
```

---

## Xác minh nhanh bài nộp (không cần re-run)

1. Đọc [STUDENT.md](STUDENT.md) và báo cáo cá nhân.
2. Mở `starter/results/summary_20261113T212140Z.json` — Hit@1 / Hit@3 theo strategy × split.
3. Mở `starter/results/audit_summary.json` — coherence / metadata / filter / grounding.
4. Đối chiếu giả thuyết A/B/C trong `artifacts/experiment_log.csv`.

Run đã chốt trong báo cáo: **`20261113T212140Z`**.

---

## Cấu trúc repo (rút gọn)

```text
STUDENT.md
README.md                          ← file này (setup / chạy / đường dẫn)
data/manifest.json
artifacts/experiment_log.csv
report/individual/2A202603759_TranCanhHung.md
starter/
  requirements.txt
  .env.example
  src/                             ← pipeline
  data/questions.json              ← benchmark gold
  results/                         ← traces, summary, audit labels
  scripts/run_benchmark_only.py
```
