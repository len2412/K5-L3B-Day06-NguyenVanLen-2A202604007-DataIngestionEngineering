# Starter · PostgreSQL + pgvector

Bộ khởi động **tùy chọn** cho lab cá nhân. Docker Compose tạo PostgreSQL + pgvector local. Pipeline mặc định dùng model BAAI đã có trong Hugging Face cache; vẫn có thể chọn Qdrant, Chroma hoặc vector store tương đương theo [GUIDE.md](../GUIDE.md).

## Cấu trúc

```text
starter/
├── docker-compose.yml     # PostgreSQL 16 + pgvector, chỉ mở cổng local
├── init.sql               # bật extension khi tạo database mới
├── requirements.txt       # psycopg, pypdf, python-dotenv, sentence-transformers
├── .env.example           # cấu hình mẫu; copy thành .env
├── scripts/check_setup.py # kiểm tra kết nối và extension (đã chạy được)
├── src/
│   ├── models.py          # cấu trúc dữ liệu mẫu
│   ├── ingestion.py       # đọc trang trong manifest, chuẩn hóa và giữ metadata
│   ├── chunking.py        # fixed windows và paragraph-aware chunks
│   ├── embeddings.py      # local BAAI BGE embeddings
│   ├── store.py           # lưu vector, metadata và filter trong pgvector
│   ├── evaluation.py      # Hit@1, Evidence Hit@3 và tổng hợp theo split
│   └── run.py             # chạy ingestion, A/B/C, trace và mẫu chấm tay
├── ../data/               # corpus, manifest và benchmark của project
└── ../artifacts/          # kết quả từng lần chạy
```

## 1. Cài và kiểm tra database

Cần Docker Desktop hoặc Docker Engine + Compose, Python **3.11–3.14**, và mạng để tải image/dependencies lần đầu. Các lệnh sau chạy từ thư mục `starter/`:

```bash
cd starter
python3 -m venv .venv
```

Kích hoạt `.venv`: macOS/Linux dùng `source .venv/bin/activate`; Windows PowerShell dùng `.venv\Scripts\Activate.ps1`. Sau đó:

```bash
python -m pip install -r requirements.txt
# macOS/Linux: cp .env.example .env
# Windows PowerShell: Copy-Item .env.example .env
docker compose -p day6_student_starter up -d --wait
python scripts/check_setup.py
```

Kỳ vọng: hiện tên database, phiên bản PostgreSQL và pgvector, sau đó `Setup OK`. Đây **chỉ là kiểm tra hạ tầng**, không phải kết quả benchmark. Docker dùng cổng `127.0.0.1:55433` và mật khẩu cố định **chỉ dùng local cho lab**; nếu cổng bận, sửa đồng thời port trong `docker-compose.yml` và `DATABASE_URL` trong `.env`. `init.sql` chỉ chạy khi volume mới được tạo; nếu database sẵn có chưa bật extension, xem log Docker và kiểm tra cấu hình thay vì xóa volume chứa dữ liệu.

Dừng container nhưng giữ dữ liệu:

```bash
docker compose -p day6_student_starter stop
```

## 2. Chọn embedding model

Starter hiện implement embedding local qua Sentence Transformers. `.env.example` chọn `BAAI/bge-large-en-v1.5`; model phải có trong Hugging Face cache vì pipeline chạy offline. Có thể đặt `EMBEDDING_MODEL=BAAI/bge-m3` nếu model đó đã được tải. API embedding chưa được implement trong starter. Ghi tên model và số chiều; dùng cùng model cho toàn bộ chiến lược. Query vectors và document vectors phải cùng model.

## 3. Hoàn thiện pipeline

1. Đặt PDF, manifest và benchmark trong thư mục `data/` ở project root. Đọc [GUIDE.md](../GUIDE.md) về **30 trang nội dung**, quyền dữ liệu và metadata.
2. Loader lấy các trang useful từ manifest, còn metadata chương được dùng cho filter. Câu benchmark cần có evidence chính xác ở trang gold; pipeline dừng và báo câu lỗi nếu không tìm thấy.
3. Xây ít nhất ba cấu hình; ví dụ A fixed-size, B theo đoạn, C như B nhưng có filter. Cần cùng corpus, model và benchmark; nạp lại không nhân đôi và xử lý chunk cũ khi dữ liệu đổi.
4. Tự viết ít nhất 20 câu hỏi kèm evidence, tách dev/test trước khi thử theo [BENCHMARK.md](../BENCHMARK.md). Lưu trace top-3 và báo cáo đủ năm tiêu chí.
5. Chạy `python -m src.run` từ thư mục `starter/`. Mỗi lần chạy tạo thư mục mới dưới project `artifacts/`, lưu `pdf_extraction.txt` ngay sau khi đọc PDF và `chunking_result.txt` ngay sau khi chia chunk; sau đó lưu traces, summary và file review. Hai file text vẫn còn nếu benchmark validation hoặc bước database tiếp theo thất bại.

Lưu code, cấu hình, kết quả và lệnh chạy để người chấm tái lập; không commit key, dữ liệu mật hoặc dữ liệu không có quyền chia sẻ.
