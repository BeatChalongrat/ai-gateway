# SRAN Desktop — Chat Message Layout Fix (Pixel-Perfect Web Parity)

> ให้ Claude Fable ทำ พร้อม ai-gateway codebase attached  
> ค่าทุกอย่างมาจาก web source จริง (globals.css, MessageBubble.tsx, ChatThread.tsx)  
> เทียบกับ desktop source จริง (MessageBubble.xaml, MarkdownRenderer.cs, ChatView.xaml)

---

## ค่าที่ต้องแก้ (Desktop → Web)

### 1. Conversation Column Width

**Desktop:** MaxWidth="860"  
**Web:** max-w-3xl = **768px**

**แก้ `ChatView.xaml`:**
```xml
<!-- เปลี่ยน MessageBubble หรือ ItemsControl container -->
MaxWidth="768"  <!-- was 860 -->
```

---

### 2. Message Gap (ระยะห่างระหว่าง messages)

**Desktop:** Root Grid `Margin="0,6"` = 12px gap  
**Web:** `gap-4` = **16px** + extra 8px เมื่อ role เปลี่ยน (user→assistant หรือ assistant→user) = **24px**

**แก้ `MessageBubble.xaml` root Grid:**
```xml
Margin="0,8"  <!-- was 0,6 → now 16px gap base -->
```

**เพิ่ม extra spacing เมื่อ role เปลี่ยน:**
- เพิ่ม property `IsRoleChange` (bool) ใน MessageBubble
- ถ้า `IsRoleChange=True` → Margin="0,12" แทน "0,8" (เพิ่ม 8px = total 24px)

หรือวิธีง่ายกว่า — ใช้ `Margin="0,12"` ตลอด (24px gap) ก็ใกล้เคียง web

---

### 3. User Bubble Line-Height

**Desktop:** ไม่มี explicit LineHeight (default ~20px)  
**Web:** `leading-relaxed` = **line-height 1.625 = ~24.4px**

**แก้ `MessageBubble.xaml` user message TextBlock:**
```xml
<TextBlock ... FontSize="15" LineHeight="24" 
           LineStackingStrategy="BlockLineHeight" />
```

---

### 4. User Bubble MaxWidth

**Desktop:** MaxWidth="620" (fixed)  
**Web:** `max-w-[85%]` ของ container (85% ของ 768 = 653px)

**แก้ `MessageBubble.xaml`:**
```xml
<!-- User message StackPanel -->
MaxWidth="653"  <!-- was 620, now 85% of 768 -->
```

หรือดีกว่า — ใช้ binding กับ parent width:
```xml
MaxWidth="{Binding ActualWidth, RelativeSource={RelativeSource AncestorType=ItemsControl}, 
           Converter={StaticResource MultiplyConverter}, ConverterParameter=0.85}"
```

---

### 5. Assistant MaxWidth

**Desktop:** MaxWidth="740"  
**Web:** `w-full` (100% ของ 768px container)

**แก้ `MessageBubble.xaml`:**
```xml
<!-- Assistant message StackPanel -->
MaxWidth="768"  <!-- was 740, match container width -->
```

---

### 6. Paragraph Margin (สำคัญ — ทำให้ text แน่นเกินไป)

**Desktop:** Paragraph Margin = `0,3,0,3` (3px top + 3px bottom = 6px between paragraphs)  
**Web:** `margin: 0 0 1.1em 0` = **0 top, ~17.6px bottom** (last-child 0)

**แก้ `MarkdownRenderer.cs` paragraph rendering:**
```csharp
// เปลี่ยนจาก:
paragraph.Margin = new Thickness(0, 3, 0, 3);
// เป็น:
paragraph.Margin = new Thickness(0, 0, 0, 16); // ~1em bottom spacing

// สำหรับ last paragraph in a block → Margin 0 (no bottom)
// ถ้าทำยาก → ใช้ Margin(0, 0, 0, 14) ทุก paragraph ก็ได้
```

---

### 7. Heading Sizes

**Desktop → Web adjustments:**

| Heading | Desktop | Web | Change |
|---------|---------|-----|--------|
| H1 | 28 | 24 (1.5rem) | **ลดเป็น 24** |
| H2 | 22 | 20 (1.25rem) | **ลดเป็น 20** |
| H3 | 18 | 17.6 (1.1rem) | **ลดเป็น 17** (ปัดลง) |

**แก้ `MarkdownRenderer.cs`:**
```csharp
case 1: text.FontSize = 24; break;  // was 28
case 2: text.FontSize = 20; break;  // was 22
case 3: text.FontSize = 17; break;  // was 18
```

**Heading margins ก็ต้องแก้:**

**Desktop:** H1/H2 = `0,10,0,4`, H3 = `0,8,0,4`  
**Web:** `margin: 1.2em 0 0.5em` = ~19px top, 8px bottom

```csharp
// ทุก heading:
heading.Margin = new Thickness(0, 18, 0, 8);
```

---

### 8. Code Block Styling

| Property | Desktop | Web | Change |
|----------|---------|-----|--------|
| Font-size | 13 | 14px | **เพิ่มเป็น 14** |
| Border-radius | 8 | 12px | **เพิ่มเป็น 12** |
| Margin | 0,6,0,6 | 12px 0 | **เพิ่มเป็น 0,12,0,12** |
| Border | none | 1px solid | **เพิ่ม border** |
| Shadow | none | 0 1px 3px rgba(0,0,0,0.2) | **เพิ่ม shadow** |

**แก้ `MarkdownRenderer.cs` code block:**
```csharp
// Code text font size
codeRun.FontSize = 14;  // was 13

// Code block outer border
outerBorder.CornerRadius = new CornerRadius(12);  // was 8
outerBorder.Margin = new Thickness(0, 12, 0, 12);  // was 0,6,0,6
outerBorder.BorderThickness = new Thickness(1);
outerBorder.SetResourceReference(Border.BorderBrushProperty, "BorderBrush");
outerBorder.Effect = new DropShadowEffect 
{ 
    ShadowDepth = 1, BlurRadius = 3, Opacity = 0.2, Color = Colors.Black 
};

// Code block header CornerRadius
headerBorder.CornerRadius = new CornerRadius(12, 12, 0, 0);  // was 8,8,0,0

// Code block body CornerRadius
bodyBorder.CornerRadius = new CornerRadius(0, 0, 12, 12);  // was 0,0,8,8
```

**Inline code font-size:**
```csharp
inlineCode.FontSize = 14;  // was 13
```

---

### 9. List Styling

**Web:** `padding-left: 1.4rem` (22.4px), item `margin: 0.25rem 0` (4px)

**แก้ `MarkdownRenderer.cs`:**
```csharp
// List container
list.Margin = new Thickness(0, 8, 0, 16);  // web: 0.5rem 0 1rem
list.Padding = new Thickness(22, 0, 0, 0);  // web: 1.4rem left

// List item
listItem.Margin = new Thickness(0, 4, 0, 4);  // web: 0.25rem 0
```

---

### 10. Action Row

| Property | Desktop | Web | Change |
|----------|---------|-----|--------|
| Button size | 26x26 | ~30x30 (padding 6px + 18px icon) | **เพิ่มเป็น 30x30** |
| Icon size | 14x14 | 18x18 | **เพิ่มเป็น 18x18** |
| Button radius | 6 | 4px | **ลดเป็น 4** |
| Top margin | 4px | 8px (mt-2) | **เพิ่มเป็น 8** |

**แก้ `MessageBubble.xaml` ActionIconButton style:**
```xml
<Style x:Key="ActionIconButton" TargetType="Button">
    <Setter Property="Width" Value="30" />      <!-- was 26 -->
    <Setter Property="Height" Value="30" />     <!-- was 26 -->
    <!-- CornerRadius ใน template: 4 แทน 6 -->
</Style>
```

**Icon sizes ใน action buttons:**
```xml
<controls:IconView ... Width="18" Height="18" />  <!-- was 14 -->
```

**Action row margin:**
```xml
<!-- Assistant action StackPanel -->
<StackPanel ... Margin="-4,8,0,0">  <!-- was -4,4,0,0 -->
```

---

### 11. Blockquote

**Desktop:** `BorderThickness="3,0,0,0"`, `Padding="10,2,0,2"`, `Margin="0,4,0,4"`  
**Web:** `border-left: 3px solid`, `padding: 0.4rem 0.9rem` (6.4px 14.4px), `margin: 0.8rem 0` (12.8px), `border-radius: 0 8px 8px 0`

```csharp
blockquote.BorderThickness = new Thickness(3, 0, 0, 0);  // same
blockquote.Padding = new Thickness(14, 6, 14, 6);  // was 10,2,0,2
blockquote.Margin = new Thickness(0, 12, 0, 12);  // was 0,4,0,4
blockquote.CornerRadius = new CornerRadius(0, 8, 8, 0);  // add border-radius
```

---

### 12. Table Styling

**Desktop:** cell `Padding="10,5,10,5"`, no specific font-size  
**Web:** `font-size: 0.9rem` (14.4px), cell `padding: 0.5rem 0.75rem` (8px 12px), `margin: 0.8rem 0` (12.8px)

```csharp
// Table text
tableText.FontSize = 14;  // web: 0.9rem

// Table cell padding
cellBorder.Padding = new Thickness(12, 8, 12, 8);  // was 10,5,10,5

// Table margin
tableBorder.Margin = new Thickness(0, 12, 0, 12);  // was 0,6,0,6
```

---

### 13. Horizontal Rule

**Desktop:** Height=1, `Margin="0,8,0,8"`  
**Web:** `margin: 1.25rem 0` (20px)

```csharp
hr.Margin = new Thickness(0, 20, 0, 20);  // was 0,8,0,8
```

---

### 14. Cleanup Empty Conversations (Bonus)

เพิ่มปุ่ม "Clean up empty chats" ใน Settings dialog (Advanced section):

```xml
<Button Content="Clean up empty chats" Margin="0,10,0,0"
        Padding="12,6" HorizontalAlignment="Left"
        Background="Transparent"
        BorderBrush="{DynamicResource BorderBrush}"
        Command="{Binding CleanupEmptyChatsCommand}" />
```

**ViewModel logic:**
```csharp
[RelayCommand]
private async Task CleanupEmptyChatsAsync()
{
    var emptyConversations = new List<ConversationViewModel>();
    
    foreach (var conv in _conversationListVm.Conversations.ToList())
    {
        if (conv == _conversationListVm.SelectedConversation) continue;
        var msgs = await _conversationStore.GetMessagesAsync(conv.Id);
        if (msgs == null || !msgs.Any())
            emptyConversations.Add(conv);
    }
    
    if (emptyConversations.Count == 0)
    {
        // Show "No empty conversations found"
        return;
    }
    
    // Confirm dialog
    var result = MessageBox.Show(
        $"Delete {emptyConversations.Count} empty conversations?",
        "Clean up", MessageBoxButton.YesNo);
    
    if (result == MessageBoxResult.Yes)
    {
        foreach (var conv in emptyConversations)
        {
            try { await _chatEdgeClient.DeleteConversationAsync(conv.Id); } catch { }
            await _conversationStore.DeleteConversationAsync(conv.Id);
            _conversationListVm.Conversations.Remove(conv);
        }
    }
}
```

---

## SUMMARY: ทุก value ที่ต้องเปลี่ยน

| File | Property | Old | New |
|------|----------|-----|-----|
| ChatView.xaml | Container MaxWidth | 860 | **768** |
| MessageBubble.xaml | Root Margin | 0,6 | **0,8** (หรือ 0,12 ถ้าอยาก spacious) |
| MessageBubble.xaml | User TextBlock LineHeight | (none) | **24** |
| MessageBubble.xaml | User StackPanel MaxWidth | 620 | **653** (85% of 768) |
| MessageBubble.xaml | Assistant StackPanel MaxWidth | 740 | **768** |
| MessageBubble.xaml | ActionIconButton Width/Height | 26 | **30** |
| MessageBubble.xaml | Action icon Width/Height | 14 | **18** |
| MessageBubble.xaml | Action button CornerRadius | 6 | **4** |
| MessageBubble.xaml | Action row Margin top | 4 | **8** |
| MarkdownRenderer.cs | Paragraph Margin | 0,3,0,3 | **0,0,0,16** |
| MarkdownRenderer.cs | H1 FontSize | 28 | **24** |
| MarkdownRenderer.cs | H2 FontSize | 22 | **20** |
| MarkdownRenderer.cs | H3 FontSize | 18 | **17** |
| MarkdownRenderer.cs | Heading Margin | 0,10,0,4 | **0,18,0,8** |
| MarkdownRenderer.cs | Code FontSize | 13 | **14** |
| MarkdownRenderer.cs | Code block CornerRadius | 8 | **12** |
| MarkdownRenderer.cs | Code block Margin | 0,6,0,6 | **0,12,0,12** |
| MarkdownRenderer.cs | Inline code FontSize | 13 | **14** |
| MarkdownRenderer.cs | Blockquote Padding | 10,2,0,2 | **14,6,14,6** |
| MarkdownRenderer.cs | Blockquote Margin | 0,4,0,4 | **0,12,0,12** |
| MarkdownRenderer.cs | List Margin | 4,3,0,3 | **0,8,0,16** |
| MarkdownRenderer.cs | List item Margin | - | **0,4,0,4** |
| MarkdownRenderer.cs | Table FontSize | (16) | **14** |
| MarkdownRenderer.cs | Table cell Padding | 10,5,10,5 | **12,8,12,8** |
| MarkdownRenderer.cs | Table Margin | 0,6,0,6 | **0,12,0,12** |
| MarkdownRenderer.cs | HR Margin | 0,8,0,8 | **0,20,0,20** |
| MarkdownRenderer.cs | Code block border | none | **1px BorderBrush** |
| MarkdownRenderer.cs | Code block shadow | none | **DropShadow** |

## VERIFICATION CHECKLIST

1. [ ] Message gap ≈ 16px (ไม่แน่นเกินไป)
2. [ ] User bubble text มี line-height ~24px (อ่านง่าย)
3. [ ] Paragraph spacing ~16px ระหว่าง paragraphs (ไม่ติดกัน)
4. [ ] Headings ขนาดถูกต้อง: H1=24, H2=20, H3=17
5. [ ] Code blocks มี border + shadow + radius 12
6. [ ] Code font size = 14px
7. [ ] Action buttons = 30x30 กับ icon 18x18
8. [ ] Conversation column max-width = 768px
9. [ ] Light theme code blocks มี light bg (ไม่ใช่ dark)
10. [ ] 98 tests ยังผ่าน
11. [ ] Dark + Light theme ทั้งคู่ดูถูกต้อง

---

*End of Chat Layout Fix Prompt*
