# SRAN Desktop — 4 Critical Visual Fixes (Web Parity Round 2)

> ให้ Claude Fable ทำ พร้อม ai-gateway codebase attached  
> เปรียบเทียบกับ web จริงที่ localhost:3002/chat  
> **เป้าหมาย: desktop ต้องดูเหมือน web ทุกประการ**

---

## Fix 1: กด New Chat แล้วไม่ขึ้น Empty State (ต้องเห็น Logo + Greeting)

### ปัญหา
Desktop: กด "+ New chat" → สร้าง conversation ใหม่ทันที → เห็น ChatView ว่างเปล่า (ไม่มี logo, ไม่มี greeting, ไม่มี suggestion chips)  
Web: กด "+ New chat" → แสดง empty state มี SRAN logo + "Good evening, Somchai" + centered composer + Write/Learn/Code/Analyze chips → สร้าง conversation จริงเมื่อ user ส่ง message แรก

### สาเหตุ
`MainWindow.xaml` line 210: empty state trigger `DataTrigger Binding="{Binding ActiveConversation}" Value="{x:Null}"` → empty state แสดงเฉพาะเมื่อ ActiveConversation เป็น null  
แต่ `NewChatAsync()` สร้าง conversation ทันที → ActiveConversation ไม่เป็น null → empty state ซ่อน

### วิธีแก้ (2 ทางเลือก)

**ทางเลือก A (แนะนำ): Lazy conversation creation**  
ไม่สร้าง conversation ตอนกด New chat → สร้างตอน user ส่ง message แรก

```csharp
// ConversationListViewModel.cs
[RelayCommand]
private async Task NewChatAsync()
{
    // ถ้า conversation ปัจจุบันว่าง → ไม่ต้องสร้างใหม่
    if (SelectedConversation != null)
    {
        var messages = await _conversationStore.GetMessagesAsync(SelectedConversation.Id);
        if (messages == null || !messages.Any())
            return;
    }
    
    // *** ไม่สร้าง conversation ใหม่ที่นี่ ***
    // แค่ set ActiveConversation = null เพื่อแสดง empty state
    SelectedConversation = null;
    // MainViewModel ควร set ActiveConversation = null ด้วย
}
```

```csharp
// ChatViewModel.cs — SendMessageAsync()
// ตอน send message แรก ถ้ายังไม่มี conversation → สร้างที่นี่
private async Task SendMessageAsync()
{
    if (Conversation == null)
    {
        // Lazy create conversation on first message
        var dto = await _chatEdgeClient.CreateConversationAsync();
        var conv = new ConversationViewModel { Id = dto.Id, Title = dto.Title ?? "New conversation" };
        await _conversationStore.SaveConversationAsync(conv);
        _conversationListVm.Conversations.Insert(0, conv);
        _conversationListVm.SelectedConversation = conv;
        Conversation = conv;
    }
    
    // ... rest of existing send logic
}
```

**ทางเลือก B (ง่ายกว่า): เปลี่ยน visibility trigger**  
ให้ empty state แสดงเมื่อ conversation ว่าง (0 messages) แทนที่จะเช็ค null

```xml
<!-- MainWindow.xaml — เปลี่ยน trigger ของ empty state -->
<StackPanel.Style>
    <Style TargetType="StackPanel">
        <Setter Property="Visibility" Value="Collapsed" />
        <Style.Triggers>
            <!-- แสดงเมื่อ ActiveConversation = null -->
            <DataTrigger Binding="{Binding ActiveConversation}" Value="{x:Null}">
                <Setter Property="Visibility" Value="Visible" />
            </DataTrigger>
            <!-- หรือเมื่อ Chat ไม่มี message -->
            <DataTrigger Binding="{Binding Chat.Messages.Count}" Value="0">
                <Setter Property="Visibility" Value="Visible" />
            </DataTrigger>
        </Style.Triggers>
    </Style>
</StackPanel.Style>
```

และเพิ่ม trigger ให้ ChatView ซ่อนเมื่อ Messages.Count = 0:
```xml
<!-- MainWindow.xaml — ChatView -->
<views:ChatView DataContext="{Binding Chat}">
    <views:ChatView.Style>
        <Style TargetType="views:ChatView">
            <Setter Property="Visibility" Value="Visible" />
            <Style.Triggers>
                <DataTrigger Binding="{Binding Conversation}" Value="{x:Null}">
                    <Setter Property="Visibility" Value="Collapsed" />
                </DataTrigger>
                <DataTrigger Binding="{Binding Messages.Count}" Value="0">
                    <Setter Property="Visibility" Value="Collapsed" />
                </DataTrigger>
            </Style.Triggers>
        </Style>
    </views:ChatView.Style>
</views:ChatView>
```

---

## Fix 2: Chat Layout ไม่เหมือน Web (Action Buttons + Spacing)

### ปัญหา 2a: Action buttons ต้องแสดงแบบ web
**Desktop (ปัจจุบัน):** ทุก assistant message แสดง 4 buttons: Copy → ThumbUp → ThumbDown → Regenerate  
**Web:** 
- Message ทั่วไป: **3 buttons** — Copy, Helpful, Not helpful (ไม่มี Regenerate)
- **เฉพาะ message สุดท้าย** ของ assistant: Copy, Helpful, Not helpful, **Regenerate**

### วิธีแก้ 2a

เพิ่ม property `IsLastAssistantMessage` ใน `MessageViewModel`:

```csharp
// MessageViewModel.cs
[ObservableProperty]
private bool _isLastAssistantMessage;
```

อัปเดตค่าทุกครั้งที่ Messages collection เปลี่ยน:
```csharp
// ChatViewModel.cs — หลังจากเพิ่ม/ลบ message
private void UpdateLastAssistantFlag()
{
    MessageViewModel? lastAssistant = null;
    foreach (var msg in Messages)
    {
        if (msg.Role == "assistant" && !msg.IsError)
        {
            if (lastAssistant != null) lastAssistant.IsLastAssistantMessage = false;
            lastAssistant = msg;
        }
    }
    if (lastAssistant != null) lastAssistant.IsLastAssistantMessage = true;
}
```

ใน `MessageBubble.xaml` — ซ่อน Regenerate button ถ้าไม่ใช่ message สุดท้าย:

```xml
<!-- Regenerate button (last assistant message only — web parity) -->
<Button Style="{StaticResource ActionIconButton}" ToolTip="Regenerate"
        Command="..." Margin="0">
    <Button.Style>
        <Style TargetType="Button" BasedOn="{StaticResource ActionIconButton}">
            <Setter Property="Visibility" Value="Collapsed" />
            <Style.Triggers>
                <DataTrigger Binding="{Binding IsLastAssistantMessage}" Value="True">
                    <Setter Property="Visibility" Value="Visible" />
                </DataTrigger>
            </Style.Triggers>
        </Style>
    </Button.Style>
    <controls:IconView Geometry="{StaticResource Geo_Regenerate}" ... />
</Button>
```

### ปัญหา 2b: Action buttons ควร hover-only (ไม่แสดงตลอดเวลา)
**Web:** action buttons ซ่อนอยู่ → แสดงเมื่อ hover message  
**Desktop:** action buttons แสดงตลอดเวลา

**วิธีแก้:** เพิ่ม hover trigger ใน assistant action StackPanel:

```xml
<StackPanel x:Name="AssistantActions" Orientation="Horizontal" ...>
    <StackPanel.Style>
        <Style TargetType="StackPanel">
            <Setter Property="Opacity" Value="0" />
            <Style.Triggers>
                <!-- Show on hover over the message area -->
                <DataTrigger Binding="{Binding IsMouseOver, 
                    RelativeSource={RelativeSource AncestorType=controls:MessageBubble}}" 
                    Value="True">
                    <Setter Property="Opacity" Value="1" />
                </DataTrigger>
                <!-- Always show for last assistant message (web behavior) -->
                <DataTrigger Binding="{Binding IsLastAssistantMessage}" Value="True">
                    <Setter Property="Opacity" Value="1" />
                </DataTrigger>
            </Style.Triggers>
        </Style>
    </StackPanel.Style>
    ...
</StackPanel>
```

### ปัญหา 2c: User action buttons
**Web:** user message hover: Copy, **Edit** (2 buttons only)  
**Desktop:** ตรวจสอบว่าเหมือนกันแล้วหรือยัง — ถ้ายังไม่เหมือนให้แก้ให้ตรง

---

## Fix 3: Composer กว้างไม่พอ + ตำแหน่งไม่ตรง

### ปัญหา
Desktop: composer `Margin="20,4,20,10"` → มี padding ทั้ง 2 ข้าง 20px แต่ไม่ได้ center ภายใน 768px column  
Web: composer กว้าง **768px** (เต็ม max-w-3xl column) centered ใน content area

### วิธีแก้

```xml
<!-- ChatView.xaml — เปลี่ยน ComposerControl layout -->
<!-- จาก: -->
<controls:ComposerControl Grid.Row="3" Margin="20,4,20,10" />

<!-- เป็น: -->
<Grid Grid.Row="3" Margin="0,4,0,10">
    <controls:ComposerControl MaxWidth="768" HorizontalAlignment="Center" 
                               Margin="24,0" />
</Grid>
```

นี่จะทำให้ composer:
- `MaxWidth="768"` เหมือน message column
- `HorizontalAlignment="Center"` อยู่กลาง content area
- `Margin="24,0"` ป้องกันชิดขอบเมื่อจอเล็ก
- เมื่อขยายจอ composer จะอยู่กลางตรง messages พอดี

**Empty state composer ก็ต้องแก้ด้วย:**
```xml
<!-- MainWindow.xaml — empty state -->
<!-- จาก: -->
<controls:ComposerControl DataContext="{Binding Chat}" Margin="20,0" />

<!-- เป็น: -->
<controls:ComposerControl DataContext="{Binding Chat}" MaxWidth="768" Margin="0" 
                           HorizontalAlignment="Center" />
```

---

## Fix 4: ขยายเต็มจอแล้วตกขอบ (WindowChrome maximize bug)

### ปัญหา
เมื่อ maximize หน้าต่าง → content ไปอยู่หลัง taskbar หรือ edge ของจอ  
สาเหตุ: `WindowStyle="None"` + `GlassFrameThickness="0"` ทำให้ WPF ไม่รู้ขอบจอ

### วิธีแก้

**วิธี A: เปลี่ยน GlassFrameThickness**
```xml
<!-- MainWindow.xaml -->
<shell:WindowChrome.WindowChrome>
    <shell:WindowChrome CaptionHeight="32"
                        ResizeBorderThickness="6"
                        GlassFrameThickness="-1"
                        CornerRadius="0"
                        UseAeroCaptionButtons="False" />
</shell:WindowChrome.WindowChrome>
```

เปลี่ยน `GlassFrameThickness="0"` → `GlassFrameThickness="-1"`  
ค่า `-1` บอก WPF ให้ใช้ system glass frame → ป้องกัน maximize ล้นจอ

**วิธี B: Handle maximize padding ใน code-behind (ถ้า A ไม่ work)**

```csharp
// MainWindow.xaml.cs
protected override void OnStateChanged(EventArgs e)
{
    base.OnStateChanged(e);
    
    if (WindowState == WindowState.Maximized)
    {
        // ป้องกัน content ล้นออกนอกจอ
        var thickness = SystemParameters.WindowResizeBorderThickness;
        BorderThickness = new Thickness(
            thickness.Left + 4,
            thickness.Top + 4,
            thickness.Right + 4,
            thickness.Bottom + 4);
    }
    else
    {
        BorderThickness = new Thickness(0);
    }
}
```

**วิธี C: ใช้ MaximizedBounds (reliable ที่สุด)**

```csharp
// MainWindow.xaml.cs
protected override void OnSourceInitialized(EventArgs e)
{
    base.OnSourceInitialized(e);
    var source = PresentationSource.FromVisual(this) as HwndSource;
    source?.AddHook(WndProc);
}

private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
{
    // WM_GETMINMAXINFO = 0x0024
    if (msg == 0x0024)
    {
        WmGetMinMaxInfo(hwnd, lParam);
        handled = true;
    }
    return IntPtr.Zero;
}

private static void WmGetMinMaxInfo(IntPtr hwnd, IntPtr lParam)
{
    var mmi = Marshal.PtrToStructure<MINMAXINFO>(lParam);
    
    var monitor = MonitorFromWindow(hwnd, 0x00000002 /* MONITOR_DEFAULTTONEAREST */);
    if (monitor != IntPtr.Zero)
    {
        var monitorInfo = new MONITORINFO { cbSize = Marshal.SizeOf<MONITORINFO>() };
        GetMonitorInfo(monitor, ref monitorInfo);
        
        var workArea = monitorInfo.rcWork;
        var monitorArea = monitorInfo.rcMonitor;
        
        mmi.ptMaxPosition.X = workArea.Left - monitorArea.Left;
        mmi.ptMaxPosition.Y = workArea.Top - monitorArea.Top;
        mmi.ptMaxSize.X = workArea.Right - workArea.Left;
        mmi.ptMaxSize.Y = workArea.Bottom - workArea.Top;
    }
    
    Marshal.StructureToPtr(mmi, lParam, true);
}

// P/Invoke declarations needed:
[DllImport("user32.dll")]
private static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint flags);

[DllImport("user32.dll")]
private static extern bool GetMonitorInfo(IntPtr hMonitor, ref MONITORINFO lpmi);

[StructLayout(LayoutKind.Sequential)]
private struct POINT { public int X, Y; }

[StructLayout(LayoutKind.Sequential)]
private struct MINMAXINFO
{
    public POINT ptReserved;
    public POINT ptMaxSize;
    public POINT ptMaxPosition;
    public POINT ptMaxTrackSize;
    public POINT ptMinTrackSize;
}

[StructLayout(LayoutKind.Sequential)]
private struct RECT { public int Left, Top, Right, Bottom; }

[StructLayout(LayoutKind.Sequential)]
private struct MONITORINFO
{
    public int cbSize;
    public RECT rcMonitor;
    public RECT rcWork;
    public uint dwFlags;
}
```

**ลำดับ:** ลอง A ก่อน (1 บรรทัด) → ถ้าไม่ work ลอง B → ถ้ายังไม่ work ใช้ C

---

## SUMMARY TABLE

| # | Fix | File(s) | Effort |
|---|-----|---------|--------|
| 1 | Empty state on New chat | `ConversationListViewModel.cs`, `MainWindow.xaml`, `ChatViewModel.cs` | Medium |
| 2 | Action buttons: hover-only, 3 vs 4, last-only regenerate | `MessageBubble.xaml`, `MessageViewModel.cs`, `ChatViewModel.cs` | Medium |
| 3 | Composer centered + max-width 768 | `ChatView.xaml`, `MainWindow.xaml` | Easy |
| 4 | Maximize overflow fix | `MainWindow.xaml` (1 line) or `MainWindow.xaml.cs` | Easy-Medium |

## VERIFICATION CHECKLIST

1. [ ] กด New chat → เห็น SRAN logo + greeting + centered composer + pills (ไม่ใช่จอว่าง)
2. [ ] พิมพ์ message ใน empty state → สร้าง conversation + ส่ง message ได้
3. [ ] Hover message → action buttons ปรากฏ
4. [ ] Message ทั่วไป: Copy, ThumbUp, ThumbDown (3 buttons, ไม่มี Regenerate)
5. [ ] Message สุดท้ายของ assistant: Copy, ThumbUp, ThumbDown, **Regenerate** (4 buttons)
6. [ ] Regenerate ของ message สุดท้ายแสดงตลอด (ไม่ต้อง hover)
7. [ ] Composer กว้าง max 768px + centered ตรง messages
8. [ ] Maximize → content ไม่ล้นออกนอกจอ, ไม่ตกขอบ, taskbar ยังเห็น
9. [ ] Restore จาก maximize → กลับมาปกติ
10. [ ] Dark + Light theme ทั้งคู่ดูถูกต้อง
11. [ ] 98 tests ยังผ่าน

---

*End of 4 Critical Fixes Prompt*
