# Knowledge Types — Phân loại Tri thức

Knowledge Types là hệ thống taxonomy cho phép phân loại tài liệu theo mục đích và nội dung. Đây là cầu nối quan trọng giữa tài liệu thô và chất lượng wiki được tạo ra.

---

## Tại sao Knowledge Types quan trọng?

Khi LLM xử lý tài liệu, nó cần hiểu **loại tri thức** để:

1. **Trích xuất đúng thực thể**: SOP cần trích xuất "bước thực hiện", còn chính sách cần trích xuất "quy định, điều kiện"
2. **Định dạng wiki phù hợp**: Quy trình → numbered list; Định nghĩa → prose; So sánh → table
3. **Chọn slug convention**: Quy trình vận hành → `topic/`, Sản phẩm cụ thể → `entity/`
4. **Lọc theo MCP scope**: Employee chỉ thấy knowledge types được assign cho MCP token của họ

---

## Cấu trúc Knowledge Type

```
KnowledgeType
  id          UUID
  name        VARCHAR     # "An ninh mạng", "SOP", "Chính sách"
  slug        VARCHAR     # "an-ninh-mang", "sop", "chinh-sach"
  description TEXT        # LLM ĐỌC TRƯỜNG NÀY khi xử lý tài liệu
  icon        VARCHAR     # Material Symbols icon name
```

---

## Thiết kế Description hiệu quả

Description là phần **LLM đọc** để hiểu loại tài liệu. Hãy viết như đang hướng dẫn một chuyên gia mới.

### Template mẫu

```
[Tên type] là tài liệu về [chủ đề gì].

Loại tri thức cần trích xuất:
- [Loại thực thể 1] (vd: tên, định nghĩa)
- [Loại thực thể 2] (vd: bước thực hiện, điều kiện)
- [Loại thực thể 3] (vd: cảnh báo, ngoại lệ)

Wiki pages nên được tổ chức theo [cách nào].
```

### Ví dụ description tốt vs xấu

**Xấu** (quá ngắn, không đủ context):
```
Quy trình vận hành tiêu chuẩn.
```

**Tốt** (đủ context để LLM hiểu):
```
SOP (Standard Operating Procedure) là tài liệu mô tả quy trình thực hiện
một công việc cụ thể theo từng bước tuần tự.

Tri thức cần trích xuất:
- Tên quy trình và mục đích
- Các bước thực hiện (số thứ tự, hành động, người thực hiện)
- Điều kiện tiên quyết và điều kiện kết thúc
- Trường hợp ngoại lệ và cách xử lý
- Tài liệu/công cụ cần thiết
- Tần suất thực hiện (nếu có)

Wiki pages nên được trình bày dạng numbered list cho các bước,
có phần "Yêu cầu", "Các bước", và "Xử lý sự cố".
```

---

## Bộ Knowledge Types gợi ý cho doanh nghiệp

### 1. SOP / Quy trình vận hành

```
Name: SOP
Slug: sop
Description:
SOP (Standard Operating Procedure) mô tả quy trình thực hiện công việc
theo từng bước cụ thể, có thể tái lặp và đo lường được.

Trích xuất: tên quy trình, điều kiện tiên quyết, các bước tuần tự (ai làm gì),
điều kiện ngoại lệ, tài liệu liên quan, KPI/metrics đo lường.

Wiki nên có cấu trúc: Mục đích → Phạm vi áp dụng → Quy trình từng bước → 
Xử lý ngoại lệ → Tài liệu liên quan.
```

### 2. Chính sách / Nội quy

```
Name: Chính sách
Slug: chinh-sach
Description:
Tài liệu quy định nội bộ, chính sách nhân sự, nội quy công ty.
Bao gồm: quy tắc ứng xử, chính sách nghỉ phép, chính sách bảo mật thông tin,
điều khoản sử dụng thiết bị, quy định về làm việc từ xa.

Trích xuất: tên chính sách, phạm vi áp dụng, điều kiện/quy định cụ thể,
hình thức xử lý vi phạm, ngày hiệu lực, người phê duyệt.

Wiki nên có cấu trúc: Phạm vi → Quy định → Xử lý vi phạm → Liên hệ.
```

### 3. Tài liệu kỹ thuật

```
Name: Kỹ thuật
Slug: ky-thuat
Description:
Tài liệu kỹ thuật bao gồm: API documentation, hướng dẫn cài đặt/cấu hình,
kiến trúc hệ thống, troubleshooting guides, runbooks.

Trích xuất: tên hệ thống/component, dependencies, cú pháp lệnh, parameters,
error codes, giải pháp lỗi thường gặp, version compatibility.

Wiki nên dùng code blocks cho lệnh/config, table cho parameters,
numbered list cho các bước cài đặt.
```

### 4. An ninh mạng (Cybersecurity)

```
Name: An ninh mạng
Slug: an-ninh-mang
Description:
Tài liệu bảo mật thông tin gồm: threat intelligence, CVE advisories,
incident response procedures, security policies, vulnerability assessments,
penetration testing reports, phân tích malware/APT.

Trích xuất: CVE IDs, IOC (Indicators of Compromise: IP, domain, hash),
MITRE ATT&CK techniques, severity (Critical/High/Medium/Low),
hệ thống bị ảnh hưởng, biện pháp giảm thiểu, bước phản ứng sự cố.

Wiki nên phân loại theo: Threats, Vulnerabilities, Incidents, Controls,
mỗi trang có Risk Level rõ ràng và Remediation steps.
```

### 5. Sản phẩm / Dịch vụ

```
Name: Sản phẩm
Slug: san-pham
Description:
Tài liệu về sản phẩm hoặc dịch vụ của công ty: feature specs,
pricing, use cases, competitive analysis, release notes.

Trích xuất: tên sản phẩm, tính năng chính, đối tượng khách hàng,
giá/gói dịch vụ, so sánh với đối thủ, limitations, roadmap.

Wiki nên có: Overview → Features → Pricing → Use Cases → FAQ.
```

### 6. Đào tạo / Onboarding

```
Name: Đào tạo
Slug: dao-tao
Description:
Tài liệu đào tạo nhân viên: onboarding guide, training materials,
hướng dẫn sử dụng công cụ nội bộ, career development resources.

Trích xuất: mục tiêu học tập, prerequisites, nội dung khóa học,
thời gian ước tính, bài tập thực hành, tài nguyên tham khảo.

Wiki nên có Learning Path rõ ràng với checkpoints.
```

### 7. Hợp đồng / Pháp lý

```
Name: Pháp lý
Slug: phap-ly
Description:
Tài liệu pháp lý gồm: hợp đồng mẫu, điều khoản dịch vụ, thỏa thuận
bảo mật (NDA), compliance requirements, quy định pháp luật liên quan.

Trích xuất: loại hợp đồng, các bên liên quan, điều khoản chính,
nghĩa vụ, điều kiện chấm dứt, quy định phạt/bồi thường.

Wiki nên trình bày dạng clauses rõ ràng, highlight các điều khoản quan trọng.
```

### 8. Khách hàng / CRM

```
Name: Khách hàng
Slug: khach-hang
Description:
Tài liệu liên quan đến khách hàng: customer profiles, case studies,
support tickets, feedback, contract terms.

Trích xuất: tên công ty/khách hàng, ngành nghề, quy mô, nhu cầu chính,
vấn đề đã giải quyết, kết quả đạt được.

Wiki entity pages cho từng khách hàng quan trọng.
```

---

## Sử dụng Knowledge Types trong MCP

### Lọc kết quả theo knowledge type

Trong MCP config, token có thể giới hạn `allowed_knowledge_types`:

```json
{
  "allowed_knowledge_types": ["an-ninh-mang", "ky-thuat"]
}
```

Khi đó Claude chỉ thấy tài liệu thuộc 2 loại này.

### Query theo knowledge type

```
# Trong Claude
[gọi MCP tool list_sources]
knowledge_type_slug="an-ninh-mang"

→ Chỉ trả về tài liệu cybersecurity
```

### Tìm wiki theo knowledge type

```
[gọi MCP tool list_wiki_pages]
knowledge_type_slug="chinh-sach"

→ Chỉ trả về wiki pages từ tài liệu chính sách
```

---

## Best Practices

### Đặt tên và slug

- Tên: Ngắn gọn, dễ hiểu (2-4 từ)
- Slug: Không dấu, chữ thường, dùng `-` thay khoảng trắng
- Tránh trùng lặp slug

### Số lượng knowledge types

- **Ít hơn thường tốt hơn** — 6-10 types là ideal cho hầu hết tổ chức
- Quá nhiều types → khó chọn khi upload, nhân viên bối rối
- Quá ít types → mô tả không đủ chi tiết → wiki quality kém

### Cập nhật description sau khi dùng thực tế

Sau khi xử lý vài tài liệu đầu, xem wiki được tạo ra có đúng format không. Nếu chưa, cải thiện description và re-ingest tài liệu.

### Không xóa knowledge type đang dùng

Xóa knowledge type không xóa tài liệu và wiki pages đang dùng nó. Chỉ ảnh hưởng đến filtering. Thay vào đó, rename hoặc merge với type khác.
