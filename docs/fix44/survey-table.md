| กลุ่ม | คลาส | จุด | ไฟล์ (ตัวอย่าง) | ที่เห็นก่อน FIX44 | ทำอะไร |
|---|---|---|---|---|---|
| A | `border-border/60` | 18 | page.tsx:392, page.tsx:401, page.tsx:517 … | ขอบ = preflight `#e5e7eb` (สว่างจ้าบน dark) | ลบ `/NN` → token เดิม |
| A | `focus:ring-border-strong/40` | 13 | page.tsx:422, page.tsx:458, page.tsx:609 … | ขอบ = preflight `#e5e7eb` (สว่างจ้าบน dark) | ลบ `/NN` → token เดิม |
| B | `border-status-danger/40` | 16 | AppProviders.tsx:77, page.tsx:530, page.tsx:489 … | ขอบ = `#e5e7eb` | **รอ** L1/L2 (allowlist) |
| B | `hover:bg-status-danger/10` | 13 | page.tsx:416, page.tsx:499, page.tsx:489 … | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| B | `bg-status-danger/10` | 11 | page.tsx:510, CloudTasksSection.tsx:297, CloudTasksSection.tsx:645 … | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| B | `bg-status-danger/85` | 5 | ChatInput.tsx:1260, ConversationItem.tsx:335, FilePreviewCard.tsx:141 … | ไม่มีพื้นหลัง/สี | ลบ `/NN` → แดงทึบ (เจ้าของเคาะ) |
| B | `bg-status-danger/5` | 4 | page.tsx:644, ConsentCard.tsx:112, FilePreviewCard.tsx:83 … | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| B | `border-status-danger/50` | 3 | ConsentCard.tsx:125, ToolCard.tsx:104, ToolCard.tsx:216 | ขอบ = `#e5e7eb` | **รอ** L1/L2 (allowlist) |
| B | `border-status-danger/30` | 3 | MessageBubble.tsx:744, ShareModal.tsx:154, ErrorBoundary.tsx:43 | ขอบ = `#e5e7eb` | **รอ** L1/L2 (allowlist) |
| B | `border-status-success/40` | 2 | page.tsx:525, page.tsx:512 | ขอบ = `#e5e7eb` | **รอ** L1/L2 (allowlist) |
| B | `hover:bg-status-danger/15` | 2 | ConversationItem.tsx:298, Sidebar.tsx:1388 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| B | `bg-status-danger/15` | 1 | AppProviders.tsx:77 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| B | `border-status-warning/40` | 1 | page.tsx:520 | ขอบ = `#e5e7eb` | **รอ** L1/L2 (allowlist) |
| B | `bg-status-warning/15` | 1 | layout.tsx:465 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| B | `ring-status-warning/30` | 1 | layout.tsx:465 | ไม่มี ring (ได้ น้ำเงิน default ของ Tailwind แทน) | **รอ** L1/L2 (allowlist) |
| B | `hover:bg-status-danger/20` | 1 | page.tsx:510 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| B | `text-status-danger/80` | 1 | MessageBubble.tsx:751 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| B | `hover:bg-status-danger/90` | 1 | RecordingBar.tsx:65 | ไม่มีพื้นหลัง/สี | ลบ `/NN` → แดงทึบ (เจ้าของเคาะ) |
| B | `bg-status-danger/90` | 1 | Button.tsx:25 | ไม่มีพื้นหลัง/สี | ลบ `/NN` → แดงทึบ (เจ้าของเคาะ) |
| B | `border-status-danger/60` | 1 | FileTypeBadge.tsx:152 | ขอบ = `#e5e7eb` | **รอ** L1/L2 (allowlist) |
| B | `border-status-warning/60` | 1 | FileTypeBadge.tsx:156 | ขอบ = `#e5e7eb` | **รอ** L1/L2 (allowlist) |
| B | `border-text-secondary/30` | 1 | Spinner.tsx:5 | ขอบ = `#e5e7eb` | **รอ** L1/L2 (allowlist) |
| C | `bg-bg-base/40` | 6 | ConversationItem.tsx:236, planCardUI.tsx:65, Modal.tsx:123 … | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| C | `bg-bg-secondary/50` | 5 | CloudTasksSection.tsx:314, CloudTasksSection.tsx:365, CloudTasksSection.tsx:620 … | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| C | `bg-bg-surface/85` | 4 | AttachmentDropZone.tsx:90, ChatThread.tsx:651, FilePreviewCard.tsx:157 … | ไม่มีพื้นหลัง/สี | ลบ `/NN` → แดงทึบ (เจ้าของเคาะ) |
| C | `bg-bg-surface/95` | 4 | ChatThread.tsx:573, ChatThread.tsx:590, ChatThread.tsx:598 … | ไม่มีพื้นหลัง/สี | ลบ `/NN` → แดงทึบ (เจ้าของเคาะ) |
| C | `bg-bg-surface/80` | 4 | VideoGeneratingPlaceholder.tsx:104, ImageLightbox.tsx:174, ImageLightbox.tsx:189 … | ไม่มีพื้นหลัง/สี | ลบ `/NN` → แดงทึบ (เจ้าของเคาะ) |
| C | `hover:bg-bg-elevated/60` | 3 | ModelSelector.tsx:498, ModelSelector.tsx:673, ShareModal.tsx:199 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| C | `bg-bg-base/50` | 2 | page.tsx:392, page.tsx:401 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| C | `bg-bg-base/30` | 2 | VersionTimeline.tsx:153, WorkspacePanel.tsx:185 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| C | `bg-bg-base/80` | 1 | page.tsx:74 | ไม่มีพื้นหลัง/สี | ลบ `/NN` → แดงทึบ (เจ้าของเคาะ) |
| C | `bg-bg-elevated/40` | 1 | CanvasPanel.tsx:270 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| C | `bg-bg-elevated/70` | 1 | BranchNavigator.tsx:27 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| C | `bg-bg-secondary/70` | 1 | ChatThread.tsx:813 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| C | `bg-bg-elevated/60` | 1 | ModelSelector.tsx:673 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| C | `bg-bg-base/60` | 1 | ShareModal.tsx:136 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| C | `bg-bg-surface/60` | 1 | ToolCard.tsx:104 | ไม่มีพื้นหลัง/สี | **รอ** L1/L2 (allowlist) |
| C | `bg-bg-elevated/80` | 1 | ProvenanceBadge.tsx:49 | ไม่มีพื้นหลัง/สี | ลบ `/NN` → แดงทึบ (เจ้าของเคาะ) |
