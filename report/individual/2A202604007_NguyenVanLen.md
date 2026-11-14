# Báo cáo cá nhân — Day 06: Data Ingestion Engineering

## Thông tin học viên và bài nộp

- Họ tên: Nguyễn Văn Lên
- MSSV: 2A202604007 (suy ra từ tên thư mục)
- Lớp: L3B (theo thông tin học viên cung cấp)
- Corpus: *Pro Git*, ấn bản thứ hai, chương 1–3; 95 trang in có nội dung
- Hình thức nộp: Repo cá nhân
- Repo: https://github.com/len2412/K5-L3B-Day06-NguyenVanLen-2A202604007-DataIngestionEngineering
- Commit chốt bài: Chưa cung cấp

## Dữ liệu và pipeline

Corpus là bản tiếng Anh của *Pro Git*, ấn bản thứ hai, tác giả Scott Chacon và Ben Straub, phiên bản 2.1.450. Manifest ghi nguồn sách và dự án GitHub, các trang in được chọn (trang 10–104 thuộc chương 1–3), ánh xạ sang trang vật lý trong PDF, số ký tự trích xuất và SHA-256 của PDF. Sau khi loại phần đầu sách và các chương ngoài phạm vi, manifest tính được 95 trang có nội dung. Nguồn công bố giấy phép CC BY-NC-SA 3.0 Unported. Khi phân phối hoặc tạo bản phái sinh cần ghi công và giữ cùng giấy phép; không được dùng cho mục đích thương mại. Tài liệu của bên thứ ba nằm trong sách chưa được rà soát riêng.

Loader dùng pypdf, chuẩn hóa Unicode NFKC, làm sạch khoảng trắng và ánh xạ trang theo manifest. Pipeline báo lỗi nếu thiếu PDF, sai số trang, ánh xạ không hợp lệ hoặc trang được chọn không có văn bản trích xuất; không âm thầm bỏ qua trang. Lần chạy mới nhất trích xuất đủ 95 trang khai báo. Bộ kiểm tra benchmark đối chiếu từng trích dẫn evidence với văn bản đã chuẩn hóa trên đúng trang in và hoàn tất thành công cho cả 20 câu. Việc này xác nhận trích dẫn có mặt và trang được ánh xạ đúng, nhưng không thay cho việc đánh giá đáp án có đủ ý hay không; học viên vẫn cần rà soát câu hỏi và đáp án.

Embedding dùng cục bộ `BAAI/bge-large-en-v1.5`, 1.024 chiều, chạy CPU qua Sentence Transformers; văn bản tài liệu không thêm tiền tố, còn truy vấn dùng hướng dẫn tìm kiếm được khuyến nghị cho BGE. Chi phí ghi nhận: 0 USD. Vector store là PostgreSQL 16 với pgvector 0.8.7, cosine distance và tìm kiếm exact bằng sắp xếp khoảng cách. Metadata gồm document ID, nguồn, trang in, page ID, trang PDF vật lý, số/tên chương, phương pháp và chỉ số chunk, cùng offset ký tự. Trường lọc benchmark `chapter_number` được lấy từ manifest. Compose chỉ mở cổng trên localhost; mật khẩu mẫu chỉ dùng cho môi trường lab cục bộ.

## Thiết kế thí nghiệm

Benchmark có 20 câu: q01–q15 thuộc dev, q16–q20 thuộc test; có sáu câu kèm filter chương (ba dev, ba test). Cách chia được ghi trong file benchmark. Câu hỏi gồm dạng trực tiếp, diễn đạt lại và câu có điều kiện. Pipeline từ chối evidence không có trên trang gold hoặc giá trị filter không tồn tại trong metadata. Cả ba lần chạy đã lưu đều vượt qua bước kiểm tra này.

| Chiến lược | Chia chunk và truy xuất |
|---|---|
| A | Cửa sổ cố định 500 ký tự, chồng lấp 50 ký tự; không filter |
| B | Gom theo đoạn văn, tối đa 1.200 ký tự; không filter |
| C | Dùng lại chính xác chunk và vector của B; chỉ áp dụng filter chương được nêu trong câu hỏi |

A–B chỉ thay cách chia chunk. B–C chỉ thay filter tại thời điểm truy vấn. Mã dùng cùng corpus, model, benchmark và vector store cho cả ba cấu hình. Hồ sơ thí nghiệm không chứng minh tiêu chí chọn cấu hình đã được chốt trước khi xem test; hai lần chạy đã lưu đều có kết quả test. Vì vậy, số test dưới đây chỉ mang tính mô tả, không phải holdout chưa đụng tới. Nếu chạy tiếp, cần chốt lựa chọn dựa trên dev rồi đánh giá trên một test set mới.

## Kết quả truy xuất

Lần chạy mới nhất: `artifacts/run_20261114T054151456688Z/`. Hit@1 và Evidence Hit@3 chỉ tính là trúng khi chunk truy xuất thuộc đúng nguồn/trang gold và có chứa evidence đã kiểm chứng.

| Chiến lược | Tập | Hit@1 | Evidence Hit@3 | Số chunk | Thời gian embedding (giây) | Thời gian upsert (giây) |
|---|---|---:|---:|---:|---:|---:|
| A | Dev | 8/15 | 8/15 | 375 | 84.37 | 0.35 |
| A | Test | 3/5 | 5/5 | 375 | 84.37 | 0.35 |
| B | Dev | 9/15 | 11/15 | 177 | 77.38 | 0.19 |
| B | Test | 0/5 | 4/5 | 177 | 77.38 | 0.19 |
| C | Dev | 9/15 | 11/15 | 177 | 77.38 (dùng chung với B) | 0.19 |
| C | Test | 0/5 | 5/5 | 177 | 77.38 (dùng chung với B) | 0.19 |

Trên dev, B có thêm 3 câu Evidence Hit@3 so với A trong khi giảm 198 chunk. Kết quả test không đồng nhất: A có Hit@1 cao hơn, còn B thiếu một evidence trong top-3. B và C giống nhau trên dev; trên test, filter của C giúp tìm evidence gold cho thêm một câu (q17). Mẫu quá nhỏ để kết luận chiến lược nào tốt hơn nói chung. Nếu chỉ căn cứ dev, B/C có lợi thế về Evidence Hit@3; C bổ sung giới hạn phạm vi truy vấn mà không cần tạo thêm embedding cho tài liệu.

## Rà soát thủ công có AI hỗ trợ

Biểu mẫu kết quả nằm trong `manual_test_review.jsonl` và `coherence_review.csv`. Đây là lượt đánh giá ban đầu có AI hỗ trợ, không phải đánh giá độc lập do con người thực hiện. Rubric yêu cầu học viên tự đọc các chunk được trích dẫn và xác nhận hoặc điều chỉnh nhãn trước khi nộp. Metadata được so sánh với nguồn/trang/chương do loader và trace ghi nhận. Trong 45 bản ghi top-3 của test (5 câu × 3 chiến lược × 3 kết quả), trường nguồn và trang có mặt, khớp manifest: 45/45. Với C, cả ba câu test có filter đều trả về chương 3; cả chín kết quả tương ứng tuân thủ filter: 9/9.

Chấm grounding cho năm câu test theo thang 0 = không có evidence dùng được, 1 = evidence thiếu ngữ cảnh, 2 = đủ evidence và truy được nguồn:

| Chiến lược | Điểm q16–q20 | Trung bình /2 | Ghi chú |
|---|---|---:|---|
| A | 2, 2, 2, 2, 2 | 2.0 | Top-3 đều có đoạn nguồn đủ dùng; với q16, evidence xếp thứ hai. |
| B | 2, 0, 2, 2, 2 | 1.6 | q17 là lỗi: top-3 không tìm được evidence về trạng thái detached HEAD. |
| C | 2, 2, 2, 2, 2 | 2.0 | Filter chương giúp đưa evidence q17 vào top-3. |

Tính mạch lạc được chấm tại 10 vị trí nguồn cố định (điểm giữa của 10 trang đầu tiên theo thứ tự), dùng thang 0–2 của rubric. Kết quả đánh giá ban đầu có AI hỗ trợ: A đạt 12/20 (1,2/2), B đạt 17/20 (1,7/2), C đạt 17/20 (1,7/2). Chunk cửa sổ cố định thường bắt đầu giữa từ hoặc giữa ý; chunk theo đoạn thường giữ được lời giải thích trọn vẹn hơn. Điểm và lý do từng mẫu ở `coherence_review.csv`. Học viên cần xác nhận hoặc sửa các nhãn này.

## Lỗi, giới hạn và kết luận

Một số lỗi truy xuất quan sát được: B bỏ lỡ q17 (evidence cần nêu cả trạng thái HEAD thông thường và detached HEAD); A và B xếp đoạn không chứa evidence gold lên đầu cho q16 dù đoạn đúng có trong top-3; B và C xếp nhầm đoạn lên đầu cho q20 nhưng đoạn giải thích fast-forward nằm thứ hai. Test chỉ có năm câu và đã được xem trong các lần chạy trước, nên các nhận xét này mang tính thăm dò. Mỗi câu hiện chỉ có một trích dẫn/trang gold, có thể khiến evidence tương đương ở nơi khác bị tính thiếu. Trích xuất PDF có thể làm sai nội dung hình hoặc bố cục; tài liệu bên thứ ba trong sách chưa được rà soát riêng.

Để tái lập, vào thư mục `starter/` sau khi cài requirements, chạy Docker Compose và xác nhận model đã có trong cache cục bộ, rồi chạy `python -m src.run`. Runner tạo thư mục kết quả theo thời gian và thay thế các dòng của từng chiến lược cho tài liệu đó khi upsert, tránh nhân đôi chunk khi chạy lại. Phiên bản phụ thuộc và image database được ghi trong `requirements.txt` và `docker-compose.yml`; thời gian chạy tùy phần cứng. AI được dùng để hỗ trợ soạn và rà soát báo cáo; học viên cần kiểm tra nhãn, hành vi mã và nội dung báo cáo, đồng thời có thể giải thích bài làm.

## Việc cần hoàn tất trước khi nộp

1. Học viên tự xác nhận hoặc sửa nhãn coherence, grounding test và metadata có AI hỗ trợ.
2. Commit chốt bài chưa được ghi trong `STUDENT.md`; bổ sung hash commit sẽ nộp và xác nhận repo mở được cho người chấm.
