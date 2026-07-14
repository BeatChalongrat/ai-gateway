# SRAN Desktop — Web Parity Round 2: Sidebar + Header + Composer

> ให้ Claude Fable ทำ พร้อม ai-gateway codebase attached
> เทียบจาก web (localhost:3002/chat) ค่าทุกอย่างวัดจาก DevTools + source code จริง

---

## Fix 1: Sidebar Top — Search Chats ต้องเป็น Button ไม่ใช่ Input Box

**ปัญหา:**
Desktop แสดง Search เป็น TextBox (input box) ตลอดเวลา
Web แสดง "Search chats" เป็น **clickable button** (icon + text + ⌘K hint) — คลิกแล้วค่อย expand เป็น inline input

**Web layout (Sidebar.tsx):**
```
+ New chat                    ⌘⇧N    ← clickable row, ไม่มี border
📁 New folder                         ← clickable row
🔍 Search chats               ⌘K     ← button (click → expand to input)
```

**Desktop ปัจจุบัน (SidebarView.xaml):**
```
[  + New chat               ]         ← bordered button (full-width)
   📁 New folder                      ← disabled text
[ 🔍 Search chats...  Ctrl+K 🔍 ]    ← always-visible TextBox input
```

**แก้ SidebarView.xaml:**

### 1a. เปลี่ยน New chat จาก bordered button เป็น flat row (ไม่มี border)

```xml
<!-- เปลี่ยนจาก bordered Button เป็น flat row เหมือน web -->
<Button Command="{Binding NewChatCommand}"
        Background="Transparent" BorderThickness="0"
        HorizontalContentAlignment="Stretch"
        Padding="10,8" Cursor="Hand">
    <Grid>
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto" />
            <ColumnDefinition Width="*" />
            <ColumnDefinition Width="Auto" />
        </Grid.ColumnDefinitions>
        <controls:IconView Grid.Column="0" Geometry="{StaticResource Geo_Plus}"
                           Stroke="{DynamicResource TextPrimaryBrush}"
                           Width="16" Height="16" Margin="0,0,10,0" />
        <TextBlock Grid.Column="1" Text="New chat"
                   Foreground="{DynamicResource TextPrimaryBrush}"
                   VerticalAlignment="Center" FontSize="14" />
        <TextBlock Grid.Column="2" Text="Ctrl+N"
                   Foreground="{DynamicResource TextTertiaryBrush}"
                   VerticalAlignment="Center" FontSize="12" />
    </Grid>
</Button>
```

Hover style: background เปลี่ยนเป็น `BgSecondaryBrush` (subtle highlight) + CornerRadius 6

### 1b. New folder ต้อง enable + same flat style

```xml
<Button Background="Transparent" BorderThickness="0"
        HorizontalContentAlignment="Stretch"
        Padding="10,8" Cursor="Hand"
        IsEnabled="True">
    <!-- same 3-column Grid layout เหมือน New chat แต่ไม่มี shortcut hint -->
    <Grid>
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto" />
            <ColumnDefinition Width="*" />
        </Grid.ColumnDefinitions>
        <controls:IconView Grid.Column="0" Geometry="{StaticResource Geo_Folder}"
                           Stroke="{DynamicResource TextSecondaryBrush}"
                           Width="16" Height="16" Margin="0,0,10,0" />
        <TextBlock Grid.Column="1" Text="New folder"
                   Foreground="{DynamicResource TextSecondaryBrush}"
                   VerticalAlignment="Center" FontSize="14" />
    </Grid>
</Button>
```

### 1c. Search chats เปลี่ยนจาก TextBox เป็น toggle button/input

**Default state:** แสดงเป็น button (icon + "Search chats" + "Ctrl+K")
**Clicked / Ctrl+K:** expand เป็น TextBox input (auto-focus) + X ปุ่ม close
**พิมพ์:** filter conversation list ตาม debounce 300ms (ใช้ logic เดิม)
**X หรือ Escape:** กลับเป็น button mode

```xml
<!-- Search: toggle between button mode and input mode -->
<Grid Margin="0,2,0,4">
    <!-- Button mode (default) -->
    <Button x:Name="SearchButton"
            Background="Transparent" BorderThickness="0"
            HorizontalContentAlignment="Stretch"
            Padding="10,8" Cursor="Hand"
            Click="SearchButton_Click"
            Visibility="{Binding IsSearchExpanded, Converter={StaticResource BoolToVisibility}, ConverterParameter=Invert}">
        <Grid>
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto" />
                <ColumnDefinition Width="*" />
                <ColumnDefinition Width="Auto" />
            </Grid.ColumnDefinitions>
            <controls:IconView Grid.Column="0" Geometry="{StaticResource Geo_Search}"
                               Stroke="{DynamicResource TextSecondaryBrush}"
                               Width="16" Height="16" Margin="0,0,10,0" />
            <TextBlock Grid.Column="1" Text="Search chats"
                       Foreground="{DynamicResource TextSecondaryBrush}"
                       VerticalAlignment="Center" FontSize="14" />
            <TextBlock Grid.Column="2" Text="Ctrl+K"
                       Foreground="{DynamicResource TextTertiaryBrush}"
                       VerticalAlignment="Center" FontSize="12" />
        </Grid>
    </Button>
    
    <!-- Input mode (expanded) -->
    <Grid Visibility="{Binding IsSearchExpanded, Converter={StaticResource BoolToVisibility}}">
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto" />
            <ColumnDefinition Width="*" />
            <ColumnDefinition Width="Auto" />
        </Grid.ColumnDefinitions>
        <controls:IconView Grid.Column="0" Geometry="{StaticResource Geo_Search}"
                           Stroke="{DynamicResource TextSecondaryBrush}"
                           Width="16" Height="16" Margin="10,0,6,0" />
        <TextBox Grid.Column="1" x:Name="SearchInput"
                 Text="{Binding SearchText, UpdateSourceTrigger=PropertyChanged}"
                 Background="Transparent" BorderThickness="0"
                 Foreground="{DynamicResource TextPrimaryBrush}"
                 FontSize="14" VerticalAlignment="Center"
                 controls:WatermarkService.Text="Search chats..." />
        <Button Grid.Column="2" Background="Transparent" BorderThickness="0"
                Padding="6" Click="CloseSearch_Click">
            <controls:IconView Geometry="{StaticResource Geo_Close}"
                               Stroke="{DynamicResource TextSecondaryBrush}"
                               Width="12" Height="12" />
        </Button>
    </Grid>
</Grid>
```

**ViewModel เพิ่ม:**
```csharp
[ObservableProperty] private bool _isSearchExpanded;

[RelayCommand]
private void ToggleSearch()
{
    IsSearchExpanded = !IsSearchExpanded;
    if (!IsSearchExpanded) SearchText = "";
}
```

---

## Fix 2: Header Top-Right — เพิ่ม Memory Button + "..." Export Menu

**ปัญหา:**
Desktop header มีแค่ sidebar toggle icon ด้านซ้าย ด้านขวาว่างเปล่า
Web มี "🧠 Memory" button + "..." menu ด้านขวา

**Web (page.tsx):**
- **Memory button:** Brain icon + "Memory" text → click opens popover with:
  - "Turn off/on for this chat" (toggle per-conversation)
  - "Open Memory Settings" (navigate to settings)
- **"..." menu:** click opens dropdown with:
  - Export as Markdown
  - Export as JSON
  - Copy as Markdown (clipboard)
  - Share

**แก้ MainWindow.xaml — header Grid ด้านขวา:**

```xml
<!-- Header bar -->
<Border Grid.Row="0" ... >
    <Grid>
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto" />  <!-- sidebar toggle -->
            <ColumnDefinition Width="*" />     <!-- spacer -->
            <ColumnDefinition Width="Auto" />  <!-- Memory -->
            <ColumnDefinition Width="Auto" />  <!-- More menu -->
        </Grid.ColumnDefinitions>
        
        <!-- Left: existing sidebar toggle -->
        <Button Grid.Column="0" ... />
        
        <!-- Right: Memory button (visible only when conversation is active) -->
        <Button Grid.Column="2" 
                Style="{StaticResource HeaderPillButton}"
                ToolTip="Memory settings"
                Visibility="{Binding HasActiveConversation, Converter={StaticResource BoolToVisibility}}"
                Command="{Binding ToggleMemoryPopupCommand}"
                Margin="0,0,4,0">
            <StackPanel Orientation="Horizontal">
                <controls:IconView Geometry="{StaticResource Geo_Brain}"
                                   Stroke="{DynamicResource TextSecondaryBrush}"
                                   Width="16" Height="16" Margin="0,0,6,0" />
                <TextBlock Text="Memory" FontSize="13"
                           Foreground="{DynamicResource TextSecondaryBrush}" />
            </StackPanel>
        </Button>
        
        <!-- Right: "..." more menu (visible only when conversation is active) -->
        <Button Grid.Column="3"
                Style="{StaticResource HeaderIconButton}"
                ToolTip="More options"
                Visibility="{Binding HasActiveConversation, Converter={StaticResource BoolToVisibility}}"
                Click="MoreMenu_Click"
                Padding="6">
            <controls:IconView Geometry="{StaticResource Geo_MoreHorizontal}"
                               Stroke="{DynamicResource TextSecondaryBrush}"
                               Width="18" Height="18" />
        </Button>
    </Grid>
</Border>
```

### 2a. เพิ่ม Geo_Brain icon geometry

```xml
<!-- Resources.xaml -->
<StreamGeometry x:Key="Geo_Brain">M12 2a7 7 0 0 0-4.6 12.3A7 7 0 0 0 12 22a7 7 0 0 0 4.6-7.7A7 7 0 0 0 12 2z</StreamGeometry>
<!-- ใช้ lucide brain path จริง — ถ้ามี lucide icon set อยู่แล้วใช้อันนั้น -->
```

หรือง่ายกว่า — ใช้ SVG path จาก lucide-react `Brain` icon แปลงเป็น StreamGeometry

### 2b. เพิ่ม Geo_MoreHorizontal icon geometry

```xml
<StreamGeometry x:Key="Geo_MoreHorizontal">M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zm-5 0a1 1 0 1 0 0-2 1 1 0 0 0 0 2zm10 0a1 1 0 1 0 0-2 1 1 0 0 0 0 2z</StreamGeometry>
```

### 2c. "..." Menu — ContextMenu popup

```csharp
// MainWindow.xaml.cs
private void MoreMenu_Click(object sender, RoutedEventArgs e)
{
    var btn = (Button)sender;
    var menu = new ContextMenu();
    
    menu.Items.Add(CreateMenuItem("Export as Markdown", Geo_FileText, ExportMarkdownCommand));
    menu.Items.Add(CreateMenuItem("Export as JSON", Geo_FileCode, ExportJsonCommand));
    menu.Items.Add(new Separator());
    menu.Items.Add(CreateMenuItem("Copy as Markdown", Geo_Copy, CopyMarkdownCommand));
    
    menu.PlacementTarget = btn;
    menu.Placement = PlacementMode.Bottom;
    menu.IsOpen = true;
}
```

### 2d. Memory Popup — simple Popup/Popover

```csharp
// ToggleMemoryPopupCommand → show a Popup with:
// - Toggle "Memory for this chat" (on/off switch)
// - "Open Memory Settings" link → navigate to settings page
// ถ้ายังไม่มี memory API → ทำ UI ก่อน, command เป็น stub ได้
```

### 2e. HeaderPillButton + HeaderIconButton styles

```xml
<!-- Styles.xaml -->
<Style x:Key="HeaderPillButton" TargetType="Button">
    <Setter Property="Background" Value="Transparent" />
    <Setter Property="BorderThickness" Value="1" />
    <Setter Property="BorderBrush" Value="{DynamicResource BorderBrush}" />
    <Setter Property="Padding" Value="10,5" />
    <Setter Property="Cursor" Value="Hand" />
    <Setter Property="Template">
        <Setter.Value>
            <ControlTemplate TargetType="Button">
                <Border Background="{TemplateBinding Background}"
                        BorderBrush="{TemplateBinding BorderBrush}"
                        BorderThickness="{TemplateBinding BorderThickness}"
                        CornerRadius="8" Padding="{TemplateBinding Padding}">
                    <ContentPresenter />
                </Border>
            </ControlTemplate>
        </Setter.Value>
    </Setter>
    <Style.Triggers>
        <Trigger Property="IsMouseOver" Value="True">
            <Setter Property="Background" Value="{DynamicResource BgSecondaryBrush}" />
        </Trigger>
    </Style.Triggers>
</Style>

<Style x:Key="HeaderIconButton" TargetType="Button">
    <Setter Property="Background" Value="Transparent" />
    <Setter Property="BorderThickness" Value="0" />
    <Setter Property="Cursor" Value="Hand" />
    <Setter Property="Template">
        <Setter.Value>
            <ControlTemplate TargetType="Button">
                <Border Background="{TemplateBinding Background}"
                        CornerRadius="6" Padding="{TemplateBinding Padding}">
                    <ContentPresenter />
                </Border>
            </ControlTemplate>
        </Setter.Value>
    </Setter>
    <Style.Triggers>
        <Trigger Property="IsMouseOver" Value="True">
            <Setter Property="Background" Value="{DynamicResource BgSecondaryBrush}" />
        </Trigger>
    </Style.Triggers>
</Style>
```

---

## Fix 3: Composer — Model Selector Interactive + เพิ่ม Knowledge Button

**ปัญหา:**
1. Desktop model selector เป็น pill แสดงชื่อ model (non-interactive, tooltip "Change in Settings")
2. Web model selector เป็น interactive dropdown — คลิกแล้วเปิด popover เลือก model ได้เลย
3. Desktop ไม่มี "Knowledge" button
4. Desktop มี Agent toggle + Attach icon แยก ซึ่ง web ไม่มี (web มีแค่ + ปุ่มเดียวสำหรับ attach)

**Web composer layout (ChatInput.tsx):**
```
[  +  ] [ Message SRAN Chat...                    ] [ Knowledge ] [ Select model ▾ ] [ 🎤 ] [ ↑ ]
 attach    text input                                 KB selector    model dropdown    mic    send
```

**Desktop ปัจจุบัน (ComposerControl.xaml):**
```
[  +  ] [ 🛡️ ] [ 📎 ] [ Message SRAN Chat...      ]              [ ✦ deepseek-r1 ▾ ]        [ ↑ ]
 attach  agent  attach2   text input                                model pill (static)       send
```

### 3a. ลบ Agent toggle + Attach icon ออกจากด้านซ้าย (เหลือแค่ + เดียว)

Desktop มี 3 icons ด้านซ้าย: +, Agent shield, Attach paperclip
Web มีแค่ + เดียว (ใช้สำหรับ attach)

**แก้:** ลบ Agent toggle button และ Attach (paperclip) button ออก เหลือแค่ + ปุ่มเดียว
(Agent mode toggle ย้ายไปอยู่ใน Settings หรือ header แทน — ไม่อยู่ใน composer)

```xml
<!-- ComposerControl.xaml — toolbar left side -->
<!-- เหลือแค่ + attach button อันเดียว -->
<Button Style="{StaticResource ComposerIconButton}"
        Command="{Binding AttachFileCommand}" ToolTip="Attach file">
    <controls:IconView Geometry="{StaticResource Geo_Plus}"
                       Stroke="{DynamicResource TextSecondaryBrush}"
                       Width="18" Height="18" />
</Button>
```

### 3b. Model Selector เปลี่ยนเป็น Interactive Dropdown

แทนที่จะเป็น static pill → เปลี่ยนเป็น Button ที่คลิกแล้วเปิด Popup/ContextMenu แสดง model list

```xml
<!-- ComposerControl.xaml — toolbar right side: model selector -->
<Button x:Name="ModelSelectorButton"
        Style="{StaticResource ComposerPillButton}"
        Click="ModelSelector_Click"
        ToolTip="Select model">
    <StackPanel Orientation="Horizontal">
        <controls:IconView Geometry="{StaticResource Geo_Sparkles}"
                           Stroke="{DynamicResource TextSecondaryBrush}"
                           Width="14" Height="14" Margin="0,0,4,0" />
        <TextBlock Text="{Binding CurrentModelDisplayName}"
                   Foreground="{DynamicResource TextSecondaryBrush}"
                   FontSize="13" VerticalAlignment="Center" />
        <controls:IconView Geometry="{StaticResource Geo_ChevronDown}"
                           Stroke="{DynamicResource TextSecondaryBrush}"
                           Width="12" Height="12" Margin="4,0,0,0" />
    </StackPanel>
</Button>

<!-- Model selector popup -->
<Popup x:Name="ModelSelectorPopup"
       PlacementTarget="{Binding ElementName=ModelSelectorButton}"
       Placement="Top" StaysOpen="False"
       AllowsTransparency="True" PopupAnimation="Fade">
    <Border Background="{DynamicResource BgPrimaryBrush}"
            BorderBrush="{DynamicResource BorderBrush}"
            BorderThickness="1" CornerRadius="12"
            Padding="8" MinWidth="280" MaxHeight="400">
        <Grid>
            <Grid.RowDefinitions>
                <RowDefinition Height="Auto" /> <!-- search -->
                <RowDefinition Height="*" />    <!-- model list -->
            </Grid.RowDefinitions>
            
            <!-- Search box -->
            <TextBox Grid.Row="0" 
                     Text="{Binding ModelSearchText, UpdateSourceTrigger=PropertyChanged}"
                     controls:WatermarkService.Text="Search models..."
                     Margin="4,4,4,8" Padding="8,6" FontSize="13" />
            
            <!-- Model list -->
            <ScrollViewer Grid.Row="1" MaxHeight="320" 
                          VerticalScrollBarVisibility="Auto">
                <ItemsControl ItemsSource="{Binding FilteredModels}">
                    <ItemsControl.ItemTemplate>
                        <DataTemplate>
                            <Button Style="{StaticResource ModelListItem}"
                                    Command="{Binding DataContext.SelectModelCommand, 
                                             RelativeSource={RelativeSource AncestorType=Popup}}"
                                    CommandParameter="{Binding}">
                                <Grid>
                                    <Grid.ColumnDefinitions>
                                        <ColumnDefinition Width="*" />
                                        <ColumnDefinition Width="Auto" />
                                    </Grid.ColumnDefinitions>
                                    <TextBlock Text="{Binding DisplayName}"
                                               FontSize="13" />
                                    <!-- checkmark for selected -->
                                    <controls:IconView Grid.Column="1" 
                                                       Geometry="{StaticResource Geo_Check}"
                                                       Stroke="{DynamicResource AccentBrush}"
                                                       Width="14" Height="14"
                                                       Visibility="{Binding IsSelected, Converter={StaticResource BoolToVisibility}}" />
                                </Grid>
                            </Button>
                        </DataTemplate>
                    </ItemsControl.ItemTemplate>
                </ItemsControl>
            </ScrollViewer>
        </Grid>
    </Border>
</Popup>
```

**ViewModel:**
```csharp
// ChatViewModel.cs — model list จาก API
[ObservableProperty] private string _modelSearchText = "";
[ObservableProperty] private ObservableCollection<ModelInfo> _availableModels = new();

public IEnumerable<ModelInfo> FilteredModels => string.IsNullOrEmpty(ModelSearchText)
    ? AvailableModels
    : AvailableModels.Where(m => m.DisplayName.Contains(ModelSearchText, StringComparison.OrdinalIgnoreCase));

// CurrentModelDisplayName — แสดงชื่อย่อ เช่น "gpt-4o-mini" แทน full path
public string CurrentModelDisplayName => CurrentModelName?.Split('/').Last() ?? "Auto";

[RelayCommand]
private void SelectModel(ModelInfo model)
{
    CurrentModelName = model.Id;
    // close popup
}

// Load models จาก gateway API: GET /api/models
private async Task LoadAvailableModelsAsync()
{
    var models = await _gatewayClient.GetModelsAsync();
    AvailableModels = new ObservableCollection<ModelInfo>(models);
}
```

### 3c. เพิ่ม Knowledge Button (ระหว่าง input กับ model selector)

```xml
<!-- Knowledge selector pill — visible when conversation is active -->
<Button Style="{StaticResource ComposerPillButton}"
        Click="KnowledgeSelector_Click"
        ToolTip="Select knowledge base"
        Visibility="{Binding HasActiveConversation, Converter={StaticResource BoolToVisibility}}">
    <StackPanel Orientation="Horizontal">
        <controls:IconView Geometry="{StaticResource Geo_BookOpen}"
                           Stroke="{DynamicResource TextSecondaryBrush}"
                           Width="14" Height="14" Margin="0,0,4,0" />
        <TextBlock Text="{Binding KnowledgeBaseName, TargetNullValue='Knowledge'}"
                   Foreground="{DynamicResource TextSecondaryBrush}"
                   FontSize="13" VerticalAlignment="Center" />
    </StackPanel>
</Button>
```

Knowledge API: web ใช้ GET/POST `/api/conversations/:id/knowledge` — ตรวจว่า desktop มี endpoint นี้ใน `IChatEdgeClient` หรือยัง ถ้ายัง → เพิ่ม stub
ถ้ายังไม่พร้อม backend → แสดง button แต่ click แล้ว show "Coming soon" tooltip

### 3d. ComposerPillButton style

```xml
<Style x:Key="ComposerPillButton" TargetType="Button">
    <Setter Property="Background" Value="Transparent" />
    <Setter Property="BorderThickness" Value="1" />
    <Setter Property="BorderBrush" Value="{DynamicResource BorderBrush}" />
    <Setter Property="Padding" Value="8,4" />
    <Setter Property="Cursor" Value="Hand" />
    <Setter Property="Margin" Value="2,0" />
    <Setter Property="Template">
        <Setter.Value>
            <ControlTemplate TargetType="Button">
                <Border Background="{TemplateBinding Background}"
                        BorderBrush="{TemplateBinding BorderBrush}"
                        BorderThickness="{TemplateBinding BorderThickness}"
                        CornerRadius="8" Padding="{TemplateBinding Padding}">
                    <ContentPresenter />
                </Border>
            </ControlTemplate>
        </Setter.Value>
    </Setter>
    <Style.Triggers>
        <Trigger Property="IsMouseOver" Value="True">
            <Setter Property="Background" Value="{DynamicResource BgSecondaryBrush}" />
        </Trigger>
    </Style.Triggers>
</Style>
```

---

## SUMMARY TABLE

| # | Fix | Files | What Changes |
|---|-----|-------|--------------|
| 1a | New chat → flat row style | `SidebarView.xaml` | ลบ border, ใช้ transparent button + hover highlight |
| 1b | New folder → same flat style | `SidebarView.xaml` | Enable + same row layout |
| 1c | Search → button/input toggle | `SidebarView.xaml`, `ConversationListViewModel.cs` | เพิ่ม `IsSearchExpanded`, click→expand, Esc→collapse |
| 2a | Memory button | `MainWindow.xaml`, ViewModel | เพิ่ม Brain icon + "Memory" pill ด้านขวา header |
| 2b | "..." export menu | `MainWindow.xaml`, code-behind | เพิ่ม MoreHorizontal icon + ContextMenu (Export MD/JSON, Copy MD) |
| 3a | Composer ลดปุ่มซ้าย | `ComposerControl.xaml` | ลบ Agent toggle + Attach แยก เหลือ + เดียว |
| 3b | Model selector interactive | `ComposerControl.xaml`, ViewModel | เปลี่ยน static pill → dropdown Popup + model list จาก API |
| 3c | Knowledge button | `ComposerControl.xaml` | เพิ่ม Knowledge pill (stub หรือ connect KB API) |

## VERIFICATION CHECKLIST

1. [ ] Sidebar: New chat เป็น flat row (ไม่มี border) + hover highlight
2. [ ] Sidebar: New folder เป็น flat row เหมือนกัน
3. [ ] Sidebar: Search chats เป็น button → click/Ctrl+K expand → Esc collapse
4. [ ] Header right: Memory button มี Brain icon + "Memory" text
5. [ ] Header right: "..." button มี export menu (MD, JSON, Copy)
6. [ ] Header right: ทั้งสองปุ่มแสดงเฉพาะเมื่อ conversation active
7. [ ] Composer left: มีแค่ + ปุ่มเดียว (ไม่มี Agent toggle, ไม่มี Attach แยก)
8. [ ] Composer right: Model selector คลิกได้ → popup แสดง model list
9. [ ] Composer right: Knowledge button อยู่ก่อน Model selector
10. [ ] Dark + Light theme ถูกต้องทั้งคู่
11. [ ] 98 tests ยังผ่าน

---

*End of Web Parity Round 2 Prompt*
