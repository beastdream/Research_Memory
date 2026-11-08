# ScienceDirect LLM Master Dataset & Enriched V2 — ResearchMemory

## 1. Tổng quan (Overview)

ScienceDirect LLM Master Dataset là nguồn dữ liệu khoa học độc lập được tích hợp vào dự án **ResearchMemory**. Dữ liệu này được xây dựng dành riêng cho các tác vụ nghiên cứu khoa học, Retrieval-Augmented Generation (RAG), Long-Term Memory, và Adaptive Memory.

### Quy mô và Trạng thái hoàn thành:
- **Tổng số bài báo khoa học chuẩn hóa:** **306 bài báo** (100% DOI đã xác minh qua Crossref Official Registry).
- **Trạng thái Toàn văn (Full-Text Status):**
  - **Complete Full-Text:** **259 bài** (84.6%) — toàn văn hoàn chỉnh qua multi-section Publisher XML hoặc multi-page PDF layout.
  - **Partial Full-Text:** **8 bài** (2.6%) — cấu trúc section trích xuất được một phần.
  - **Metadata-Only:** **39 bài** (12.7%) — metadata, abstract và canonical text theo phân cấp quyền truy cập.
- **Tập dữ liệu kép (Dual-Tier Datasets):**
  1. **Master Dataset:** `datasets/sciencedirect/master/sciencedirect_master.jsonl` tuân thủ JSON Schema Draft 2020-12.
  2. **Enriched Dataset v2:** `datasets/sciencedirect/enriched/sciencedirect_enriched_v2.jsonl` và 306 file JSON chi tiết tại `datasets/sciencedirect/enriched/papers/*.json` theo chuẩn `memorybank_enriched_v2.json` (sections, stable paragraph IDs, figures, tables, citation markers, equations, research metadata).

### Nguyên tắc cốt lõi:
1. **Hoàn toàn độc lập:** Lưu trữ và quản lý trong thư mục `datasets/sciencedirect/` và schema PostgreSQL `sciencedirect`. Tuyệt đối không ghi đè, hòa trộn hay làm thay đổi dataset arXiv, QASPER, SciFact, unarXive hiện tại.
2. **100% DOI đã xác minh (Verified DOI Invariant):** Mọi bài báo được chấp nhận vào Master Dataset chính bắt buộc phải có DOI hợp lệ, tồn tại trên registry (Crossref / Handle resolver), và được xác minh khớp tiêu đề, tác giả và năm xuất bản.
3. **Tuân thủ bản quyền & Giấy phép (TDM / License Governance):**
   - Chỉ thu thập toàn văn qua API chính thức của Elsevier (Article Retrieval API) hoặc PDF truy cập mở hợp lệ.
   - Tuyệt đối không screen-scraping, bypass CAPTCHA, hay bypass paywall.
   - Không bịa đặt nội dung full text, figures, tables, references hoặc synthetic QAs.
4. **Pipeline Khôi phục & Chuẩn hóa Đa nguồn (Multi-Source Repair):**
   - Tự động khôi phục abstract bị thiếu qua Publisher XML -> Crossref Registry -> PyMuPDF Layout Extraction.
   - Chuẩn hóa danh sách tác giả đầy đủ từ Crossref Official Registry và Publisher XML.
   - Phát hiện PDF xem trước 1 trang (preview) để tự động fallback sang toàn văn XML đa section.
   - Đồng bộ hóa trường `text` chuẩn mực với cấu trúc section và paragraph identifiers.

---

## 2. Cấu trúc thư mục (File Structure)

```
d:\ResearchMemory\
├── datasets\
│   └── sciencedirect\
│       ├── master\
│       │   └── sciencedirect_master.jsonl      # Master Dataset chuẩn hóa (306 bài)
│       ├── enriched\
│       │   ├── sciencedirect_enriched_v2.jsonl # V2 Enriched Dataset chuẩn hóa (306 bài)
│       │   └── papers\                         # 306 file JSON enriched riêng lẻ từng bài
│       │       ├── 10.1016_j.aei.2026.105154.json
│       │       └── ...
│       ├── manifests\
│       │   ├── candidates.jsonl                # Danh sách bài ứng viên ban đầu
│       │   ├── accepted.jsonl                  # Các bài đã chấp nhận vào dataset
│       │   ├── download_status.jsonl           # Trạng thái tải chi tiết từng bài
│       │   └── repair_checkpoint.json          # Checkpoint kiểm soát tiến trình repair (306 DOIs)
│       ├── metadata\
│       │   ├── candidates.jsonl                # Ứng viên metadata từ Elsevier Search API
│       │   └── candidates_verified.jsonl       # Ứng viên có DOI đã xác minh qua Crossref
│       ├── backup\                             # Bản lưu trữ trước các đợt repair lớn
│       │   └── backup_20261028_184032\
│       └── rejected\
│           └── invalid_or_unverified_doi.jsonl # Các bài bị từ chối do DOI lỗi/mismatch/unverified
├── storage\
│   └── sciencedirect\
│       ├── xml\                                # Raw publisher XML có cấu trúc
│       ├── crossref\                           # Metadata JSON cache từ Crossref API
│       ├── fulltext\                           # Raw full-text XML
│       ├── pdf\                                # PDF chính thức (63 multi-page, 93 preview)
│       └── figures\                            # Figures/Tables đa phương tiện
├── schemas\
│   └── sciencedirect_master.schema.json        # JSON Schema chuẩn Draft 2020-12
├── app\
│   ├── ingestion\
│   │   ├── sciencedirect_client.py             # Client API Elsevier và Crossref DOI verification
│   │   ├── sciencedirect_parser.py             # Parser XML khoa học, clean abstract, topic quotas
│   │   ├── sciencedirect_enrichment.py         # Pipeline trích xuất V2 Enriched & PDF PyMuPDF
│   │   └── sciencedirect_schema.py             # Trình kiểm tra schema và ràng buộc toàn vẹn
│   ├── storage\
│   │   └── sciencedirect_postgres.py           # Module quản lý schema PostgreSQL riêng biệt
│   ├── dataset_inspector.py                    # Core logic tra cứu và adapter dataset
│   └── ui\
│       └── dataset_inspector.py                # Giao diện Streamlit Dataset Inspector
├── scripts\
│   ├── collect_sciencedirect.py                # CLI điều phối thu thập, enrich, báo cáo
│   ├── repair_sciencedirect.py                 # Pipeline kiểm toán, khôi phục và chuẩn hóa
│   ├── normalize_sciencedirect.py              # Script chuẩn hóa Master Dataset
│   └── audit_sciencedirect.py                  # Script kiểm tra chất lượng tự động
├── reports\
│   ├── sciencedirect_collection_report.json    # Báo cáo thu thập (JSON)
│   ├── sciencedirect_collection_report.md      # Báo cáo thu thập (Markdown)
│   ├── sciencedirect_repair_report.json        # Báo cáo khôi phục & chuẩn hóa (JSON)
│   └── sciencedirect_repair_report.md          # Báo cáo khôi phục & chuẩn hóa (Markdown)
├── tests\
│   └── test_sciencedirect_pipeline.py          # Bộ kiểm thử đơn vị và tích hợp (30 tests)
└── docs\
    └── SCIENCEDIRECT_DATASET.md                # Tài liệu kỹ thuật này
```

---

## 3. Phân bổ Chủ đề và Năm xuất bản (Topic & Year Distribution)

Quy mô thực tế đạt **306 bài báo khoa học** đã xác minh:

### Phân bổ theo Nhóm chủ đề:
| Nhóm chủ đề | Số lượng bài | Tỷ lệ | Từ khóa chính |
| :--- | :---: | :---: | :--- |
| **Group 1 — Large Language Models** | 62 | 20.3% | Large Language Models, LLM, Foundation Models, Generative AI, Transformer Language Models |
| **Group 2 — RAG and Information Retrieval** | 72 | 23.5% | Retrieval-Augmented Generation, Dense Retrieval, Neural Information Retrieval, Reranking |
| **Group 3 — AI Agents and Long-Term Memory** | 72 | 23.5% | LLM Agents, Autonomous Agents, Agentic Memory, Long-Term Memory, Adaptive Memory |
| **Group 4 — Hallucination and Faithfulness** | 50 | 16.3% | LLM Hallucination, Faithfulness, Factual Consistency, AI Verification, Grounded Generation |
| **Group 5 — Scientific NLP and QA** | 50 | 16.3% | Scientific Question Answering, Scientific Literature Analysis, Long Document Understanding |
| **TỔNG CỘNG** | **306** | **100%** | |

### Phân bổ theo Năm xuất bản:
| Năm | Số bài | Năm | Số bài |
| :---: | :---: | :---: | :---: |
| **2026** | 195 | **2022** | 8 |
| **2025** | 42 | **2021** | 7 |
| **2024** | 20 | **2020** | 11 |
| **2023** | 10 | **2019-2018** | 13 |

---

## 4. Pipeline Khôi phục & Chuẩn hóa Đa nguồn (Standardization & Repair)

Nhằm đảm bảo chất lượng khoa học cao nhất và giải quyết triệt để các hạn chế khi thu thập API, pipeline `scripts/repair_sciencedirect.py` thực hiện:

### 4 Vấn đề kỹ thuật cốt lõi đã giải quyết:
1. **Khôi phục Tóm tắt (Empty Abstracts Resolution):**
   - *Nguyên nhân:* Một số bản ghi Elsevier API ban đầu trả về trường abstract rỗng do định dạng XML phức hợp hoặc thẻ JATS chưa được bóc tách.
   - *Giải pháp đa nguồn:* Khôi phục theo thứ tự ưu tiên: Publisher XML (`clean_abstract_xml`) -> Crossref Official Registry (`extract_abstract_from_crossref`) -> PyMuPDF Layout Extraction (quét 2 trang đầu).
   - *Kết quả:* Khôi phục thành công **280 tóm tắt** (từ 304 bài rỗng giảm xuống còn 24 bài — 24 bài này là các editorial, letter to editor hoặc conference abstract không có abstract độc lập trong xuất bản phẩm).
2. **Mở rộng Danh sách Tác giả (Incomplete Authors):**
   - *Nguyên nhân:* Bản ghi metadata ban đầu chỉ giữ tác giả đầu tiên (`dc:creator`).
   - *Giải pháp:* Truy vấn Crossref API và Publisher XML bóc tách toàn bộ danh sách đồng tác giả (`given` + `family`), loại bỏ ký tự rác/affiliation marker, khử trùng lặp.
   - *Kết quả:* Mở rộng thành công **150 danh sách đồng tác giả** (từ 183 bài chỉ có 1 tác giả giảm xuống còn 33 bài — gồm 32 bài thực sự chỉ có 1 tác giả và 1 bản tin công nghiệp).
3. **Phát hiện PDF Xem trước 1 trang & Fallback Toàn văn XML (1-Page Preview Gate):**
   - *Nguyên nhân:* Một số bài tải về PDF chỉ là trang preview/bìa 1 trang của Elsevier.
   - *Giải pháp:* Module `get_pdf_info()` sử dụng PyMuPDF kiểm tra số trang thực tế. Nếu `page_count == 1`, hệ thống không trích xuất văn bản sơ sài từ PDF mà tự động fallback sang phân tích Publisher XML (`parse_elsevier_xml`) đa section với đầy đủ các đoạn văn (`para`).
   - *Kết quả:* Đạt **259 bài Toàn văn Hoàn chỉnh (Complete Full-Text)** và 8 bài Partial.
4. **Chuẩn hóa Trường Văn bản Đồng nhất (Canonical `text` Field):**
   - *Giải pháp:* Tổng hợp trường `text` chính tắc từ Tiêu đề + Abstract + Section Headings + Các đoạn văn theo đúng thứ tự đọc, gán paragraph IDs ổn định cho tác vụ Retrieval & RAG.
   - *Kết quả:* **306/306 bài (100%)** sở hữu trường `text` nhất quán, tương thích hoàn toàn với schema Master Dataset V2.

### Bảng đối chiếu Trước và Sau Repair:
| Chỉ số | Trước Repair | Sau Repair | Cải thiện |
| :--- | :---: | :---: | :---: |
| **Tổng số bài báo** | 306 | 306 | 100% bảo toàn |
| **DOI đã xác minh** | 306 | 306 | 100% Pass |
| **Tóm tắt bị rỗng** | 304 | 24 | **+280 tóm tắt khôi phục** |
| **Bài chỉ có 0-1 tác giả** | 183 | 33 | **+150 danh sách tác giả mở rộng** |
| **Toàn văn Complete** | 2 | 259 | **+257 bài toàn văn hoàn chỉnh** |
| **Trường `text` chính tắc** | Chưa nhất quán | 306 (100%) | 100% chuẩn hóa |
| **Lỗi Schema Validation** | 0 | 0 | 100% Draft 2020-12 Pass |

---

## 5. Hướng dẫn Lệnh CLI (PowerShell)

Mọi thao tác đều thực hiện trực tiếp từ thư mục gốc `d:\ResearchMemory`:

### Nhóm lệnh Điều phối Pipeline chính:
```powershell
# 1. Kiểm tra môi trường, API key, kết nối Elsevier & Crossref
.venv\Scripts\python scripts/collect_sciencedirect.py doctor

# 2. Thu thập bài báo mới với DOI verification & tải PDF
.venv\Scripts\python scripts/collect_sciencedirect.py collect --add 150 --resume --doi-only --pdf-required

# 3. Xây dựng tập dữ liệu V2 Enriched Dataset
.venv\Scripts\python scripts/collect_sciencedirect.py enrich --resume

# 4. Xuất báo cáo kiểm định chất lượng thu thập
.venv\Scripts\python scripts/collect_sciencedirect.py report
```

### Nhóm lệnh Khôi phục & Chuẩn hóa (Repair CLI):
```powershell
# 1. Kiểm toán chất lượng hiện tại của Master Dataset
.venv\Scripts\python scripts/repair_sciencedirect.py audit --all

# 2. Thử nghiệm khôi phục một bài đơn lẻ (Dry-run)
.venv\Scripts\python scripts/repair_sciencedirect.py repair --doi 10.1016/j.aei.2026.105154 --dry-run

# 3. Chạy khôi phục toàn bộ dataset với backup và checkpoint tự động
.venv\Scripts\python scripts/repair_sciencedirect.py repair --all --resume --backup

# 4. Xác minh tính toàn vẹn và xuất báo cáo chuẩn hóa
.venv\Scripts\python scripts/repair_sciencedirect.py verify --all

# 5. Xuất báo cáo kiểm toán Markdown và JSON
.venv\Scripts\python scripts/repair_sciencedirect.py report
```

---

## 6. Khởi chạy Dataset Inspector Web UI

ResearchMemory cung cấp giao diện tương tác Streamlit cho phép duyệt cả Master Dataset và V2 Enriched Dataset:

```powershell
.venv\Scripts\python -m streamlit run app/ui/dataset_inspector.py
```

### Các tính năng trên giao diện:
- **Nguồn dữ liệu (Dataset Source):**
  - `ScienceDirect Master Dataset`: Xem dữ liệu chuẩn hóa chính `sciencedirect_master.jsonl`.
  - `ScienceDirect Enriched v2`: Xem chi tiết cấu trúc phần mục sections, paragraphs, figures, tables, citation markers từ `sciencedirect_enriched_v2.jsonl`.
  - `arXiv Master v2`: So sánh trực tiếp với kho lưu trữ arXiv.
  - `PostgreSQL sciencedirect`: Truy vấn trực tiếp từ cơ sở dữ liệu PostgreSQL.
- **Bộ lọc mạnh mẽ:** Lọc theo Title, DOI, Canonical ID, Journal, Năm xuất bản (2018-2026), Nhóm chủ đề (5 topic groups), Trạng thái toàn văn (`complete`, `partial`, `metadata_only`), Trạng thái DOI (`verified`).
- **Tab Chi tiết Bài báo:** Metadata, Abstract, Full Text & Sections, Figures & Tables, References, QAs, Quality & Rights.

---

## 7. Schema và Kiến trúc Lưu trữ PostgreSQL

### Schema PostgreSQL Độc lập:
Dataset ScienceDirect hoạt động hoàn toàn độc lập trong namespace schema `sciencedirect`:
- `sciencedirect.papers`: Lưu trữ metadata, title, abstract, authors, canonical_id, jsonb snapshot.
- `sciencedirect.sections`: Cấu trúc section và heading.
- `sciencedirect.references`: Tài liệu tham khảo và resolution status.
- `sciencedirect.figures_and_tables`: Bảng và hình ảnh khoa học trích xuất.
- `sciencedirect.paper_enrichment`: Lưu trữ bản ghi V2 Enriched hoàn chỉnh dạng JSONB.

### Kiểm thử Tự động (Automated Tests):
Bộ kiểm thử tại `tests/test_sciencedirect_pipeline.py` gồm **30 test cases** kiểm tra toàn diện:
- Chuẩn hóa và xác thực cú pháp DOI.
- So khớp tiêu đề và xác minh Crossref REST API (200, 404, mismatch).
- Bóc tách Publisher XML và trích xuất abstract sạch.
- Đánh giá PDF đa trang và preview 1 trang qua PyMuPDF.
- Kiểm tra tính tuân thủ JSON Schema Draft 2020-12 và V2 Enriched contract.
- Thao tác upsert idempotent vào PostgreSQL.

Chạy kiểm thử:
```powershell
.venv\Scripts\pytest tests/test_sciencedirect_pipeline.py -v
```
