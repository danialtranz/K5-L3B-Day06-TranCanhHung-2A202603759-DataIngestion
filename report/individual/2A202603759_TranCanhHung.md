# Báo cáo cá nhân — Day 6

**MSSV:** 2A202603759 · **Họ tên:** Trần Cảnh Hưng · **Lớp:** L3B

## Dữ liệu và pipeline
- Chủ đề, nguồn/quyền, số trang và quy tắc tính trang: Wiley DDBMS Chapter 5 (Rahimi & Haug, 2010), PDF 63 trang vật lý; `document_id=wiley_ddbms_ch5`; đếm trang PyMuPDF 1-based. Chi tiết: `data/manifest.json`. PDF sách có bản quyền — repo không phân phối file PDF; dùng bản được phép truy cập local.
- Chuẩn hóa, lỗi và giới hạn: NFKC; tách figure/table khỏi body; heading 2-dòng; một số leaf heading bị gộp section cha trong metadata.
- Embedding model/provider/version/số chiều, vector store/version, metric, lệnh chạy và chi phí: `sentence-transformers/all-MiniLM-L6-v2` (384-d), Chroma persistent cosine, SQLite; chi phí API = 0 (local). Benchmark: `starter/scripts/run_benchmark_only.py` (từ thư mục `starter/`).
- Metadata bắt buộc, trường lọc bổ sung, nguồn giá trị và lý do chọn: `document_id`, `source`, `page`; filter chính `section` (từ heading parse).

## Cấu hình và benchmark
- A / B / C (tham số cụ thể, khác biệt duy nhất B–C): A fixed-size **500/80**; B heading + paragraph pack **max 1000 / min 200**; C = B + query filter `section` khi câu có filter.
- ≥20 câu: **25 câu** trong `starter/data/questions.json` — **20 dev / 5 test**; **6 câu filter** (q06,q15,q17,q18,q24,q25) trong đó **2 test** (q24,q25); evidence đã verify trên PDF + chunk SQLite.
- Tiêu chí chọn chiến lược đã chốt trước khi xem test: ưu tiên Evidence Hit@3, sau đó Hit@1, rồi số chunk/thời gian.

## Kết quả
| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 8/20 | 13/20 | 419 | index ~9.0s | 0 |
| A | test | 2/5 | 3/5 | 419 | (cùng A) | 0 |
| B | dev | 11/20 | 16/20 | 242 | index ~7.0s | 0 |
| B | test | 2/5 | 4/5 | 242 | (cùng B) | 0 |
| C | dev | 12/20 | 16/20 | 242 | index ~7.2s | 0 |
| C | test | 3/5 | 4/5 | 242 | (cùng C) | 0 |

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | 1.30 /2 (n=10) | 15/15 | N/A (không áp filter) | 0:0, 1:2, 2:3 · TB 1.60 /2 |
| B | 1.80 /2 (n=10) | 15/15 | N/A | 0:0, 1:1, 2:4 · TB 1.80 /2 |
| C | 1.80 /2 (n=10) | 15/15 | 6/6 | 0:0, 1:1, 2:4 · TB 1.80 /2 |

Đường dẫn trace, nhãn thủ công và cách chọn mẫu:
- Trace: `starter/results/traces_20261113T212140Z.json`
- Summary: `starter/results/summary_latest.json` / `summary_20261113T212140Z.json`
- Gold: `starter/data/questions.json`
- Experiment log: `artifacts/experiment_log.csv`
- Nhãn audit: `starter/results/audit_coherence.csv`, `audit_metadata.csv`, `audit_filter.csv`, `audit_grounding.csv`, `audit_summary.json`
- Coherence: cố định 10 trang nguồn trước khi chấm — pages `1,2,6,7,9,10,14,19,25,27`; mỗi strategy lấy 1 text chunk covering page từ SQLite.
- Metadata: 5 câu test × top-3; kiểm `source` / `page` / `document_id` / `section`.
- Filter: 6 câu có filter; chỉ C áp `section` (exact match Chroma); A/B = N/A.
- Grounding: 5 câu test (q21–q25) × mỗi strategy, thang 0–2 trên sufficiency của top-3 (không chấm câu trả lời LLM).
- Người chấm: tác giả (~70–80% nhãn tự đối chiếu traces); AI hỗ trợ soạn nháp (~20–30%); tác giả duyệt trước nộp. Rủi ro thiên lệch tự chấm.

## Phân tích và kết luận
- So sánh A–B và B–C; 2 ví dụ sai có ID câu và nguồn:
  - **A–B:** B nâng Evidence Hit@3 (dev 16/20 vs A 13/20) và coherence (1.80 vs 1.30). Ví dụ **q03** (ACID): evidence nằm trong chunk B nhưng A fixed-size dễ miss top-3. Coherence A thường cắt giữa câu (vd page 2, 6).
  - **B–C:** Hit@3 gần giống trên tổng; C cải thiện Hit@1 test (3/5 vs 2/5) và filter 6/6. Ví dụ **q24**, **q25** — filter `section` loại nhiễu ngoài phạm vi.
  - Fail điển hình chung: **q09** (blind write), **q16** (locking vs timestamping), **q22** (TO philosophy) — paraphrase/định nghĩa bị top-3 lệch; grounding q22 = 1 trên cả ba strategy.
- Chiến lược chọn, đánh đổi, điều kiện áp dụng: ưu tiên **C** khi câu hỏi có `section`; còn lại **B** tốt hơn A về Hit@3 và coherence. A đơn giản hơn nhưng coherence/evidence dễ vỡ. Metadata đủ trên mẫu test; hạn chế: leaf section đôi khi gắn nhầm cha, filter exact không gồm subsection con trừ khi gán đúng label.
- Hạn chế của dữ liệu, nhãn, test nhỏ, đo thời gian/chi phí: test chỉ 5 câu; section leaf đôi khi gộp vào cha; figure caption nhiều `[uncaptioned]`; mẫu coherence 10 trang / grounding 5 câu không suy rộng ngoài corpus; PDF gốc không nộp kèm (bản quyền).
- Nếu làm lại hoặc mở rộng, cải tiến gì?: tinh chỉnh gắn leaf section; rút ngắn evidence; hybrid BM25; mở rộng test set.
