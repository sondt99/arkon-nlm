# Knowledge Types — Phân loại Tri thức

Knowledge Types là hệ thống taxonomy cho phép phân loại tài liệu theo mục đích và nội dung. Đây là cầu nối quan trọng giữa tài liệu thô và chất lượng wiki được tạo ra.

---

## Tại sao Knowledge Types quan trọng?

Khi LLM xử lý tài liệu, nó cần hiểu **loại tri thức** để:

1. **Trích xuất đúng thực thể**: SOP cần trích xuất "bước thực hiện", còn chính sách cần trích xuất "quy định, điều kiện"
2. **Định dạng wiki phù hợp**: Quy trình → numbered list; Định nghĩa → prose; So sánh → table
3. **Chọn slug convention**: Quy trình vận hành → `topic/`, Sản phẩm cụ thể → `entity/`
4. **Áp dụng domain rules**: Tài liệu pentest cần giữ technique theo từng platform — LLM dùng `extraction_hints` để biết điều này
5. **Lọc theo MCP scope**: Employee chỉ thấy knowledge types được assign cho MCP token của họ

---

## Cấu trúc Knowledge Type

```
KnowledgeType
  id                UUID
  name              VARCHAR     # "An ninh mạng", "SOP", "Chính sách"
  slug              VARCHAR     # "an-ninh-mang", "sop", "chinh-sach"
  description       TEXT        # Nhãn/mô tả ngắn hiển thị trên UI
  extraction_hints  TEXT        # LLM ĐỌC TRƯỜNG NÀY — domain-specific extraction rules
  color             VARCHAR     # Hex color cho UI badge (#6366f1)
```

---

## Hai trường ảnh hưởng đến LLM: `description` vs `extraction_hints`

| Trường | Mục đích | Ai đọc |
|---|---|---|
| `description` | Nhãn ngắn, nhận diện danh mục ("Pentest and redteam techniques") | Hiển thị UI + label cho LLM |
| `extraction_hints` | Hướng dẫn chi tiết: cái gì cần giữ, cái gì cần bỏ, cách tổ chức wiki page | **LLM pipeline đọc trực tiếp** khi compile và extract |

**Nguyên tắc:**
- `description`: ngắn, nhận diện đủ (1-3 câu)
- `extraction_hints`: hướng dẫn đầy đủ, **ghi đè** các rule chung của pipeline khi có xung đột

---

## Thiết kế `extraction_hints` hiệu quả

`extraction_hints` là Markdown text được inject vào LLM prompt **sau** các rule chung (keep/drop general heuristics). Rules trong `extraction_hints` có độ ưu tiên cao hơn rules mặc định.

### Khi nào cần `extraction_hints`?

- Domain có kiến thức **rất specific** mà rule chung sẽ "generalize away" (pentest, y tế, luật pháp)
- Cần entity types đặc biệt ngoài bộ mặc định (`person|org|product|regulation|location|system|equipment|technique|cve|tool|payload`)
- Cần cấu trúc wiki page khác với template chung
- Cần giữ lại **platform-specific details** mà LLM hay bỏ vì tưởng là "source framing"

### Template cho `extraction_hints`

```markdown
Các tài liệu thuộc loại này chứa [mô tả domain].

**Quy tắc giữ lại (bắt buộc):**
- KEEP [loại thông tin 1] nguyên văn — KHÔNG generalize
- KEEP [loại thông tin 2] với đầy đủ context
- KEEP [loại thông tin đặc thù platform/version]

**Quy tắc bỏ:**
- DROP [loại thông tin không có giá trị cho domain này]

**Loại entity trong domain này:**
- `technique` — [định nghĩa]
- `cve` — [định nghĩa]

**Quy ước đặt slug:**
- concept/<tên-technique>-<platform> cho từng variant
```

### Ví dụ: SOP thông thường (không cần `extraction_hints`)

SOP là domain đủ chung — pipeline mặc định xử lý tốt. Chỉ cần `description` rõ ràng.

### Ví dụ: Pentest/Redteam (cần `extraction_hints`)

**Description** (ngắn):
```
Tài liệu pentest, redteam, kỹ thuật tấn công và bypass bảo mật.
```

**Extraction hints** (đầy đủ):
```markdown
Các tài liệu này chứa kỹ thuật tấn công bảo mật — pentest, redteam, exploit development.

**Quy tắc giữ lại (bắt buộc):**
- KEEP tất cả lệnh/cú pháp theo từng platform nguyên văn.
  Ví dụ: "EXEC master..xp_cmdshell 'whoami'" trên SQL Server KHÔNG được
  rút gọn thành "stored procedure execution" — tính cụ thể là giá trị.
- KEEP kỹ thuật bypass theo từng platform là các concept page riêng biệt
  (SQLi bypass trên MySQL ≠ SQLi bypass trên MSSQL ≠ Oracle).
- KEEP CVE IDs, CVSS scores, CWE numbers — đây là khóa chính, không phải metadata.
- KEEP lệnh tool với đầy đủ flag và option (vd: "sqlmap -u URL --dbs --batch --level=5").
- KEEP điều kiện version cụ thể (vd: "chỉ hoạt động trên Apache 2.4.49").
- KEEP payload strings, shellcode, PoC code nguyên văn.
- KEEP chuỗi bypass WAF/AV theo từng vendor.

**KHÔNG làm:**
- KHÔNG coi platform-specific commands là "source-specific framing" rồi bỏ.
- KHÔNG gộp các technique của các platform khác nhau vào một concept page.

**Slug convention:**
- concept/<tên-technique>-<platform> (vd: concept/sqli-stored-proc-mssql)
- entity/<tool-name> cho tool offensive security
- entity/<cve-id> cho từng CVE
```

---

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

### 4. An ninh mạng — Phòng thủ (Defensive Security)

```
Name: An ninh mạng
Slug: an-ninh-mang
Description:
Tài liệu bảo mật thông tin gồm: threat intelligence, CVE advisories,
incident response procedures, security policies, vulnerability assessments.

Trích xuất: CVE IDs, IOC (Indicators of Compromise: IP, domain, hash),
MITRE ATT&CK techniques, severity (Critical/High/Medium/Low),
hệ thống bị ảnh hưởng, biện pháp giảm thiểu, bước phản ứng sự cố.

Wiki nên phân loại theo: Threats, Vulnerabilities, Incidents, Controls,
mỗi trang có Risk Level rõ ràng và Remediation steps.
```

> **Lưu ý:** Với tài liệu **tấn công/pentest/redteam** (không phải phòng thủ), cần thêm `extraction_hints` riêng — xem mục **Pentest & Redteam** bên dưới.

### 4b. Pentest & Redteam (Offensive Security)

Loại này được **tự động seed `extraction_hints`** khi startup nếu slug có chứa `pentest`, `redteam`, `offensive`, `exploit`, `bypass`, hoặc tên tool tấn công.

```
Name: Pentest
Slug: pentest
Description:
Tài liệu penetration testing, redteam operations, kỹ thuật tấn công và bypass bảo mật.
Bao gồm: SQLi bypass, privilege escalation, lateral movement, C2, post-exploitation.
```

`extraction_hints` (tự động seed — admin có thể chỉnh sửa):
```markdown
Tài liệu này chứa kỹ thuật tấn công — pentest, redteam, exploit development.

**KEEP (bắt buộc):**
- Lệnh platform-specific nguyên văn (EXEC xp_cmdshell, INTO OUTFILE MySQL...)
- Kỹ thuật bypass theo từng platform = concept page riêng biệt
- CVE IDs, CVSS scores, CWE numbers
- Tool commands với đầy đủ flag (sqlmap -u URL --dbs --batch --level=5)
- Điều kiện version (Apache 2.4.49 only, patched in 2.4.50)
- Payload strings, PoC code, bypass WAF/AV theo vendor

**KHÔNG generalize platform-specific technique** — tính cụ thể là giá trị.
**Slug:** concept/<technique>-<platform>, entity/<tool>, entity/<cve-id>
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

### Khi nào dùng `description` vs `extraction_hints`

- **Chỉ cần `description`**: domain chung (SOP, chính sách, tài liệu kỹ thuật thông thường) — pipeline mặc định xử lý tốt
- **Cần cả `extraction_hints`**: domain chuyên biệt mà rule chung sẽ lọc mất thông tin có giá trị:
  - Pentest/Redteam: technique theo platform bị coi là "source-specific framing" → bị lọc
  - Y tế: liều lượng/protocol cụ thể bị coi là "repetitive detail" → bị bỏ
  - Pháp lý: điều khoản cụ thể của hợp đồng bị "summarized" → mất ngữ nghĩa

### `extraction_hints` không ghi đè admin khi đã set

Seed tự động chỉ set `extraction_hints` khi trường này đang NULL. Nếu admin đã chỉnh sửa, seed sẽ bỏ qua. Muốn reset về default: đặt lại `extraction_hints = NULL` qua API rồi restart server.

### Cập nhật `extraction_hints` sau khi dùng thực tế

Sau khi xử lý vài tài liệu đầu, xem wiki được tạo ra:
- Nếu LLM đang generalize mất detail quan trọng → thêm rule `KEEP [loại thông tin đó]` vào `extraction_hints`
- Nếu LLM đang giữ quá nhiều noise → thêm rule `DROP [loại thông tin đó]`
- Re-ingest tài liệu sau khi cập nhật để thấy hiệu quả

### Không xóa knowledge type đang dùng

Xóa knowledge type không xóa tài liệu và wiki pages đang dùng nó. Chỉ ảnh hưởng đến filtering. Thay vào đó, rename hoặc merge với type khác.
