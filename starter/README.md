# Starter — pipeline đã hoàn thiện (bài nộp)

> **Hướng dẫn setup / chạy đầy đủ:** xem [README.md](../README.md) ở root repo.

Stack đã chốt: **SQLite + Chroma + MiniLM** (không bắt buộc Docker/pgvector). Entry points:

```bash
cd starter
python -m pip install -r requirements.txt
# copy .env.example → .env; đặt PDF vào ../assets/ hoặc set PDF_PATH

python -m src.run                      # ingest + index A/B/C + benchmark
python scripts/run_benchmark_only.py   # chỉ benchmark lại
python -m src.app                      # demo UI (tùy chọn)
```

Artifacts: `data/questions.json`, `results/` (traces, summary, audit). Manifest corpus: `../data/manifest.json`.

File `docker-compose.yml` / `init.sql` còn lại từ khung đề pgvector — **không dùng** cho bài nộp này.
