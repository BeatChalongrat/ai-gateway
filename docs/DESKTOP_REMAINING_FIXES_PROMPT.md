# SRAN Desktop — Remaining Parity Fixes (10 Gaps)

> ให้ Claude Fable ทำ พร้อม ai-gateway codebase attached  
> Reference: `docs/DESKTOP_WEB_PARITY_SPEC.md` (ค่า DevTools จริง)  
> เป้าหมาย: ทำให้ desktop เหมือน web **ทุกจุด** — ไม่ใช่แค่ "ใกล้เคียง"

---

## สิ่งที่ถูกแล้ว (อย่าทำเสีย)

- New chat = outlined ghost ✓
- Section labels UPPERCASE (PINNED, TODAY, PREVIOUS 30 DAYS, OLDER) ✓
- Action row visible (copy/regen/thumbs) ✓
- Disclaimer "SRAN Chat can make mistakes..." ✓
- User bubble gray ✓
- No blue accent bar on selected sidebar item ✓
- Model selector "auto" pill ✓
- Assistant message no avatar, no bubble ✓

---

## Fix 1: Composer → Single Rounded Container (สำคัญที่สุด)

**ปัญหา:** Desktop มี model pill แยกบรรทัดหนึ่ง, input อีกบรรทัด, icons กระจาย — ดูแตกต่างจาก web มากที่สุด

**Web (จาก DevTools):** ทุกอย่างอยู่ใน **1 rounded box**:
- Container: `border-radius: 24px`, `border: 0.8px solid rgba(255,255,255,0.12)`, `background: #2f2f2f` (dark), `box-shadow: 0 2px 12px rgba(0,0,0,0.18)`, `max-width: 720px`, centered
- ข้างใน (จากบนลงล่าง):
  - Row 1 (input area): TextBox placeholder "Message SRAN Chat..." กินพื้นที่เต็ม
  - Row 2 (toolbar): ซ้าย→ขวา: `+` button | agent icon | paperclip | ช่องว่าง | model pill "✦ auto ▾" | send circle

**Layout ที่ต้องทำ:**
```
┌─────────────────────────────────────────────────────────┐
│  Message SRAN Chat...                                   │
│                                                         │
│  [+]  [🤖]  [📎]              [✦ auto ▾]    [⬆]       │
└─────────────────────────────────────────────────────────┘
```

**XAML (แทนที่ composer ทั้ง section):**
```xml
<!-- Composer Container — single rounded box like web -->
<Border MaxWidth="720" Margin="20,0,20,8"
        CornerRadius="24" 
        BorderThickness="1"
        BorderBrush="{DynamicResource BorderBrush}"
        Background="{DynamicResource BgSecondaryBrush}"
        Padding="16,12,16,8">
    <Border.Effect>
        <DropShadowEffect ShadowDepth="2" BlurRadius="12" Opacity="0.18" Color="Black" Direction="270" />
    </Border.Effect>
    <Grid>
        <Grid.RowDefinitions>
            <RowDefinition Height="*" />     <!-- input -->
            <RowDefinition Height="Auto" />  <!-- toolbar -->
        </Grid.RowDefinitions>

        <!-- Row 0: Input -->
        <TextBox Grid.Row="0" 
                 Text="{Binding MessageText, UpdateSourceTrigger=PropertyChanged}"
                 AcceptsReturn="True" TextWrapping="Wrap"
                 MaxHeight="200" MinHeight="24"
                 FontSize="15"
                 Background="Transparent" BorderThickness="0"
                 Foreground="{DynamicResource TextPrimaryBrush}"
                 CaretBrush="{DynamicResource TextPrimaryBrush}"
                 controls:WatermarkService.Text="Message SRAN Chat..."
                 VerticalScrollBarVisibility="Auto" />

        <!-- Row 1: Toolbar -->
        <DockPanel Grid.Row="1" Margin="0,6,0,0" LastChildFill="False">
            <!-- Left side buttons -->
            <StackPanel DockPanel.Dock="Left" Orientation="Horizontal">
                <!-- + (plus/attach menu) -->
                <Button Style="{DynamicResource IconButton}" Width="30" Height="30"
                        ToolTip="Attach" Margin="0,0,2,0">
                    <controls:IconView Geometry="{StaticResource Geo_Plus}"
                                       Stroke="{DynamicResource TextSecondaryBrush}"
                                       Width="16" Height="16" />
                </Button>

                <!-- Agent toggle -->
                <ToggleButton IsChecked="{Binding IsAgentMode}" 
                              Style="{DynamicResource IconToggleButton}"
                              Width="30" Height="30" ToolTip="Agent Mode" Margin="0,0,2,0">
                    <controls:IconView Geometry="{StaticResource Geo_Agent}"
                                       Stroke="{DynamicResource TextSecondaryBrush}"
                                       Width="16" Height="16" />
                </ToggleButton>

                <!-- Paperclip (file attach) -->
                <Button Style="{DynamicResource IconButton}" Width="30" Height="30"
                        Command="{Binding AttachCommand}" ToolTip="Attach file">
                    <controls:IconView Geometry="{StaticResource Geo_Attach}"
                                       Stroke="{DynamicResource TextSecondaryBrush}"
                                       Width="16" Height="16" />
                </Button>
            </StackPanel>

            <!-- Right side -->
            <StackPanel DockPanel.Dock="Right" Orientation="Horizontal">
                <!-- Model selector pill -->
                <Button Style="{DynamicResource IconButton}" Padding="8,4"
                        Height="26" Margin="0,0,8,0"
                        Command="{Binding SelectModelCommand}">
                    <StackPanel Orientation="Horizontal">
                        <controls:IconView Geometry="{StaticResource Geo_Sparkles}"
                                           Stroke="{DynamicResource TextSecondaryBrush}"
                                           Width="13" Height="13" Margin="0,0,5,0" />
                        <TextBlock Text="{Binding CurrentModelName}" FontSize="12"
                                   Foreground="{DynamicResource TextSecondaryBrush}"
                                   VerticalAlignment="Center" />
                        <controls:IconView Geometry="{StaticResource Geo_ChevronDown}"
                                           Stroke="{DynamicResource TextSecondaryBrush}"
                                           Width="10" Height="10" Margin="4,0,0,0" />
                    </StackPanel>
                </Button>

                <!-- Send / Stop circle -->
                <Button Width="32" Height="32" Command="{Binding SendCommand}"
                        ToolTip="Send message">
                    <Button.Style>
                        <Style TargetType="Button" BasedOn="{StaticResource {x:Type Button}}">
                            <Setter Property="Template">
                                <Setter.Value>
                                    <ControlTemplate TargetType="Button">
                                        <Border x:Name="Bg" Width="32" Height="32"
                                                CornerRadius="16"
                                                Background="{DynamicResource BgSelectedBrush}">
                                            <controls:IconView x:Name="Ico"
                                                Geometry="{StaticResource Geo_Send}"
                                                Stroke="{DynamicResource TextTertiaryBrush}"
                                                Width="16" Height="16"
                                                HorizontalAlignment="Center" VerticalAlignment="Center" />
                                        </Border>
                                        <ControlTemplate.Triggers>
                                            <!-- Active (has text) -->
                                            <DataTrigger Binding="{Binding HasMessageText}" Value="True">
                                                <Setter TargetName="Bg" Property="Background"
                                                        Value="{DynamicResource AccentBrush}" />
                                                <Setter TargetName="Ico" Property="Stroke"
                                                        Value="{DynamicResource OnAccentBrush}" />
                                            </DataTrigger>
                                            <!-- Streaming (show stop) -->
                                            <DataTrigger Binding="{Binding IsStreaming}" Value="True">
                                                <Setter TargetName="Bg" Property="Background"
                                                        Value="{DynamicResource AccentBrush}" />
                                                <Setter TargetName="Ico" Property="Geometry"
                                                        Value="{StaticResource Geo_Stop}" />
                                                <Setter TargetName="Ico" Property="Stroke" Value="Transparent" />
                                                <Setter TargetName="Ico" Property="Fill"
                                                        Value="{DynamicResource OnAccentBrush}" />
                                            </DataTrigger>
                                        </ControlTemplate.Triggers>
                                    </ControlTemplate>
                                </Setter.Value>
                            </Setter>
                        </Style>
                    </Button.Style>
                </Button>
            </StackPanel>
        </DockPanel>
    </Grid>
</Border>
```

**สำคัญ:** ลบ composer elements เก่าทั้งหมด (model pill แยก, input border แยก, button row แยก) แล้วแทนด้วย single Border container ข้างบน

---

## Fix 2: Bottom User Area → Avatar + Email + Overflow Menu

**ปัญหา:** Desktop แสดง `email + ⚙ gear + "Log out" text` เรียงแถว  
**Web:** `avatar circle "S" + email + "..." kebab menu`

**แก้ sidebar footer ใน `SidebarView.xaml`:**

```xml
<!-- แทนที่ bottom user area ทั้ง section -->
<DockPanel Margin="12,8" LastChildFill="True">
    <!-- Avatar circle -->
    <Border DockPanel.Dock="Left" Width="28" Height="28" CornerRadius="14"
            Background="{DynamicResource BgSecondaryBrush}"
            Margin="0,0,8,0">
        <TextBlock Text="S" FontSize="12" FontWeight="SemiBold"
                   HorizontalAlignment="Center" VerticalAlignment="Center"
                   Foreground="{DynamicResource TextPrimaryBrush}" />
    </Border>

    <!-- Overflow menu "..." -->
    <Button DockPanel.Dock="Right" Style="{DynamicResource IconButton}"
            Width="28" Height="28" Margin="4,0,0,0"
            Click="UserMenuButton_Click">
        <controls:IconView Geometry="{StaticResource Geo_More}"
                           Stroke="{DynamicResource TextSecondaryBrush}"
                           Width="14" Height="14" />
        <Button.ContextMenu>
            <ContextMenu>
                <MenuItem Header="Settings" Command="{Binding OpenSettingsCommand}">
                    <MenuItem.Icon>
                        <controls:IconView Geometry="{StaticResource Geo_Settings}"
                                           Stroke="{DynamicResource TextSecondaryBrush}"
                                           Width="14" Height="14" />
                    </MenuItem.Icon>
                </MenuItem>
                <Separator />
                <MenuItem Header="Log out" Command="{Binding LogoutCommand}">
                    <MenuItem.Icon>
                        <controls:IconView Geometry="{StaticResource Geo_Logout}"
                                           Stroke="{DynamicResource AccentRedBrush}"
                                           Width="14" Height="14" />
                    </MenuItem.Icon>
                </MenuItem>
            </ContextMenu>
        </Button.ContextMenu>
    </Button>

    <!-- Email (truncated) -->
    <TextBlock Text="{Binding UserEmail}" FontSize="12"
               VerticalAlignment="Center"
               TextTrimming="CharacterEllipsis"
               Foreground="{DynamicResource TextSecondaryBrush}" />
</DockPanel>
```

**ลบ:** gear icon button เก่า, "Log out" text button เก่า

**Code-behind สำหรับ "..." click → open context menu:**
```csharp
private void UserMenuButton_Click(object sender, RoutedEventArgs e)
{
    if (sender is Button btn && btn.ContextMenu != null)
    {
        btn.ContextMenu.PlacementTarget = btn;
        btn.ContextMenu.IsOpen = true;
    }
}
```

---

## Fix 3: New Chat Button — Add Shortcut Hint

**ปัญหา:** แค่ "+ New chat" ไม่มี "Ctrl+N"

**แก้:** เพิ่ม shortcut text ทางขวาใน New chat button:

หา New chat button ใน `SidebarView.xaml` แล้วแก้ content ให้เป็น:
```xml
<Grid>
    <StackPanel Orientation="Horizontal" HorizontalAlignment="Left">
        <controls:IconView Geometry="{StaticResource Geo_Plus}"
                           Stroke="{DynamicResource TextPrimaryBrush}"
                           Width="14" Height="14" Margin="0,0,7,0" />
        <TextBlock Text="New chat" VerticalAlignment="Center" FontSize="13" />
    </StackPanel>
    <TextBlock Text="Ctrl+N" FontSize="10" Opacity="0.4"
               HorizontalAlignment="Right" VerticalAlignment="Center"
               Foreground="{DynamicResource TextSecondaryBrush}" />
</Grid>
```

---

## Fix 4: New Folder Button

**ปัญหา:** ไม่มี  
**Web:** มีปุ่ม "New folder" ใต้ New chat

**เพิ่มใน `SidebarView.xaml` ใต้ New chat button:**
```xml
<Button Margin="12,0,12,6" Padding="10,7"
        Background="Transparent" BorderThickness="0"
        Foreground="{DynamicResource TextSecondaryBrush}"
        ToolTip="Create folder">
    <StackPanel Orientation="Horizontal">
        <controls:IconView Geometry="{StaticResource Geo_Folder}"
                           Stroke="{DynamicResource TextSecondaryBrush}"
                           Width="14" Height="14" Margin="0,0,7,0" />
        <TextBlock Text="New folder" FontSize="13" VerticalAlignment="Center" />
    </StackPanel>
</Button>
```

**Note:** ถ้า backend ยังไม่ support folders ให้ปุ่มนี้ disabled หรือแสดง "Coming soon" tooltip — แต่ต้อง**มีอยู่** เพื่อให้ UI ตรงกับ web

---

## Fix 5: Sidebar Header — Add Collapse Chevron

**ปัญหา:** "SRAN Chat" wordmark ไม่มี collapse chevron ข้างขวา  
**Web:** "SRANChat" + `<` chevron ข้างขวา กดแล้ว sidebar collapse

**แก้ sidebar header ใน `SidebarView.xaml`:**
```xml
<DockPanel Margin="12,12,8,8">
    <!-- Collapse chevron → right -->
    <Button DockPanel.Dock="Right" Style="{DynamicResource IconButton}"
            Width="28" Height="28"
            Command="{Binding ToggleSidebarCommand}"
            ToolTip="Collapse sidebar">
        <controls:IconView Geometry="{StaticResource Geo_ChevronLeft}"
                           Stroke="{DynamicResource TextSecondaryBrush}"
                           Width="14" Height="14" />
    </Button>
    <!-- Wordmark -->
    <TextBlock VerticalAlignment="Center">
        <Run Text="SRAN" FontWeight="Bold" FontSize="16"
             Foreground="{DynamicResource TextPrimaryBrush}" />
        <Run Text="Chat" FontWeight="Normal" FontSize="16"
             Foreground="{DynamicResource TextPrimaryBrush}" />
    </TextBlock>
</DockPanel>
```

**เพิ่ม Geometry ใน `Icons.xaml`:**
```xml
<Geometry x:Key="Geo_ChevronLeft">M 15 18 L 9 12 L 15 6</Geometry>
```

---

## Fix 6: Search — Add Filter Icon + Shortcut Hint

**ปัญหา:** Search box มีแค่ search icon + placeholder  
**Web:** search icon + placeholder + filter icon (⊞) ขวา + ⌘K shortcut visible

**แก้ search section ใน `SidebarView.xaml`:**
```xml
<Grid Margin="12,0,12,8">
    <!-- Search icon (left) -->
    <controls:IconView Geometry="{StaticResource Geo_Search}"
                       Stroke="{DynamicResource TextSecondaryBrush}"
                       Width="14" Height="14"
                       HorizontalAlignment="Left" VerticalAlignment="Center"
                       Margin="10,0,0,0" IsHitTestVisible="False"
                       Panel.ZIndex="1" />
    <!-- Search input -->
    <TextBox Text="{Binding SearchText, UpdateSourceTrigger=PropertyChanged}"
             Padding="30,6,60,6"
             FontSize="13"
             controls:WatermarkService.Text="Search chats..." />
    <!-- Right side: filter icon + shortcut hint -->
    <StackPanel Orientation="Horizontal" HorizontalAlignment="Right"
                VerticalAlignment="Center" Margin="0,0,8,0"
                IsHitTestVisible="False">
        <TextBlock Text="Ctrl+K" FontSize="9" Opacity="0.4"
                   Foreground="{DynamicResource TextSecondaryBrush}"
                   VerticalAlignment="Center" Margin="0,0,6,0" />
        <Button Style="{DynamicResource IconButton}" Width="22" Height="22"
                IsHitTestVisible="True">
            <controls:IconView Geometry="{StaticResource Geo_Settings}"
                               Stroke="{DynamicResource TextTertiaryBrush}"
                               Width="12" Height="12" />
        </Button>
    </StackPanel>
</Grid>
```

---

## Fix 7: Bullet List — Proper Bullets (Not Asterisks)

**ปัญหา:** Markdown renderer แสดง list items ด้วย "* " (asterisk) แทน bullet dot

**แก้ `MarkdownRenderer.cs`:**

หา method ที่ render `ListBlock` / `ListItemBlock` — เปลี่ยน bullet character:

```csharp
// แทนที่ "*" หรือ "-" prefix ด้วย proper bullet
// Option A: ใช้ WPF List control
var list = new System.Windows.Documents.List();
list.MarkerStyle = listBlock.IsOrdered 
    ? TextMarkerStyle.Decimal 
    : TextMarkerStyle.Disc;  // proper round bullet
list.Padding = new Thickness(20, 0, 0, 0);
list.FontSize = 16;

foreach (var item in listBlock)
{
    var listItem = new System.Windows.Documents.ListItem();
    // ... add paragraphs
    list.ListItems.Add(listItem);
}

// Option B: ถ้าใช้ Inline approach (Run/TextBlock)
// เปลี่ยนจาก "* " เป็น "•  " (U+2022 + double space)
var bulletPrefix = listBlock.IsOrdered 
    ? $"{index}. " 
    : "•  ";
```

**สำคัญ:** ถ้าตอนนี้ใช้ `TextBlock` แทน `FlowDocument List` ให้เปลี่ยน prefix จาก `"* "` เป็น `"•  "` (bullet dot U+2022) — เป็นวิธีที่ง่ายที่สุด

**ถ้าใช้ FlowDocument:** ใช้ `System.Windows.Documents.List` กับ `TextMarkerStyle.Disc` — จะได้ bullet ที่ถูกต้องและ indentation ที่เหมาะสม

---

## Fix 8: Table Header — Distinct Background

**ปัญหา:** Table header row มี bg สีเหมือน body rows  
**Web:** header row มี bg เข้มกว่า + text bold

**แก้ `MarkdownRenderer.cs` table rendering:**

```csharp
// เมื่อ render table header row:
headerRow.Background = new DynamicResourceExtension("BgSecondaryBrush")... 
// หรือ
headerCell.SetResourceReference(TextElement.BackgroundProperty, "BgSecondaryBrush");
headerCell.FontWeight = FontWeights.SemiBold;

// Body rows: transparent background (or alternate with very subtle bg)
```

**ถ้าใช้ WPF `Grid` สำหรับ table:**
```csharp
// Header cells
var headerBorder = new Border
{
    Padding = new Thickness(12, 8),
    BorderThickness = new Thickness(0, 0, 0, 1),
};
headerBorder.SetResourceReference(Border.BackgroundProperty, "BgSecondaryBrush");
headerBorder.SetResourceReference(Border.BorderBrushProperty, "BorderBrush");

var headerText = new TextBlock
{
    Text = cell.ToString(),
    FontWeight = FontWeights.SemiBold,
    FontSize = 14,
};
headerText.SetResourceReference(TextBlock.ForegroundProperty, "TextPrimaryBrush");
```

---

## Fix 9: Remove Centered Conversation Title from Header

**ปัญหา:** Desktop แสดง conversation title ตรงกลางด้านบนของ chat area  
**Web:** ไม่มี title ตรงนั้นเลย

**แก้ `ChatView.xaml` หรือ `MainWindow.xaml`:**

หา `TextBlock` ที่ bind to conversation title (น่าจะ `{Binding CurrentConversation.Title}` หรือคล้ายกัน) ที่อยู่ด้านบนของ chat area → **ลบออก** หรือ set `Visibility="Collapsed"`

```xml
<!-- ลบหรือซ่อน: -->
<!-- <TextBlock Text="{Binding ConversationTitle}" HorizontalAlignment="Center" ... /> -->
```

**(Optional เพิ่ม)** แทนที่ด้วย kebab menu ขวาบน:
```xml
<Button HorizontalAlignment="Right" Margin="0,8,16,0"
        Style="{DynamicResource IconButton}" Width="30" Height="30">
    <controls:IconView Geometry="{StaticResource Geo_More}"
                       Stroke="{DynamicResource TextSecondaryBrush}"
                       Width="16" Height="16" />
    <Button.ContextMenu>
        <ContextMenu>
            <MenuItem Header="Export as Markdown" />
            <MenuItem Header="Export as JSON" />
            <Separator />
            <MenuItem Header="Rename" />
            <MenuItem Header="Delete" Foreground="{DynamicResource AccentRedBrush}" />
        </ContextMenu>
    </Button.ContextMenu>
</Button>
```

---

## Fix 10: Send Button States

**ปัญหา:** Send button disabled state ดูไม่ชัดเจน  
**Web (จาก DevTools):**
- **Disabled** (no text): bg = `#3a3a3a` (surface-hover), icon = `TextTertiary` (#6b6b6b)
- **Active** (has text): bg = `TextPrimary` (#ececec dark / #0d0d0d light), icon = `BgPrimary` (#212121 dark / #ffffff light)
- **Streaming**: bg = `TextPrimary`, icon = filled stop square

**ถ้า Fix 1 ข้างบนทำแล้ว** send button ควรถูกต้องแล้วจาก ControlTemplate ที่ให้ไป — verify ว่า:
- `HasMessageText = false`: วงกลม bg `BgSelectedBrush`, icon จาง
- `HasMessageText = true`: วงกลม bg `AccentBrush` (= TextPrimary), icon `OnAccentBrush` (= BgPrimary)
- `IsStreaming = true`: วงกลม bg `AccentBrush`, filled stop square icon

---

## VERIFICATION CHECKLIST

หลัง build เสร็จ ให้ verify ทุกข้อ:

1. [ ] Composer เป็น single rounded box (radius 24, border, shadow) — ไม่มี element แยกนอก box
2. [ ] Composer toolbar: [+] [agent] [clip] ............ [✦ auto ▾] [send] อยู่ใน box เดียวกัน
3. [ ] Bottom user area: avatar "S" circle + email + "..." menu — ไม่มี gear icon แยก, ไม่มี "Log out" text
4. [ ] New chat มี "Ctrl+N" hint ขวา
5. [ ] "New folder" button อยู่ใต้ New chat
6. [ ] "SRAN Chat" wordmark มี `<` chevron ข้างขวา กดแล้ว collapse
7. [ ] Search มี filter icon + "Ctrl+K" hint
8. [ ] Bullet list ใช้ • (bullet dot) ไม่ใช่ * (asterisk)
9. [ ] Table header row มี bg สีเข้มกว่า body + text bold
10. [ ] ไม่มี centered conversation title ด้านบน chat area
11. [ ] Send button: disabled = dark circle + จาง icon, active = bright circle + contrast icon
12. [ ] Dark theme ถูกต้อง
13. [ ] Light theme ถูกต้อง
14. [ ] 98 tests ยังผ่าน

## BUILD ORDER

ทำตามลำดับนี้:
1. Fix 1 (Composer container) — ใหญ่ที่สุด ทำก่อน
2. Fix 2 (User area)
3. Fix 3-6 (Sidebar: shortcut, folder, chevron, search)
4. Fix 7-8 (Markdown: bullets, table header)
5. Fix 9-10 (Header, send button)
6. Verify ทุกข้อ

---

*End of Remaining Fixes Prompt*
