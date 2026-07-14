# SRAN Desktop — Final 5 Visual Fixes (Web Parity)

> ให้ Claude Fable ทำ พร้อม ai-gateway codebase attached  
> ค่าทุกอย่างมาจาก DevTools วัดจริงบน web (localhost:3002/chat)

---

## Fix 1: List Items ห่างเกินไป + bullet "•" ใหญ่เกินไป

**ปัญหา:**  
Desktop แสดง "•" ตัวใหญ่ (FontSize 16) สี TextSecondary + ช่องว่างระหว่าง items เยอะ  
Web ใช้ `list-style: none` (ไม่มี bullet เลยสำหรับ task list) แต่ regular list ใช้ disc ตัวเล็กมากจาก browser default + spacing แน่นกว่ามาก

**ค่าจริงจาก web (globals.css + computed):**
- `ul` — `list-style-type: disc` (browser default, ไม่ได้ override), dot เล็กมาก
- `li` — `margin: 4px 0` (0.25rem)
- `li > p` — `margin: 0.15rem 0` = 2.4px (แทบไม่มี)
- `ul` — `padding-left: 22.4px`, `margin: 8px 0 16px`
- Item height ≈ 28px (line-height)
- **Gap ระหว่าง items = 4px** (จาก DOM measurement: item bottom → next item top = 4px)

**แก้ `MarkdownRenderer.cs` method `RenderList`:**

### 1a. ลด bullet ให้เล็กลง

เปลี่ยน "•" (U+2022) เป็น "·" (U+00B7 middle dot) หรือลด FontSize ของ bullet:

```csharp
var bulletText = list.IsOrdered ? $"{index}." : "·";  // was "•", now middle dot "·"
var bullet = new TextBlock
{
    Text = bulletText,
    FontSize = 14,          // was 16 → ลดลง
    Margin = new Thickness(0, 2, 6, 0),  // was 0,1,8,0 → tighten
    MinWidth = 12           // was 16 → ลดลง
};
```

### 1b. ลด spacing ระหว่าง items

```csharp
// Row (list item container) — web: li margin 0.25rem 0 = 4px
var row = new Grid { Margin = new Thickness(0, 2, 0, 2) };  // was 0,4,0,4 → ลดเหลือ 2+2=4px gap
```

### 1c. ลด paragraph margin ภายใน list items

```csharp
// สำหรับ li > p → web uses margin: 0.15rem 0 = ~2.4px
// ใน loop ที่ render child blocks ของ list item:
foreach (var child in item)
{
    var rendered = RenderBlock(child);
    if (rendered is not null)
    {
        // Override paragraph margin inside list items to match web's li > p
        if (rendered is TextBlock tb)
            tb.Margin = new Thickness(0, 2, 0, 2);  // much tighter than normal paragraph 0,0,0,16
        content.Children.Add(rendered);
    }
}
// Keep the existing last-child margin-0 trick (line 270-271)
```

**ทำไมสำคัญ:** ตอนนี้ list items บน desktop ดู "spacious" กว่า web มาก — ช่องว่างระหว่าง Item 1, Item 2, Item 3 ชัดเจนเกินไป

---

## Fix 2: Table กว้างเกินไป (ต้อง auto-fit ตาม content)

**ปัญหา:**  
Desktop table ยืด outer container เต็ม max-width ของ conversation column ทำให้มีพื้นที่ว่างด้านขวาเยอะมาก  
Web table auto-fit ตาม content — กว้างแค่พอดี columns

**ค่าจริงจาก web (computed):**
- Table `width: auto` (ไม่มี explicit width)
- Table ไม่มี `max-width`
- Border outer: ไม่มี outer border wrapper แบบ desktop — แต่ละ cell มี border เอง
- Web table: `border-collapse: collapse` style

**แก้ `MarkdownRenderer.cs` method `RenderTable`:**

```csharp
// เปลี่ยน outer Border → ไม่ใช้ ScrollViewer wrap (ไม่จำเป็นถ้า table auto-width)
// และเพิ่ม HorizontalAlignment = Left เพื่อไม่ให้ stretch

var outer = new Border
{
    Child = grid,  // ไม่ต้อง wrap ด้วย ScrollViewer ถ้า table ไม่ใหญ่
    BorderThickness = new Thickness(1),
    CornerRadius = new CornerRadius(6),
    Margin = new Thickness(0, 12, 0, 12),
    HorizontalAlignment = HorizontalAlignment.Left  // ← เพิ่มบรรทัดนี้!
};
outer.SetResourceReference(Border.BorderBrushProperty, "BorderBrush");
```

**Key change:** เพิ่ม `HorizontalAlignment = HorizontalAlignment.Left`  
สาเหตุที่ table stretch คือ WPF Grid default `HorizontalAlignment = Stretch` ทำให้ outer Border กินพื้นที่ทั้ง column  
เปลี่ยนเป็น `Left` จะทำให้ table กว้างแค่พอดี content (auto-fit)

**ทางเลือก:** ถ้ายังอยากให้ scroll ได้สำหรับ table ใหญ่:

```csharp
var outer = new Border
{
    Child = new ScrollViewer
    {
        Content = grid,
        HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
        VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
        HorizontalAlignment = HorizontalAlignment.Left  // ← key
    },
    BorderThickness = new Thickness(1),
    CornerRadius = new CornerRadius(6),
    Margin = new Thickness(0, 12, 0, 12),
    HorizontalAlignment = HorizontalAlignment.Left  // ← key
};
```

---

## Fix 3: Action Button Order (ต้องเรียงตาม web)

**ปัญหา:**  
Desktop: Copy → **Regenerate** → Thumbs Up → Thumbs Down  
Web: Copy → **Thumbs Up** → **Thumbs Down** → **Regenerate**

Web ให้ feedback buttons (thumbs) อยู่ตรงกลาง, regenerate อยู่ท้ายสุด

**แก้ `MessageBubble.xaml` — สลับลำดับ buttons ใน assistant action StackPanel:**

เปลี่ยนจาก:
```xml
<!-- Current order (wrong): Copy, Regenerate, ThumbUp, ThumbDown -->
<Button ... ToolTip="Copy" .../>
<Button ... ToolTip="Regenerate" .../>
<Button ... ToolTip="Good response" .../>
<Button ... ToolTip="Bad response" .../>
```

เป็น:
```xml
<!-- Web order: Copy, ThumbUp, ThumbDown, Regenerate -->
<Button ... ToolTip="Copy" .../>
<Button ... ToolTip="Good response" .../>
<Button ... ToolTip="Bad response" .../>
<Button ... ToolTip="Regenerate" .../>
```

**วิธีทำ:** ย้าย Regenerate button block (lines ~244-249) ไปอยู่หลัง ThumbDown button block (lines ~267-283)

เอา block นี้:
```xml
<Button Style="{StaticResource ActionIconButton}" ToolTip="Regenerate"
        Command="{Binding DataContext.RegenerateCommand, RelativeSource={RelativeSource AncestorType={x:Type ItemsControl}}}">
    <controls:IconView Geometry="{StaticResource Geo_Regenerate}"
                       Stroke="{DynamicResource TextSecondaryBrush}"
                       Width="18" Height="18" />
</Button>
```

ย้ายไปอยู่หลัง Bad response button (หลัง line ~283)

---

## Fix 4: Action Button Gap (2px ไม่ใช่ 4px)

**ปัญหา:**  
Desktop action StackPanel ใช้ default spacing  
Web: `gap: 2px` (class `gap-0.5`)

**ค่าจริงจาก web:**
- Parent container: `gap: 2px`
- Button: `30x30`, `border-radius: 4px`, `padding: 6px`

**แก้ `MessageBubble.xaml` — assistant action StackPanel:**

StackPanel ใน WPF ไม่มี `gap` property โดยตรง ต้องใช้ Margin ของ buttons หรือใช้ UniformGrid/WrapPanel

ถ้า buttons ปัจจุบันมี Margin ค่าใดอยู่ → ลดให้เหลือ `Margin="1,0"` (2px gap = 1px per side)

หรือถ้า StackPanel Orientation="Horizontal" → set Margin ของแต่ละ button:
```xml
<!-- ทุก ActionIconButton ใน assistant action row -->
<Button Style="{StaticResource ActionIconButton}" ToolTip="Copy" Margin="0,0,2,0" .../>
<Button Style="{StaticResource ActionIconButton}" ToolTip="Good response" Margin="0,0,2,0" .../>
<Button Style="{StaticResource ActionIconButton}" ToolTip="Bad response" Margin="0,0,2,0" .../>
<Button Style="{StaticResource ActionIconButton}" ToolTip="Regenerate" Margin="0" .../>
```

หรือถ้า ActionIconButton style มี Margin อยู่แล้ว → ตรวจสอบและปรับให้ gap ≈ 2px

---

## Fix 5: Sidebar — ลบ Empty Conversations

**ปัญหา:**  
Sidebar มี "New conversation" ว่างๆ ค้างอยู่ 10+ อัน ทำให้ดูรก (เทียบกับ web ที่มีแต่ conversation ที่มี content)

**สถานะ:** ปุ่ม "Clean up empty chats" ใน Settings → Advanced **มีอยู่แล้ว** และทำงานได้ (ทดสอบแล้วพบ 24 conversations ว่าง)

**สิ่งที่ต้องทำ:**  
เพิ่ม **auto-cleanup ตอน app startup** — ไม่ต้องรอให้ user กดปุ่มเอง

```csharp
// ใน App.xaml.cs หรือ MainWindow.xaml.cs ตอน Loaded:
private async void OnMainWindowLoaded(object sender, RoutedEventArgs e)
{
    // ... existing initialization ...
    
    // Auto-cleanup empty conversations (silent, no confirm dialog)
    await CleanupEmptyConversationsSilentAsync();
}

private async Task CleanupEmptyConversationsSilentAsync()
{
    try
    {
        var conversations = _conversationListVm.Conversations.ToList();
        foreach (var conv in conversations)
        {
            if (conv == _conversationListVm.SelectedConversation) continue;
            
            // Only delete if title is still "New conversation" (untitled = never used)
            if (conv.Title != "New conversation" && !string.IsNullOrEmpty(conv.Title)) continue;
            
            var msgs = await _conversationStore.GetMessagesAsync(conv.Id);
            if (msgs == null || !msgs.Any())
            {
                try { await _chatEdgeClient.DeleteConversationAsync(conv.Id); } catch { }
                await _conversationStore.DeleteConversationAsync(conv.Id);
                _conversationListVm.Conversations.Remove(conv);
            }
        }
    }
    catch { /* silent cleanup — don't crash app on failure */ }
}
```

**ทางเลือก (ง่ายกว่า):** ถ้าไม่อยากเพิ่ม auto-cleanup → แค่ run ปุ่ม Clean up ที่มีอยู่แล้วใน Settings ก็ได้ (manual)

---

## SUMMARY TABLE

| # | Fix | File | What Changes |
|---|-----|------|--------------|
| 1 | List spacing + bullet | `MarkdownRenderer.cs` | bullet "•"→"·" + FontSize 14, row margin 0,2,0,2, li>p margin 0,2,0,2 |
| 2 | Table auto-width | `MarkdownRenderer.cs` | เพิ่ม `HorizontalAlignment.Left` ที่ outer Border |
| 3 | Action button order | `MessageBubble.xaml` | ย้าย Regenerate ไปท้ายสุด (Copy→ThumbUp→ThumbDown→Regen) |
| 4 | Action button gap | `MessageBubble.xaml` | เพิ่ม `Margin="0,0,2,0"` ให้ buttons (gap 2px) |
| 5 | Sidebar cleanup | `App.xaml.cs` / `MainWindow.xaml.cs` | Auto-cleanup empty conversations on startup |

## VERIFICATION CHECKLIST

1. [ ] List items แน่นเหมือน web (gap ~4px, ไม่ spacious)
2. [ ] Bullet dot เล็ก/subtle (ไม่ใช่ "•" ตัวใหญ่)
3. [ ] Table auto-fit ตาม content (ไม่ stretch เต็ม column)
4. [ ] Action button order: Copy → ThumbUp → ThumbDown → Regenerate
5. [ ] Action button gap ≈ 2px
6. [ ] Sidebar ไม่มี "New conversation" ว่างๆ
7. [ ] 98 tests ยังผ่าน
8. [ ] Dark + Light theme ทั้งคู่ดูถูกต้อง

---

*End of Final 5 Fixes Prompt*
