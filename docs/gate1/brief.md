# PHẦN 1 — BRIEF (1 TRANG)

## 1.1 Vấn đề

Imitation learning chỉ học được từ **demonstration do người điều khiển tạo ra**. Nhưng đội không có công cụ để tạo ra chúng. Ba painpoint cụ thể:

| #            | Nỗi đau                                                               | Hệ quả thực tế                                                                                                                                               |
| ------------ | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **P1** | Không có cách điều khiển robot mô phỏng một cách thuận tiện | Không ai thu được demo. Việc thu dữ liệu không bắt đầu được.                                                                                       |
| **P2** | Không ghi được**đồng bộ** observation ↔ action            | Dữ liệu thu ra*trông có vẻ đúng* nhưng lệch pha vài frame. Model học sai, và **không ai phát hiện ra** cho tới lúc training thất bại. |
| **P3** | Không có bước kiểm duyệt trước khi đưa vào tập huấn luyện | Demo hỏng lẫn vào dữ liệu tốt. Không truy được mẫu nào do ai tạo, ai duyệt.                                                                        |

> **Nỗi đau lớn nhất không phải "thiếu dữ liệu" mà là "có dữ liệu nhưng không biết nó tốt hay xấu".** Một tập dữ liệu không đo được chất lượng thì tương đương không có.

## 1.2 Giải pháp

Một **nền tảng web hoàn chỉnh** đưa dữ liệu đi trọn vòng đời, từ tay người điều khiển tới con số success rate:

> Điều khiển robot mô phỏng qua trình duyệt → ghi **đồng bộ** observation + action ở 30 Hz → người kiểm duyệt xem lại, cắt, gán nhãn, duyệt → xuất ra định dạng chuẩn ngành, quản lý phiên bản → huấn luyện behavior cloning → **đo success rate ngược lại trong simulator**.

Vòng lặp này khép kín: con số ở cuối chính là thước đo cho chất lượng dữ liệu ở đầu.

## 1.3 Đối tượng mục tiêu

**Người dùng trực tiếp** — đội nghiên cứu robot learning quy mô nhỏ (3–10 người), chưa có hạ tầng thu dữ liệu riêng:

| Vai trò           | Là ai                               | Dùng để làm gì                                                                                                            |
| ------------------ | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| **Operator** | Kỹ sư thu dữ liệu   | Điều khiển robot, ghi demonstration. Chỉ thấy bản ghi của chính mình.                                                 |
| **Reviewer** | Kỹ sư ML phụ trách chất lượng | Cắt, gán nhãn, duyệt/từ chối, xuất dataset, huấn luyện, đọc kết quả.**Không được điều khiển robot.** |
| **Admin**    | Người vận hành hệ thống        | Toàn quyền + quản lý tài khoản và vai trò.                                                                             |

> Tách Operator khỏi Reviewer là điều làm cho bước kiểm duyệt **có ý nghĩa**: người duyệt một bản ghi không phải là người tạo ra nó.
