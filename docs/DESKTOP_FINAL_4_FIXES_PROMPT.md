# SRAN Desktop — Final 4 Fixes (จาก visual comparison)

> ให้ Claude Fable ทำ พร้อม ai-gateway codebase attached  
> เทียบจาก screenshot web (localhost:3002/chat) กับ desktop จริง  
> 4 จุดสุดท้ายที่ยังไม่ตรง

---

## Fix 1: Custom Title Bar — ลบ Windows Title Bar

**ปัญหา:** Desktop มี native Windows title bar สีเข้ม แสดง "SRAN Desktop" + icon + minimize/maximize/close buttons แบบ Windows ดูไม่เหมือน web app

**Web:** ไม่มี title bar เลย (มันอยู่ใน browser tab)

**เป้าหมาย:** ทำ custom title bar แบบ borderless ให้ดูเหมือน modern app (เหมือน Claude Desktop, Slack Desktop, Discord)

**แก้ `MainWindow.xaml`:**

```xml
<!-- Window properties -->
<Window ...
        WindowStyle="None"
        AllowsTransparency="False"
        ResizeMode="CanResizeWithGrip">
```

**เพิ่ม custom title bar ด้านบนสุดของ window content:**
```xml
<Grid>
    <Grid.RowDefinitions>
        <RowDefinition Height="32" />  <!-- custom title bar -->
        <RowDefinition Height="*" />   <!-- content -->
    </Grid.RowDefinitions>

    <!-- Custom Title Bar -->
    <Border Grid.Row="0" Background="{DynamicResource BgSidebarBrush}"
            MouseLeftButtonDown="TitleBar_MouseLeftButtonDown">
        <DockPanel LastChildFill="True">
            <!-- Window controls (right) -->
            <StackPanel DockPanel.Dock="Right" Orientation="Horizontal">
                <!-- Minimize -->
                <Button Width="46" Height="32" Background="Transparent" 
                        BorderThickness="0" Click="MinimizeButton_Click"
                        ToolTip="Minimize">
                    <Path Data="M 0 0 H 10" 
                          Stroke="{DynamicResource TextSecondaryBrush}" 
                          StrokeThickness="1" VerticalAlignment="Center" />
                </Button>
                <!-- Maximize/Restore -->
                <Button Width="46" Height="32" Background="Transparent"
                        BorderThickness="0" Click="MaximizeButton_Click"
                        ToolTip="Maximize">
                    <Path Data="M 0 0 H 10 V 10 H 0 Z" 
                          Stroke="{DynamicResource TextSecondaryBrush}" 
                          StrokeThickness="1" Stretch="None" />
                </Button>
                <!-- Close -->
                <Button x:Name="CloseBtn" Width="46" Height="32" 
                        Background="Transparent" BorderThickness="0" 
                        Click="CloseButton_Click" ToolTip="Close">
                    <Path Data="M 0 0 L 10 10 M 10 0 L 0 10" 
                          Stroke="{DynamicResource TextSecondaryBrush}" 
                          StrokeThickness="1" Stretch="None" />
                </Button>
            </StackPanel>

            <!-- Empty space for drag (title bar area) -->
            <Border Background="Transparent" />
        </DockPanel>
    </Border>

    <!-- Main content (existing layout) -->
    <Grid Grid.Row="1">
        <!-- ... sidebar + chat area ... -->
    </Grid>
</Grid>
```

**Close button hover = red:**
```xml
<Style TargetType="Button" x:Key="CloseButtonStyle">
    <Style.Triggers>
        <Trigger Property="IsMouseOver" Value="True">
            <Setter Property="Background" Value="#E81123" />
        </Trigger>
    </Style.Triggers>
</Style>
```

**Code-behind (MainWindow.xaml.cs):**
```csharp
private void TitleBar_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
{
    if (e.ClickCount == 2)
        MaximizeButton_Click(sender, e);
    else
        DragMove();
}

private void MinimizeButton_Click(object sender, RoutedEventArgs e)
    => WindowState = WindowState.Minimized;

private void MaximizeButton_Click(object sender, RoutedEventArgs e)
    => WindowState = WindowState == WindowState.Maximized 
        ? WindowState.Normal : WindowState.Maximized;

private void CloseButton_Click(object sender, RoutedEventArgs e)
    => Close(); // existing close-to-tray logic handles this
```

**สำคัญ:**
- Title bar height = **32px** (เท่ากับ Windows 11 standard)
- Title bar bg = **BgSidebarBrush** (#171717 dark) ให้กลืนกับ sidebar
- **ไม่แสดง "SRAN Desktop" text** — web ก็ไม่มี app name ใน content area
- Window controls (min/max/close) ยังต้องมีเพราะเป็น desktop app
- Double-click title bar = maximize/restore
- Drag title bar = move window
- **ResizeMode="CanResizeWithGrip"** — ให้ resize ได้จากขอบ

**ถ้า WindowStyle="None" ทำให้สูญเสีย snap/resize จากขอบ:** ใช้ `WindowChrome` แทน:
```xml
<WindowChrome.WindowChrome>
    <WindowChrome CaptionHeight="32" 
                  ResizeBorderThickness="5"
                  GlassFrameThickness="0"
                  UseAeroPeek="False"
                  CornerRadius="0" />
</WindowChrome.WindowChrome>
```
แล้ว mark window controls ด้วย `WindowChrome.IsHitTestVisibleInChrome="True"` — วิธีนี้ดีกว่าเพราะยังได้ Windows snap, aero peek, resize จากขอบ

---

## Fix 2: Empty State — เพิ่ม Composer ตรงกลาง

**ปัญหา:** Desktop empty state มี logo + greeting + chips แต่**ไม่มี composer** — composer จะปรากฏแค่เมื่อเปิด conversation แล้ว

**Web:** Composer อยู่ตรงกลาง empty state เลย — user พิมพ์ได้ทันทีโดยไม่ต้องกด New chat ก่อน

**Layout ที่ web ใช้ (จากบนลงล่าง, centered):**
```
        SRAN logo (96px)
   Good afternoon, Somchai (30px bold)
   How can I help you today? (18px)

   ┌─────────────────────────────────────┐
   │  Message SRAN Chat...              │
   │                                     │
   │  [+] [🤖] [📎]    [✦ Auto ▾] [⬆] │
   └─────────────────────────────────────┘
   SRAN Chat can make mistakes. Check important info.

   [✎ Write] [◎ Learn] [<> Code] [📊 Analyze]
```

**แก้ empty state ใน `MainWindow.xaml`:**

ต้องแสดง composer **ทั้งใน empty state และใน conversation view** — ไม่ใช่แค่ conversation view

**วิธีทำ:**

**Option A (ง่ายสุด):** เมื่อ user พิมพ์ใน empty state composer แล้วกด send → auto-create new conversation + ส่ง message ทันที

```xml
<!-- Empty state layout -->
<StackPanel HorizontalAlignment="Center" VerticalAlignment="Center"
            MaxWidth="720" Visibility="{Binding HasNoSelectedConversation, Converter=...}">
    
    <!-- Logo -->
    <Image Source="/Assets/sran-logo.png" Width="96" Height="96" 
           HorizontalAlignment="Center" Margin="0,0,0,16" />
    
    <!-- Greeting -->
    <TextBlock Text="{Binding Greeting}" FontSize="30" FontWeight="SemiBold"
               HorizontalAlignment="Center"
               Foreground="{DynamicResource TextPrimaryBrush}" />
    <TextBlock Text="How can I help you today?" FontSize="18" Margin="0,6,0,24"
               HorizontalAlignment="Center"
               Foreground="{DynamicResource TextSecondaryBrush}" />
    
    <!-- === COMPOSER (same as conversation composer) === -->
    <Border MaxWidth="720" CornerRadius="24" BorderThickness="1"
            BorderBrush="{DynamicResource BorderBrush}"
            Background="{DynamicResource BgSecondaryBrush}"
            Padding="16,12,16,8" Margin="20,0">
        <Border.Effect>
            <DropShadowEffect ShadowDepth="2" BlurRadius="12" Opacity="0.18" 
                              Color="Black" Direction="270" />
        </Border.Effect>
        <Grid>
            <Grid.RowDefinitions>
                <RowDefinition Height="*" />
                <RowDefinition Height="Auto" />
            </Grid.RowDefinitions>
            
            <!-- Input -->
            <TextBox Grid.Row="0"
                     Text="{Binding MessageText, UpdateSourceTrigger=PropertyChanged}"
                     AcceptsReturn="True" TextWrapping="Wrap"
                     MaxHeight="200" MinHeight="24" FontSize="15"
                     Background="Transparent" BorderThickness="0"
                     Foreground="{DynamicResource TextPrimaryBrush}"
                     CaretBrush="{DynamicResource TextPrimaryBrush}"
                     controls:WatermarkService.Text="Message SRAN Chat..." />
            
            <!-- Toolbar (same buttons as conversation composer) -->
            <DockPanel Grid.Row="1" Margin="0,6,0,0" LastChildFill="False">
                <StackPanel DockPanel.Dock="Left" Orientation="Horizontal">
                    <Button Style="{DynamicResource IconButton}" Width="30" Height="30"
                            Margin="0,0,2,0">
                        <controls:IconView Geometry="{StaticResource Geo_Plus}"
                                           Stroke="{DynamicResource TextSecondaryBrush}"
                                           Width="16" Height="16" />
                    </Button>
                    <ToggleButton IsChecked="{Binding IsAgentMode}"
                                  Style="{DynamicResource IconToggleButton}"
                                  Width="30" Height="30" Margin="0,0,2,0">
                        <controls:IconView Geometry="{StaticResource Geo_Agent}"
                                           Stroke="{DynamicResource TextSecondaryBrush}"
                                           Width="16" Height="16" />
                    </ToggleButton>
                    <Button Style="{DynamicResource IconButton}" Width="30" Height="30"
                            Command="{Binding AttachCommand}">
                        <controls:IconView Geometry="{StaticResource Geo_Attach}"
                                           Stroke="{DynamicResource TextSecondaryBrush}"
                                           Width="16" Height="16" />
                    </Button>
                </StackPanel>
                <StackPanel DockPanel.Dock="Right" Orientation="Horizontal">
                    <Button Style="{DynamicResource IconButton}" Padding="8,4" 
                            Height="26" Margin="0,0,8,0">
                        <StackPanel Orientation="Horizontal">
                            <controls:IconView Geometry="{StaticResource Geo_Sparkles}"
                                               Stroke="{DynamicResource TextSecondaryBrush}"
                                               Width="13" Height="13" Margin="0,0,5,0" />
                            <TextBlock Text="{Binding CurrentModelName}" FontSize="12"
                                       Foreground="{DynamicResource TextSecondaryBrush}" />
                            <controls:IconView Geometry="{StaticResource Geo_ChevronDown}"
                                               Stroke="{DynamicResource TextSecondaryBrush}"
                                               Width="10" Height="10" Margin="4,0,0,0" />
                        </StackPanel>
                    </Button>
                    <Button Width="32" Height="32" Command="{Binding SendCommand}">
                        <!-- same send button template -->
                    </Button>
                </StackPanel>
            </DockPanel>
        </Grid>
    </Border>

    <!-- Disclaimer -->
    <TextBlock Text="SRAN Chat can make mistakes. Check important info."
               FontSize="11" Margin="0,10,0,16" HorizontalAlignment="Center"
               Foreground="{DynamicResource TextTertiaryBrush}" />
    
    <!-- Suggestion chips (horizontal pill row) -->
    <WrapPanel HorizontalAlignment="Center">
        <!-- Write, Learn, Code, Analyze chips (keep existing) -->
    </WrapPanel>
</StackPanel>
```

**ViewModel logic สำหรับ send จาก empty state:**
```csharp
// ใน SendCommand handler:
if (CurrentConversation == null)
{
    // Auto-create new conversation then send
    await NewChatCommand.ExecuteAsync(null);
    // CurrentConversation is now set
}
await SendMessageAsync();
```

**Option B (ง่ายกว่า):** แชร์ composer control เดียวกันระหว่าง empty state และ conversation view — สร้าง `ComposerControl.xaml` UserControl แล้วใช้ทั้ง 2 ที่

---

## Fix 3: Greeting — ใช้ English + Display Name จาก Auth

**ปัญหา:** 
- Desktop: "Still up, สมชาย" — ใช้ชื่อภาษาไทย + greeting แปลกๆ ("Still up" ตอนดึก)
- Web: "Good afternoon, Somchai" — ใช้ชื่อ English + standard greeting

**แก้ `ChatViewModel.cs` หรือ `MainViewModel.cs` (wherever Greeting is computed):**

```csharp
public string Greeting
{
    get
    {
        var hour = DateTime.Now.Hour;
        var timeGreeting = hour switch
        {
            >= 5 and < 12 => "Good morning",
            >= 12 and < 17 => "Good afternoon",
            >= 17 and < 21 => "Good evening",
            _ => "Good evening"  // ไม่ใช้ "Still up" — web ใช้ "Good evening" ตลอดหลัง 5pm
        };
        
        // ใช้ display name จาก auth profile (English name)
        // ถ้าไม่มี display name ใช้ email prefix
        var name = _authService.CurrentUser?.DisplayName 
                   ?? _authService.CurrentUser?.Email?.Split('@')[0] 
                   ?? "there";
        
        return $"{timeGreeting}, {name}";
    }
}
```

**สำคัญ:**
- **ไม่ใช้ "Still up"** — web ใช้แค่ Good morning/afternoon/evening
- **ใช้ English name** จาก user profile (DisplayName field) ไม่ใช่ Thai name
- ถ้า API ไม่มี DisplayName field → ใช้ email prefix ("somchai" จาก somchai@acme.ai.gateway)

---

## Fix 4: Sidebar section grouping — ให้ตรงกับ web

**ปัญหา:** Desktop แสดง "YESTERDAY" section ที่ web ไม่มี — web จัดกลุ่มต่างกัน

**Web grouping (จาก screenshot):**
- PINNED
- PREVIOUS 30 DAYS (รวม today + yesterday + ทุกอย่างใน 30 วัน)
- OLDER

**Desktop grouping (ตอนนี้):**
- PINNED
- TODAY
- YESTERDAY  
- PREVIOUS 30 DAYS
- OLDER

**แก้ `ConversationListViewModel.cs` grouping logic:**

เปลี่ยนให้ตรงกับ web — รวม TODAY + YESTERDAY เข้าไปใน PREVIOUS 30 DAYS:

```csharp
private string GetGroupName(DateTime createdAt)
{
    var now = DateTime.Now;
    var daysAgo = (now.Date - createdAt.Date).Days;
    
    // Web uses only 3 groups:
    // PINNED (handled separately), PREVIOUS 30 DAYS, OLDER
    if (daysAgo <= 30)
        return "PREVIOUS 30 DAYS";
    else
        return "OLDER";
}
```

**หรือถ้าอยากให้ใกล้เคียงกับ web มากที่สุด** ดูจาก web source ว่า grouping logic จริงเป็นยังไง — อาจเป็น:
- Today
- Yesterday  
- Previous 7 days
- Previous 30 days
- Older

ถ้า web จริงๆ มี Today/Yesterday ด้วย (แค่ screenshot ไม่มี conversations ของวันนี้/เมื่อวาน เลยไม่แสดง) → **ให้คงไว้เหมือนเดิม** แต่เปลี่ยนแค่ชื่อ labels ให้ตรง

**ตรวจสอบ:** เปิด web app แล้วสร้าง conversation วันนี้ — ดูว่า web แสดง "TODAY" section มั้ย ถ้ามี → desktop ถูกต้องอยู่แล้ว แค่ screenshot เก่าไม่มี conversations วันนี้

---

## VERIFICATION CHECKLIST

1. [ ] Window ไม่มี native title bar "SRAN Desktop" — ใช้ custom title bar หรือ borderless
2. [ ] Custom title bar: drag ย้ายได้, double-click maximize ได้, min/max/close buttons ยังมี
3. [ ] Windows snap (ลากไปมุมจอ) ยังทำงานได้
4. [ ] Empty state มี composer ตรงกลาง (ไม่ต้องกด New chat ก่อนพิมพ์)
5. [ ] พิมพ์ใน empty state composer แล้วกด send → auto-create conversation + ส่ง message
6. [ ] Greeting ใช้ "Good morning/afternoon/evening" (ไม่มี "Still up")
7. [ ] Greeting ใช้ English name (ไม่ใช่ Thai)
8. [ ] Sidebar grouping ตรงกับ web
9. [ ] "..." overflow menu ที่ user area ทำงานถูกต้อง (กดแล้วเปิด Settings/Logout menu)
10. [ ] Dark + Light theme ยังถูกต้อง
11. [ ] 98 tests ยังผ่าน
12. [ ] System tray / global hotkey ยังทำงานได้หลังเปลี่ยน WindowStyle

---

## BUILD ORDER

1. **Fix 1 (Title bar)** — ทำก่อนเพราะเปลี่ยน Window structure
2. **Fix 3 (Greeting)** — ง่าย แก้ string logic
3. **Fix 4 (Sidebar grouping)** — ตรวจสอบก่อนว่า web จริงๆ group ยังไง
4. **Fix 2 (Empty state composer)** — ใหญ่ที่สุด ทำหลังสุด

**⚠ ระวัง Fix 1:** เปลี่ยน WindowStyle อาจกระทบ:
- System tray minimize behavior
- Global hotkey show/hide
- First-run dialog positioning
- Settings dialog owner

**Test ทุกอย่างหลัง Fix 1** ก่อนไปต่อ

---

*End of Final 4 Fixes Prompt*
