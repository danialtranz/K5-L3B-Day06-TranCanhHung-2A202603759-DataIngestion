# Tech stack — Day 06

Phạm vi: ~70 trang, tiếng Anh, không mở rộng scale sau lab.  
Mục tiêu: pipeline **ingestion + retrieval + benchmark** (không bắt buộc chatbot / LLM generation).

## Stack đã chốt

| Thành phần | Lựa chọn | Ghi chú |
|---|---|---|
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2` | English, local; embed **ngoài** Chroma |
| Số chiều | **384** | Giữ cố định cho cả 3 cấu hình |
| Metric | cosine | Cùng metric giữa A/B/C |
| Vector store | **Chroma persistent** (embedded, không server) | Chỉ lưu vector + metadata; **không** dùng embedding mặc định của Chroma |
| Relational DB | **SQLite** (file `.db`) | Không Docker; nguồn sự thật quan hệ |
| Runtime | Python venv | Không Docker cho Chroma/SQLite |
| PDF parser | **PyMuPDF (`fitz`)** | Tách figure, table, body text |
| Ngôn ngữ corpus | English | Không dùng model multilingual |

Dự phòng chất lượng cao hơn (chỉ đổi **trước** khi embed lần đầu): `all-mpnet-base-v2` (768-dim).

## Vai trò SQLite vs Chroma

- **SQLite**: document, page, figure, **table (markdown)**, chunk text, câu hỏi gold, experiment/trace.
- **Chroma**: chỉ **lưu trữ / index vector** để search + metadata filter. Embedding do MiniLM tạo sẵn rồi `upsert`.
- Một chunk = 1 hàng SQLite + 1 vector Chroma (cùng `chunk_id`).

## Schema SQLite

```sql
-- 1) Tài liệu
CREATE TABLE documents (
  document_id   TEXT PRIMARY KEY,
  title         TEXT NOT NULL,
  source_path   TEXT NOT NULL,
  license       TEXT,
  accessed_at   TEXT,
  page_count    INTEGER,
  language      TEXT DEFAULT 'en'
);

-- 2) Trang đã chuẩn hóa (body text: đã loại vùng figure + table)
CREATE TABLE pages (
  page_id       TEXT PRIMARY KEY,          -- vd: doc1_p12
  document_id   TEXT NOT NULL REFERENCES documents(document_id),
  page_number   INTEGER NOT NULL,
  text          TEXT NOT NULL,
  char_count    INTEGER,
  UNIQUE(document_id, page_number)
);

-- 3) Hình đã cắt + caption
CREATE TABLE figures (
  figure_id     TEXT PRIMARY KEY,          -- vd: doc1_fig_1_7
  document_id   TEXT NOT NULL REFERENCES documents(document_id),
  page_number   INTEGER NOT NULL,
  figure_label  TEXT,                      -- "Figure 1.7"
  caption       TEXT NOT NULL,             -- dòng chú thích dưới ảnh
  image_path    TEXT NOT NULL,             -- figures/doc1_fig_1_7.png
  bbox_json     TEXT,                      -- optional: tọa độ cắt trên PDF
  ocr_text      TEXT                       -- OCR thêm label trong ảnh (optional)
);

-- 4) Bảng (table) — lưu Markdown, không lưu ảnh
CREATE TABLE tables (
  table_id      TEXT PRIMARY KEY,          -- vd: doc1_table_5_1
  document_id   TEXT NOT NULL REFERENCES documents(document_id),
  page_number   INTEGER NOT NULL,
  table_label   TEXT,                      -- "TABLE 5.1"
  title         TEXT,                      -- "ANSI Isolation Levels"
  markdown      TEXT NOT NULL,             -- title + bảng Markdown (nội dung embed)
  bbox_json     TEXT,                      -- optional: vùng bảng trên trang
  row_count     INTEGER,
  col_count     INTEGER
);

-- 5) Chunk (mọi chiến lược A/B/C)
CREATE TABLE chunks (
  chunk_id      TEXT PRIMARY KEY,          -- vd: A_doc1_00042
  strategy_id   TEXT NOT NULL,             -- 'A' | 'B' | 'C'
  document_id   TEXT NOT NULL,
  source        TEXT NOT NULL,             -- path hoặc title ngắn
  page          INTEGER NOT NULL,
  text          TEXT NOT NULL,             -- text đưa vào embed
  chunk_type    TEXT NOT NULL,             -- 'text' | 'figure' | 'table'
  section       TEXT,                      -- field filter chính
  figure_id     TEXT REFERENCES figures(figure_id),   -- NULL nếu không phải figure
  table_id      TEXT REFERENCES tables(table_id),     -- NULL nếu không phải table
  char_count    INTEGER,
  created_at    TEXT,
  CHECK (
    (chunk_type = 'text'   AND figure_id IS NULL AND table_id IS NULL) OR
    (chunk_type = 'figure' AND figure_id IS NOT NULL AND table_id IS NULL) OR
    (chunk_type = 'table'  AND table_id IS NOT NULL AND figure_id IS NULL)
  )
);
CREATE INDEX idx_chunks_strategy ON chunks(strategy_id);
CREATE INDEX idx_chunks_section  ON chunks(strategy_id, section);
CREATE INDEX idx_chunks_type     ON chunks(strategy_id, chunk_type);

-- 6) Benchmark gold
CREATE TABLE questions (
  id               TEXT PRIMARY KEY,
  split            TEXT NOT NULL,          -- 'dev' | 'test'
  question         TEXT NOT NULL,
  expected_answer  TEXT NOT NULL,
  gold_source_id   TEXT NOT NULL,
  gold_page        INTEGER NOT NULL,
  evidence_quote   TEXT NOT NULL,
  filter_json      TEXT,                   -- {"section":"..."} hoặc NULL
  question_type    TEXT                    -- direct/paraphrase/...
);

-- 7) Trace retrieval
CREATE TABLE retrieval_traces (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT NOT NULL,
  strategy_id   TEXT NOT NULL,
  question_id   TEXT NOT NULL,
  rank          INTEGER NOT NULL,          -- 1..3
  chunk_id      TEXT NOT NULL,
  score         REAL,
  hit           INTEGER,                   -- 0/1 sau khi chấm
  UNIQUE(run_id, strategy_id, question_id, rank)
);
```

### Thay đổi schema liên quan table

| Chỗ | Trước | Sau |
|---|---|---|
| Entity riêng | (không có) | Thêm bảng **`tables`** (`markdown`, `table_label`, `title`, …) |
| `chunks.chunk_type` | `text \| figure` | `text \| figure \| table` |
| `chunks.table_id` | (không có) | FK nullable → `tables` |
| CHECK | (không có) | Đảm bảo đúng 1 trong 3 loại + FK khớp |
| Index | strategy, section | + `idx_chunks_type` |

Filter metadata chính vẫn là **`section`**.  
`chunk_type` / `table_id` / `figure_id` chủ yếu để truy vết và phân tích, có thể filter phụ.

## Design collection Chroma

Một collection / chiến lược:

| Collection | Nội dung |
|---|---|
| `chunks_A` | fixed-size (body) + figure/table unit riêng |
| `chunks_B` | heading/paragraph (body) + figure/table unit riêng |
| `chunks_C` | giống B + query-time filter |

Mỗi vector (embedding **đã tính sẵn** từ MiniLM, không nhờ Chroma embed):

```text
id:        = chunk_id (trùng SQLite)
embedding: 384-dim từ all-MiniLM-L6-v2
document:  = text embed  (với table = markdown đầy đủ)
metadata:  {
  document_id, source, page,
  section,           # filter chính
  chunk_type,        # text | figure | table
  strategy_id,
  figure_id,         # "" nếu không phải figure (Chroma không thích null)
  table_id           # "" nếu không phải table
}
```

Persist path: `data/chroma/`  
Query C: `where={"section": "<giá trị từ câu hỏi>"}` khi có filter; không có filter thì query như B.

**Không cần** collection/`index` riêng cho table — table là chunk cùng collection, phân biệt bằng `chunk_type` + `table_id`.

## Xử lý ảnh / figure trong pipeline

### Nguyên tắc

- Chỉ cắt/lưu PNG **không đủ** để search bằng embedding text.
- Phải tạo **chunk text** từ caption (và mô tả/OCR ngắn nếu cần), rồi embed text đó.
- Ảnh là artifact truy vết (`image_path`); MiniLM **không** embed pixel.

### Luồng parse (PyMuPDF)

```
Với mỗi page:
  1. Lấy danh sách image (bbox trên trang)
  2. Lấy text blocks kèm tọa độ (y0, y1)
  3. Với mỗi image:
       a. Crop theo bbox → lưu PNG
       b. Tìm text block NẰM NGAY BÊN DƯỚI ảnh
          (cùng khoảng x, y ngay sau image.y1,
           match regex: ^Figure\s+\d+(\.\d+)?|^Fig\.\s+\d+)
       c. Caption = block đó (+ nối 1–2 dòng wrap nếu cần)
       d. Ghi figures row
  4. Body text = text blocks KHÔNG nằm trong bbox ảnh
                 và KHÔNG phải caption đã gắn figure
                 và KHÔNG thuộc vùng table đã tách
  5. Lưu pages.text = body text sạch
```

### Heuristic caption

- Regex: `^(Figure|Fig\.?)\s+\d+(\.\d+)?[:.\s]`
- Khoảng dọc: ~0–80pt dưới đáy ảnh
- Cùng cột: overlap ngang với bbox ảnh ≥ 50%
- Không match: vẫn lưu ảnh; `caption = "[uncaptioned figure on page N]"` + log audit

## Xử lý table trong pipeline

### Nguyên tắc

- **Không** cắt ảnh / OCR table như figure.
- Extract cấu trúc → **Markdown** → lưu `tables.markdown` → embed chuỗi đó.
- Tách table khỏi body trước khi chạy chunker A/B (tránh fixed-size cắt ngang hàng).

### Luồng parse

```
Với mỗi page (sau hoặc song song figure):
  1. Tìm title: ^(TABLE|Table)\s+\d+(\.\d+)?
  2. Dùng page.find_tables() (PyMuPDF) hoặc heuristic vùng dưới title
  3. Rows → Markdown (| col | col | ...)
  4. Ghi tables row: label, title, markdown, page, bbox
  5. Strip vùng table (+ dòng title) khỏi body text
```

### Khi chunk / embed

| Loại | `text` đưa vào embed | SQLite / metadata |
|---|---|---|
| Text chunk | đoạn body | `chunk_type=text`, `figure_id`/`table_id` = NULL |
| Figure chunk | `"{figure_label}. {caption}"` (+ optional `ocr_text`) | `chunk_type=figure`, `figure_id` set |
| Table chunk | `"{table_label} {title}\n{markdown}"` | `chunk_type=table`, `table_id` set |

Figure và table thường thành **1 chunk riêng / đơn vị**, không cắt nhỏ thêm.  
Chunker text chỉ chạy trên `PageText` đã strip figure + table.

## Ba cấu hình chiến lược (nhắc lại)

| Cấu hình | Chunk | Metadata / truy xuất |
|---|---|---|
| A | Kích thước cố định (vd 500 ký tự, overlap 50) trên body | Có source/page; không filter |
| B | Theo heading/đoạn trên body | Cùng metadata; không filter |
| C | Giống B | Thêm filter theo `section` khi câu hỏi có filter |

Figure/table unit gắn vào cả A/B/C (cùng nội dung unit; khác `strategy_id` / `chunk_id` nếu cần tách theo chiến lược).

## Thông số chunking đã chốt (Wiley DDBMS Ch.5)

- Normalize **NFKC** trước mọi regex.
- **A** (body only): size=500, overlap=80, min_last_chunk=100; `section`=nearest prior heading; `chunk_type=text`
- **B** (body only): heading 2-line `^5(?:\.\d+){1,3}$` + title (Bold ~10pt); pack blocks max=1000 min=200; oversize → sentence split overlap=100
- Figure/Table units giống A và B: `^Figure\s+5\.\d+$`+caption; `^TABLE\s+5\.\d+`+markdown
- **C** = chunks B + query filter `metadata.section`

## Plan triển khai (thứ tự)

1. Ghi stack vào file này (done; đã bổ sung table)
2. Tạo schema SQLite + helper connect/migrate — **done** (`starter/src/db.py`)
3. Ingestion: PDF → `pages` (body) + `figures` + `tables` — **done** (`ingestion.py`)
4. Figure pipeline → `figures/` + bảng `figures` — **done**
5. Table pipeline → Markdown + bảng `tables` — **done**
6. Chunking A/B (+ figure/table unit) → bảng `chunks` — **done** (`chunking.py`)
7. Embed MiniLM → upsert Chroma 3 collection — **done** (`embeddings.py`, `store.py`, `run.py`)
8. Benchmark JSON → `questions`; runner top-3 → `retrieval_traces` — **wired** (cần điền `data/questions.json`)
9. Metrics + báo cáo theo 5 tiêu chí BENCHMARK.md — **pending** (sau khi có gold questions)
