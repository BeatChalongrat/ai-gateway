# SRAN Desktop — Web Parity Build Prompt

> ให้ Claude Fable ทำ พร้อม ai-gateway codebase attached  
> Reference spec: `docs/DESKTOP_WEB_PARITY_SPEC.md` (ค่าทุกอย่างมาจาก DevTools วัดจริง)  
> เป้าหมาย: ทำให้ desktop ดูเหมือน web 1:1 + เพิ่ม agentic features

---

## CRITICAL CONTEXT

ค่า color/typography/spacing ทั้งหมดมาจาก DevTools `getComputedStyle` ของ web จริง (localhost:3002/chat) — ไม่ใช่ eyeball  
**สิ่งสำคัญที่สุด: web ไม่มีสีน้ำเงินเลย** — accent ของ web คือสี foreground (#ececec dark / #0d0d0d light) ไม่ใช่ blue  
Desktop ยังใช้ AccentBlue ทุกที่ — นี่คือ gap ใหญ่ที่สุด

---

## BUILD ORDER

### Step 1: Neutralize Accent — Remove ALL Blue (P0)

**เป้าหมาย:** web ไม่มี blue เลย accent ทุกอย่างเป็นสี neutral foreground

**1a. เพิ่ม token ใหม่ใน theme dictionaries:**

`src/SranDesktop.Presentation/Themes/Dark.xaml` — เพิ่ม:
```xml
<Color x:Key="AccentColor">#ECECEC</Color>
<Color x:Key="OnAccentColor">#212121</Color>
<SolidColorBrush x:Key="AccentBrush" Color="{StaticResource AccentColor}" />
<SolidColorBrush x:Key="OnAccentBrush" Color="{StaticResource OnAccentColor}" />
```

`src/SranDesktop.Presentation/Themes/Light.xaml` — เพิ่ม:
```xml
<Color x:Key="AccentColor">#0D0D0D</Color>
<Color x:Key="OnAccentColor">#FFFFFF</Color>
<SolidColorBrush x:Key="AccentBrush" Color="{StaticResource AccentColor}" />
<SolidColorBrush x:Key="OnAccentBrush" Color="{StaticResource OnAccentColor}" />
```

**1b. อัพเดท AccentButton style ใน `Styles.xaml`:**

ทุกที่ที่ `AccentButton` ใช้ `AccentBlueBrush` → เปลี่ยนเป็น `AccentBrush`  
ทุกที่ที่ใช้ `TextOnAccentBrush` (white) → เปลี่ยนเป็น `OnAccentBrush`

**1c. Send button:** 
- bg ที่ active (มี text ใน input) → `AccentBrush` (neutral foreground)
- icon สี → `OnAccentBrush` (bg color)
- bg ที่ disabled (empty input) → `BgSelectedBrush` (#3a3a3a dark)
- icon สี disabled → `TextTertiaryBrush`

**1d. Agent toggle checked state:**
- ถ้าตอนนี้ใช้ AccentBlue → เปลี่ยนเป็น `AccentBrush`
- หรือถ้าอยากให้ agent mode มีสีเฉพาะ ให้ใช้ `AccentGreenBrush` (#10a37f) แทน blue

**1e. ToolConsentDialog header:**
- ถ้าใช้ AccentBlue bg → เปลี่ยนเป็น `BgSecondaryBrush` + `AccentWarningBrush` สำหรับ danger banner

**1f. Empty state logo "S" background:**
- เปลี่ยนจาก AccentBlue เป็น `AccentBrush` (neutral) — หรือดีกว่านั้นคือใช้ SRAN owl logo (ดู Step 3)

**เก็บ AccentBlueBrush ไว้ define** (ไม่ต้องลบ) แต่ไม่ควรมี element ไหนใช้มันใน UI chrome หลัก

**Verification:** grep ทุก .xaml file สำหรับ "AccentBlue" — ต้องไม่เจอใน element ที่ user เห็น (อาจเหลือแค่ใน resource definition)

---

### Step 2: New Chat Button → Outlined Ghost (P0)

**Web:** transparent bg, 0.8px border, text-primary, radius 8px, shortcut hint ขวา

**แก้ `SidebarView.xaml`:** เปลี่ยน New Chat button จาก AccentButton (filled) เป็น outlined:

```xml
<Button Command="{Binding NewChatCommand}" 
        Margin="12,8,12,8" Padding="10,9"
        Background="Transparent" 
        BorderThickness="1"
        BorderBrush="{DynamicResource BorderBrush}"
        Foreground="{DynamicResource TextPrimaryBrush}"
        ToolTip="New chat (Ctrl+N)">
    <Grid>
        <StackPanel Orientation="Horizontal" HorizontalAlignment="Left">
            <controls:IconView Geometry="{StaticResource Geo_Plus}"
                               Stroke="{DynamicResource TextPrimaryBrush}"
                               Width="15" Height="15" Margin="0,0,7,0" />
            <TextBlock Text="New chat" VerticalAlignment="Center" FontSize="14" />
        </StackPanel>
        <TextBlock Text="Ctrl+N" FontSize="10" 
                   HorizontalAlignment="Right" VerticalAlignment="Center"
                   Opacity="0.5"
                   Foreground="{DynamicResource TextSecondaryBrush}" />
    </Grid>
</Button>
```

**สำคัญ:** ต้องไม่ใช้ `Style="{DynamicResource AccentButton}"` — ใช้ default button style แล้ว override properties ข้างบน หรือสร้าง `OutlinedButton` style ใหม่

**Hover effect:** bg เปลี่ยนเป็น `BgSelectedBrush` (#3a3a3a dark) เมื่อ hover

---

### Step 3: Empty State Redesign (P1)

**Web layout (จากบนลงล่าง, ทุกอย่าง centered):**
1. SRAN owl logo — 96px, theme-inverted (dark theme = white, light = dark)
2. Greeting — "Good afternoon, Somchai" — **30px, weight 600**
3. Subtitle — "How can I help you today?" — **18px, weight 400, text-secondary**
4. Composer (centered) — web แสดง composer ตรงนี้ด้วย
5. Disclaimer — "SRAN Chat can make mistakes. Check important info." — **11px, text-tertiary**
6. Suggestion chips — **horizontal row ของ pill buttons** (ไม่ใช่ 2x2 grid)

**แก้ `MainWindow.xaml` empty state section:**

**3a. Logo:** 
- ถ้ามี `sran-logo.png` ใน web project (`apps/web-chat/public/`) → copy มาเป็น `Assets/sran-logo.png` (embed as Resource)
- ถ้าไม่มี/ไม่สะดวก → ใช้ "S" tile แต่เปลี่ยน bg จาก blue เป็น neutral (`AccentBrush`)
- Dark theme: invert logo ด้วย opacity mask หรือ ship white variant

**3b. Greeting:** เปลี่ยน FontSize เป็น **30**, FontWeight เป็น **SemiBold** (600)

**3c. Subtitle:** เปลี่ยน FontSize เป็น **18**

**3d. Chips → horizontal pill row:**
```xml
<!-- แทนที่ UniformGrid Columns="2" ด้วย: -->
<WrapPanel HorizontalAlignment="Center" Margin="0,16,0,0">
    <!-- ทุก chip เป็น pill shape -->
    <Button Margin="4" Padding="16,8" Background="Transparent"
            BorderThickness="1" BorderBrush="{DynamicResource BorderBrush}"
            Command="{Binding SuggestionCommand}" CommandParameter="Help me draft ">
        <!-- CornerRadius ต้องใช้ attached property หรือ ControlTemplate -->
        <StackPanel Orientation="Horizontal">
            <controls:IconView Geometry="{StaticResource Geo_Edit}" 
                               Width="14" Height="14"
                               Stroke="{DynamicResource TextTertiaryBrush}" 
                               Margin="0,0,8,0" />
            <TextBlock Text="Write" FontSize="13" VerticalAlignment="Center"
                       Foreground="{DynamicResource TextSecondaryBrush}" />
        </StackPanel>
    </Button>
    <Button Margin="4" Padding="16,8" Background="Transparent"
            BorderThickness="1" BorderBrush="{DynamicResource BorderBrush}"
            Command="{Binding SuggestionCommand}" CommandParameter="Explain ">
        <StackPanel Orientation="Horizontal">
            <controls:IconView Geometry="{StaticResource Geo_Learn}" 
                               Width="14" Height="14"
                               Stroke="{DynamicResource TextTertiaryBrush}" 
                               Margin="0,0,8,0" />
            <TextBlock Text="Learn" FontSize="13" VerticalAlignment="Center"
                       Foreground="{DynamicResource TextSecondaryBrush}" />
        </StackPanel>
    </Button>
    <Button Margin="4" Padding="16,8" Background="Transparent"
            BorderThickness="1" BorderBrush="{DynamicResource BorderBrush}"
            Command="{Binding SuggestionCommand}" CommandParameter="Write code ">
        <StackPanel Orientation="Horizontal">
            <controls:IconView Geometry="{StaticResource Geo_Code}" 
                               Width="14" Height="14"
                               Stroke="{DynamicResource TextTertiaryBrush}" 
                               Margin="0,0,8,0" />
            <TextBlock Text="Code" FontSize="13" VerticalAlignment="Center"
                       Foreground="{DynamicResource TextSecondaryBrush}" />
        </StackPanel>
    </Button>
    <Button Margin="4" Padding="16,8" Background="Transparent"
            BorderThickness="1" BorderBrush="{DynamicResource BorderBrush}"
            Command="{Binding SuggestionCommand}" CommandParameter="Analyze ">
        <StackPanel Orientation="Horizontal">
            <controls:IconView Geometry="{StaticResource Geo_Analyze}" 
                               Width="14" Height="14"
                               Stroke="{DynamicResource TextTertiaryBrush}" 
                               Margin="0,0,8,0" />
            <TextBlock Text="Analyze" FontSize="13" VerticalAlignment="Center"
                       Foreground="{DynamicResource TextSecondaryBrush}" />
        </StackPanel>
    </Button>
</WrapPanel>
```

**สำคัญ:** chips ต้องเป็น **full-pill** (CornerRadius สูงมาก เช่น 18-20) — ใช้ ControlTemplate override หรือ attached property

**3e. Disclaimer:**
```xml
<TextBlock Text="SRAN Chat can make mistakes. Check important info."
           FontSize="11" Margin="0,12,0,0"
           HorizontalAlignment="Center"
           Foreground="{DynamicResource TextTertiaryBrush}" />
```

---

### Step 4: Theme-Aware Code Blocks (P1)

**Web:** code blocks เปลี่ยนสีตาม theme (light theme = light bg)  
**Desktop:** code bg ดำตลอดทั้ง dark + light

**4a. อัพเดท theme dictionaries:**

`Dark.xaml`:
```xml
<Color x:Key="CodeBgColor">#1E1E1E</Color>
<SolidColorBrush x:Key="CodeBgBrush" Color="{StaticResource CodeBgColor}" />
<SolidColorBrush x:Key="CodeTextBrush" Color="#D4D4D4" />
<SolidColorBrush x:Key="CodeHeaderBgBrush" Color="#2D2D2D" />
```

`Light.xaml`:
```xml
<Color x:Key="CodeBgColor">#F7F7F8</Color>
<SolidColorBrush x:Key="CodeBgBrush" Color="{StaticResource CodeBgColor}" />
<SolidColorBrush x:Key="CodeTextBrush" Color="#1F1F1F" />
<SolidColorBrush x:Key="CodeHeaderBgBrush" Color="#EEEEEE" />
```

**4b. อัพเดท `MarkdownRenderer.cs`:**
- Code block body: ใช้ `SetResourceReference` สำหรับ bg → `CodeBgBrush`, text → `CodeTextBrush`
- Code block header (language label + copy button): bg → `CodeHeaderBgBrush`
- ตรวจสอบว่า inline code ก็ใช้ theme-aware brush ด้วย

---

### Step 5: Sidebar Active Item — #2A2A2A + No Accent Bar (P2)

**Web:** selected item = bg `#2a2a2a` (dark) / `#e5e5e5` (light), **ไม่มี** left accent bar

**5a. เพิ่ม token:**

`Dark.xaml`:
```xml
<Color x:Key="SidebarActiveColor">#2A2A2A</Color>
<SolidColorBrush x:Key="SidebarActiveBrush" Color="{StaticResource SidebarActiveColor}" />
```

`Light.xaml`:
```xml
<Color x:Key="SidebarActiveColor">#E5E5E5</Color>
<SolidColorBrush x:Key="SidebarActiveBrush" Color="{StaticResource SidebarActiveColor}" />
```

**5b. แก้ `SidebarView.xaml`:**
- Selected conversation item: bg → `SidebarActiveBrush` (แทน `BgSelectedBrush`)
- **ลบ** left accent bar สีน้ำเงิน (Border ที่มี width 3 สี AccentBlue ทางซ้าย)
- Hover: kebab `⋯` icon ปรากฏ (three dots) แทน right-click context menu

---

### Step 6: Assistant Prose — 16px / Line-Height 28px (P2)

**Web:** assistant text = 16px, line-height 28px (1.75)

**แก้ `MarkdownRenderer.cs`:**
- Paragraph text: `FontSize = 16` (ถ้าตอนนี้เป็น 14)
- `LineHeight = 28` (ใช้ `TextBlock.LineHeight` หรือ `Paragraph.LineHeight`)
- List items: เหมือนกัน 16px / 28px
- Headings: scale จาก 16 base (H1=28, H2=22, H3=18)

---

### Step 7: Assistant Action Row — Always Visible When Complete (P3)

**Web:** action buttons (copy, thumbs up, thumbs down, regenerate) แสดงตลอดหลัง streaming จบ ไม่ใช่ hover-only

**แก้ `MessageBubble.xaml`:**
- สำหรับ assistant messages: action row `Opacity="1"` เมื่อ `IsStreaming=False`
- ลบ condition `IsMouseOver` จาก trigger ที่ control action row visibility
- สำหรับ user messages: **keep hover-gated** (web ก็เป็น hover)

**Action button sizing:** 30x30, radius 4px, icon 18px, สี `TextTertiaryBrush`

---

### Step 8: Header — Drop Centered Title (P3)

**Web:** ไม่มี conversation title ตรงกลาง header — มีแค่ kebab menu ขวาบน

**แก้ `MainWindow.xaml` หรือ `ChatView.xaml`:**
- ซ่อน/ลบ centered title TextBlock
- (Optional) เพิ่ม overflow menu `⋯` ที่ขวาบน → Export, Rename, Delete

---

### Step 9: Sidebar Extras (P3)

**9a. Section labels → UPPERCASE:**
- "Pinned" → "PINNED", "Today" → "TODAY", "Previous 30 days" → "PREVIOUS 30 DAYS"
- เพิ่ม "OLDER" group
- FontSize 10, FontWeight 500 (Medium), text-tertiary

**9b. New folder button:**
```xml
<!-- ใต้ New chat button -->
<Button Margin="12,0,12,4" Padding="10,7"
        Background="Transparent" BorderThickness="0"
        Command="{Binding NewFolderCommand}"
        Foreground="{DynamicResource TextSecondaryBrush}">
    <StackPanel Orientation="Horizontal">
        <controls:IconView Geometry="{StaticResource Geo_Folder}"
                           Stroke="{DynamicResource TextSecondaryBrush}"
                           Width="14" Height="14" Margin="0,0,7,0" />
        <TextBlock Text="New folder" FontSize="13" VerticalAlignment="Center" />
    </StackPanel>
</Button>
```

**9c. Disclaimer ใต้ composer:**
```xml
<TextBlock Text="SRAN Chat can make mistakes. Check important info."
           FontSize="11" HorizontalAlignment="Center" Margin="0,6,0,8"
           Foreground="{DynamicResource TextTertiaryBrush}" />
```

**9d. Sidebar collapse chevron:**
- เพิ่ม `<` chevron icon ข้างขวาของ "SRAN Chat" wordmark ใน sidebar header
- กดแล้ว collapse sidebar (เหมือน toggle ที่มีอยู่แล้ว)

---

## AGENTIC FEATURES

### A1: Agent Panel — Show on Toggle + Richer Rows

**เมื่อ `IsAgentMode = true`:** แสดง panel ทันที (ตอนนี้ต้องรอ tool activity)

**แก้ binding:** Panel visibility bind to `IsAgentMode` แทน `HasAgentActivity`:
```xml
<!-- เปลี่ยนจาก: -->
<Visibility="{Binding HasAgentActivity, Converter=...}">
<!-- เป็น: -->
<Visibility="{Binding IsAgentMode, Converter=...}">
```

**Panel content เมื่อยังไม่มี activity:**
- Header: Agent icon + "Agent Mode" + "9 tools available"
- List: แสดง tool names ทุกตัวจาก registry (ReadFile, WriteFile, ListDirectory, SearchFiles, OpenUrl, ReadClipboard, WriteClipboard, RunCommand, Screenshot)
- แต่ละ tool: category icon + name + permission badge (Ask/Allow/Deny)

**Execution row enhancement:**
- เพิ่ม indeterminate ProgressBar ขณะ `IsPending = true`
- แสดง elapsed time real-time (update ทุก 100ms ขณะ running)
- Expander สำหรับดู full args JSON + result

---

### A2: Thinking/Reasoning Block Enhancement

**ปัจจุบัน:** มี "Thinking..." Expander อยู่แล้ว

**เพิ่ม:**
- ขณะ streaming: **"Thinking..."** + pulsing animation + elapsed timer (0.0s, 0.1s, ...)
- เมื่อ complete: เปลี่ยนเป็น **"Thought for 3.2s"** (duration จาก analysis_status start→complete)
- Collapsed by default เมื่อ complete
- Content: reasoning text ที่ stream มา (MarkdownViewer)

**Implementation:**
- `MessageViewModel` เพิ่ม `ReasoningDuration` property
- จับเวลาจาก `StreamStatusEvent { Kind: "analysis", Status: "started" }` ถึง `Status: "completed"`
- Template: header text bind to `IsReasoningComplete ? $"Thought for {ReasoningDuration:F1}s" : "Thinking..."`

---

### A3: Search Results Cards

**เมื่อ response มี web search results** (SSE event `message_sources`):

แสดง cards ก่อน/หลัง assistant text:
```xml
<ItemsControl ItemsSource="{Binding Sources}">
    <ItemsControl.ItemTemplate>
        <DataTemplate>
            <Border Padding="10,8" Margin="0,2" CornerRadius="8"
                    Background="{DynamicResource BgSecondaryBrush}"
                    BorderThickness="1" BorderBrush="{DynamicResource BorderBrush}">
                <StackPanel>
                    <StackPanel Orientation="Horizontal">
                        <controls:IconView Geometry="{StaticResource Geo_Browser}"
                                           Width="12" Height="12"
                                           Stroke="{DynamicResource TextTertiaryBrush}"
                                           Margin="0,0,6,0" />
                        <TextBlock Text="{Binding Title}" FontSize="13" FontWeight="SemiBold"
                                   Foreground="{DynamicResource TextPrimaryBrush}" />
                    </StackPanel>
                    <TextBlock Text="{Binding Url}" FontSize="11" Margin="18,2,0,0"
                               Foreground="{DynamicResource TextTertiaryBrush}" 
                               TextTrimming="CharacterEllipsis" />
                    <TextBlock Text="{Binding Snippet}" FontSize="13" Margin="18,2,0,0"
                               Foreground="{DynamicResource TextSecondaryBrush}"
                               TextWrapping="Wrap" MaxHeight="40" TextTrimming="CharacterEllipsis" />
                </StackPanel>
            </Border>
        </DataTemplate>
    </ItemsControl.ItemTemplate>
</ItemsControl>
```

**Backend:** เพิ่ม `MessageSourcesEvent` ใน `ChatStreamEvents.cs`, handle `message_sources` ใน `ChatService.cs`

---

### A4: Inline Image Display

**เมื่อ response มี image** (SSE event `message_files` with image mime):

- แสดง image inline ใน chat (max 320px width, CornerRadius 12)
- Click → เปิด full size (Process.Start)
- ขณะ generate: placeholder กับ pulsing animation

**Implementation:**
- `MessageViewModel` เพิ่ม `ImageFiles` collection
- `MessageBubble` template: `Image` control ภายใน assistant message area
- Handle `message_files` SSE event (ตอนนี้ ignored)

---

### A5: Drag-Drop File Overlay

**เมื่อ user ลากไฟล์มาวาง:**

แสดง overlay ทับ chat area:
```xml
<Border x:Name="DropOverlay" Visibility="Collapsed"
        Background="#80212121" CornerRadius="12" Margin="20"
        BorderThickness="2" BorderBrush="{DynamicResource TextSecondaryBrush}"
        BorderDashArray="5,3">
    <StackPanel HorizontalAlignment="Center" VerticalAlignment="Center">
        <controls:IconView Geometry="{StaticResource Geo_Attach}" 
                           Width="32" Height="32"
                           Stroke="{DynamicResource TextPrimaryBrush}" />
        <TextBlock Text="Drop files to attach" FontSize="16" Margin="0,8,0,0"
                   Foreground="{DynamicResource TextPrimaryBrush}" />
    </StackPanel>
</Border>
```

Handle `DragEnter` / `DragLeave` / `Drop` events on chat view

---

### A6: Keyboard Shortcuts Panel

**Ctrl+/** → แสดง modal dialog:

| Shortcut | Action |
|---|---|
| Ctrl+N | New chat |
| Ctrl+K | Search conversations |
| Ctrl+Shift+S | Toggle sidebar |
| Ctrl+Shift+Space | Show/hide from anywhere |
| Enter | Send message |
| Shift+Enter | New line |
| Escape | Stop generating |

**Implementation:** `ShortcutsDialog.xaml` + `InputBinding` ใน `MainWindow.xaml`:
```xml
<KeyBinding Key="Oem2" Modifiers="Control" Command="{Binding ShowShortcutsCommand}" />
```

---

### A7: Conversation Branch Navigation

**เมื่อ user edit message → server สร้าง branch:**

แสดง navigation arrows บน message ที่มี branch:
```
◄  2 / 3  ►
```

**Implementation:**
- `MessageDto` เพิ่ม `BranchIndex`, `BranchCount` fields (จาก API)
- `BranchNavControl.xaml`: prev/next buttons + counter
- `ChatEdgeApiClient` เพิ่ม `SwitchBranchAsync(messageId, branchIndex)`

---

## VERIFICATION CHECKLIST

หลัง build เสร็จ ให้ verify:

1. [ ] **Zero blue** — grep `.xaml` for "AccentBlue" ต้องไม่เจอใน element ที่ user เห็น
2. [ ] New chat button = outlined (transparent bg + border), ไม่ใช่ filled
3. [ ] Send button: active = neutral foreground bg, disabled = surface-hover bg
4. [ ] Empty state: chips เป็นแถวเดียว pill shape, ไม่ใช่ 2x2 grid
5. [ ] Empty state: greeting 30px, subtitle 18px
6. [ ] Empty state: disclaimer text แสดงอยู่
7. [ ] Code blocks: light theme มี light bg (#F7F7F8), ไม่ใช่ dark
8. [ ] Code blocks: header มี bg สี (#2D2D2D dark / #EEE light)
9. [ ] Sidebar selected item: bg #2A2A2A (dark), ไม่มี blue accent bar
10. [ ] Section labels เป็น UPPERCASE
11. [ ] Assistant prose: 16px, line-height 28px
12. [ ] Assistant action row: visible ตลอดหลัง streaming จบ (ไม่ใช่ hover-only)
13. [ ] Agent panel: แสดงทันทีเมื่อ toggle on (ไม่ต้องรอ activity)
14. [ ] Thinking block: แสดง duration เมื่อ complete ("Thought for X.Xs")
15. [ ] Header: ไม่มี centered conversation title
16. [ ] Dark theme + Light theme ทั้งคู่ดูถูกต้อง
17. [ ] 98 tests ยังผ่านทั้งหมด

---

## BUILD PRIORITY

**Day 1 (closes ~80% of gap):**
- Step 1: Neutralize accent
- Step 2: Outlined New chat
- Step 3: Empty state redesign

**Day 2:**
- Step 4: Theme-aware code blocks
- Step 5: Sidebar active item
- Step 6: Assistant prose sizing

**Day 3:**
- Step 7: Action row always visible
- Step 8: Drop header title
- Step 9: Sidebar extras

**Day 4-5:**
- A1: Agent panel enhancement
- A2: Thinking block enhancement
- A3-A7: Search cards, inline images, drag-drop, shortcuts, branches

---

*End of Web Parity Build Prompt*
