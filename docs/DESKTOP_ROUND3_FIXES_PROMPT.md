# SRAN Desktop — Round 3: Agent Toggle, Conversation Menu, Folders

> ให้ Claude Fable ทำ พร้อม ai-gateway codebase attached
> ข้อมูลทุกอย่างมาจาก web source code จริง (apps/web-chat/src)

---

## Fix 1: ลบ Agent Mode Toggle ออกจาก Header

**เหตุผล:**
Web ไม่มี agent/tool mode toggle เลย — AI ตัดสินใจเองฝั่ง server ว่าจะใช้ tools เมื่อไหร่ (เหมือน Claude) ไม่ต้องให้ user เลือก

Desktop มี Agent Mode ToggleButton อยู่ที่ `MainWindow.xaml` Grid.Column="2" ในแถว header ต้องลบออก

**แก้ไข:**

### 1a. MainWindow.xaml — ลบ Agent toggle button

ลบ ToggleButton ที่ Grid.Column="2" ออกทั้ง block (ปุ่มที่มี `Geo_Agent` icon + `Chat.IsAgentMode` binding + AccentGreenBrush border)

ปรับ Grid.ColumnDefinitions ให้เหลือ 4 columns:
```
Column 0: Auto (sidebar toggle)
Column 1: * (spacer)
Column 2: Auto (Memory pill)
Column 3: Auto ("..." menu)
```

### 1b. เก็บ IsAgentMode property ไว้ แต่ให้เป็น auto

ไม่ต้องลบ `IsAgentMode` property ออกจาก ChatViewModel เพราะ backend ยังใช้อยู่ แต่เปลี่ยนเป็น auto-detect:

```csharp
// ChatViewModel.cs — ลบ manual toggle, ให้ IsAgentMode = true เสมอ
// (server ตัดสินใจเองว่าจะ route ไป agent หรือ chat)
// หรือถ้า dual-API strategy ยังจำเป็น → ตั้ง default เป็น true
// และลบ UI toggle ออก
```

ถ้า dual-API (chat-edge vs gateway) ยังจำเป็นจริงๆ → hardcode `IsAgentMode = true` หรือ detect จาก model type

### 1c. ComposerControl.xaml — ตรวจว่าไม่มี agent toggle เหลือ

Composer ลบ agent toggle ไปแล้วใน Round 2 (เหลือแค่ + attach) — ตรวจยืนยันว่าไม่มีเหลือ

---

## Fix 2: Conversation Context Menu — เพิ่ม "..." Hover Button + ปรับ Menu Items

**ปัญหา:**
Desktop มี right-click ContextMenu อยู่แล้ว (Rename, Pin/Unpin, Delete) แต่:
1. ไม่มี "..." button แสดง on hover (web มี)
2. ไม่มี "Move to folder" menu item
3. Rename ใช้ code-behind click handler ไม่ใช่ command

**Web behavior (ConversationItem.tsx):**
- ปุ่ม "..." (3 dots) แสดงเมื่อ hover conversation row
- คลิกแล้วแสดง dropdown menu:
  1. **Pin / Unpin** — lucide `Pin`/`PinOff` icon, toggle `is_pinned`
  2. **Rename** — pencil icon
  3. **Move to folder →** — folder icon, expandable sub-menu แสดง folder list + "New folder" + "Remove from folder"
  4. **Delete** — trash icon, สีแดง, มี inline confirmation ก่อนลบจริง

**แก้ไข:**

### 2a. SidebarView.xaml — เพิ่ม "..." button on hover

ใน conversation item template เพิ่ม "..." button ด้านขวา แสดง on hover:

```xml
<!-- ใน DataTemplate ของ conversation item -->
<Grid Background="Transparent" x:Name="ConvRow">
    <Grid.ColumnDefinitions>
        <ColumnDefinition Width="Auto" /> <!-- pin icon -->
        <ColumnDefinition Width="*" />    <!-- title -->
        <ColumnDefinition Width="Auto" /> <!-- "..." button -->
    </Grid.ColumnDefinitions>
    
    <!-- Pin icon (existing) -->
    <controls:IconView Grid.Column="0" ... Visibility="{Binding IsPinned}" />
    
    <!-- Title (existing) -->
    <TextBlock Grid.Column="1" Text="{Binding DisplayTitle}" ... />
    
    <!-- "..." overflow button (NEW) — visible on hover -->
    <Button Grid.Column="2" x:Name="OverflowBtn"
            Style="{StaticResource SidebarOverflowButton}"
            Click="ConversationOverflow_Click"
            Opacity="0" Padding="4,2">
        <controls:IconView Geometry="{StaticResource Geo_MoreHorizontal}"
                           Stroke="{DynamicResource TextSecondaryBrush}"
                           Width="16" Height="16" />
    </Button>
</Grid>

<!-- Hover trigger: show "..." button -->
<DataTemplate.Triggers>
    <Trigger SourceName="ConvRow" Property="IsMouseOver" Value="True">
        <Setter TargetName="OverflowBtn" Property="Opacity" Value="1" />
    </Trigger>
</DataTemplate.Triggers>
```

### 2b. Context menu items — 4 items ตาม web

```xml
<Border.ContextMenu>
    <ContextMenu>
        <!-- 1. Pin / Unpin -->
        <MenuItem Command="{Binding DataContext.TogglePinCommand, 
                           RelativeSource={RelativeSource AncestorType=ItemsControl}}"
                  CommandParameter="{Binding}">
            <MenuItem.Header>
                <TextBlock>
                    <TextBlock.Style>
                        <Style TargetType="TextBlock">
                            <Setter Property="Text" Value="Pin" />
                            <Style.Triggers>
                                <DataTrigger Binding="{Binding IsPinned}" Value="True">
                                    <Setter Property="Text" Value="Unpin" />
                                </DataTrigger>
                            </Style.Triggers>
                        </Style>
                    </TextBlock.Style>
                </TextBlock>
            </MenuItem.Header>
            <MenuItem.Icon>
                <controls:IconView Geometry="{StaticResource Geo_Pin}"
                                   Stroke="{DynamicResource TextPrimaryBrush}"
                                   Width="14" Height="14" />
            </MenuItem.Icon>
        </MenuItem>
        
        <!-- 2. Rename -->
        <MenuItem Header="Rename"
                  Command="{Binding DataContext.RenameCommand,
                           RelativeSource={RelativeSource AncestorType=ItemsControl}}"
                  CommandParameter="{Binding}">
            <MenuItem.Icon>
                <controls:IconView Geometry="{StaticResource Geo_Edit}"
                                   Stroke="{DynamicResource TextPrimaryBrush}"
                                   Width="14" Height="14" />
            </MenuItem.Icon>
        </MenuItem>
        
        <!-- 3. Move to folder (sub-menu) -->
        <MenuItem Header="Move to folder"
                  ItemsSource="{Binding DataContext.Folders,
                               RelativeSource={RelativeSource AncestorType=ItemsControl}}">
            <MenuItem.Icon>
                <controls:IconView Geometry="{StaticResource Geo_Folder}"
                                   Stroke="{DynamicResource TextPrimaryBrush}"
                                   Width="14" Height="14" />
            </MenuItem.Icon>
            <!-- Sub-items generated from Folders collection -->
            <MenuItem.ItemContainerStyle>
                <Style TargetType="MenuItem">
                    <Setter Property="Header" Value="{Binding Name}" />
                    <Setter Property="Command" 
                            Value="{Binding DataContext.MoveToFolderCommand,
                                   RelativeSource={RelativeSource AncestorType=ContextMenu}}" />
                    <Setter Property="CommandParameter">
                        <Setter.Value>
                            <MultiBinding Converter="{StaticResource MoveFolderConverter}">
                                <Binding /> <!-- folder -->
                                <Binding Path="PlacementTarget.DataContext" 
                                         RelativeSource="{RelativeSource AncestorType=ContextMenu}" />
                            </MultiBinding>
                        </Setter.Value>
                    </Setter>
                </Style>
            </MenuItem.ItemContainerStyle>
        </MenuItem>
        
        <Separator />
        
        <!-- 4. Delete (red) -->
        <MenuItem Header="Delete"
                  Command="{Binding DataContext.DeleteCommand,
                           RelativeSource={RelativeSource AncestorType=ItemsControl}}"
                  CommandParameter="{Binding}"
                  Foreground="{DynamicResource AccentRedBrush}">
            <MenuItem.Icon>
                <controls:IconView Geometry="{StaticResource Geo_Trash}"
                                   Stroke="{DynamicResource AccentRedBrush}"
                                   Width="14" Height="14" />
            </MenuItem.Icon>
        </MenuItem>
    </ContextMenu>
</Border.ContextMenu>
```

### 2c. "..." button click → open same ContextMenu

```csharp
// SidebarView.xaml.cs
private void ConversationOverflow_Click(object sender, RoutedEventArgs e)
{
    var btn = (Button)sender;
    // Find the parent that has the ContextMenu
    var container = VisualTreeHelper.GetParent(btn);
    while (container != null && !(container is Border border && border.ContextMenu != null))
        container = VisualTreeHelper.GetParent(container);
    
    if (container is FrameworkElement fe && fe.ContextMenu != null)
    {
        fe.ContextMenu.PlacementTarget = btn;
        fe.ContextMenu.Placement = PlacementMode.Bottom;
        fe.ContextMenu.IsOpen = true;
    }
    e.Handled = true;
}
```

### 2d. ConversationListViewModel — เพิ่ม RenameCommand

เปลี่ยน CommitRenameAsync จาก plain method เป็น RelayCommand:

```csharp
[RelayCommand]
private async Task RenameAsync(ConversationItemViewModel conv)
{
    if (conv == null) return;
    
    // Show inline rename UI or simple input dialog
    // Option 1: Toggle an IsRenaming flag on the conversation item
    conv.IsRenaming = true;
    // The view handles focus to the TextBox + Enter/Escape keys
    // On commit: call CommitRenameAsync(conv)
}
```

หรือใช้แบบ InputDialog ง่ายๆ:
```csharp
[RelayCommand]
private async Task RenameAsync(ConversationItemViewModel conv)
{
    if (conv == null) return;
    var newTitle = await ShowInputDialogAsync("Rename conversation", conv.Title);
    if (string.IsNullOrWhiteSpace(newTitle) || newTitle == conv.Title) return;
    
    await _chatEdgeClient.RenameConversationAsync(conv.Id, newTitle);
    conv.Title = newTitle;
    await _conversationStore.UpsertAsync(conv.ToModel());
}
```

---

## Fix 3: Folder System — Full Implementation

**Web API endpoints ที่ desktop ต้อง connect:**

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/folders` | List all folders |
| `POST` | `/folders` | Create folder `{ name }` |
| `PUT` | `/folders/{id}` | Rename folder `{ name }` |
| `DELETE` | `/folders/{id}` | Delete folder (conversations get unlinked) |
| `PUT` | `/conversations/{id}/folder` | Move conversation to folder `{ folder_id }` (null = unfile) |

### 3a. IChatEdgeApiClient — เพิ่ม Folder endpoints

```csharp
// IChatEdgeApiClient.cs
Task<List<FolderDto>> GetFoldersAsync();
Task<FolderDto> CreateFolderAsync(string name);
Task RenameFolderAsync(string folderId, string name);
Task DeleteFolderAsync(string folderId);
Task MoveConversationToFolderAsync(string conversationId, string? folderId);
```

### 3b. FolderDto model

```csharp
// Domain/Models/FolderDto.cs
public class FolderDto
{
    public string Id { get; set; } = "";
    public string Name { get; set; } = "";
    public int Position { get; set; }
    public int ConversationCount { get; set; }
    public DateTime CreatedAt { get; set; }
}
```

### 3c. ConversationListViewModel — Folder commands

```csharp
[ObservableProperty] private ObservableCollection<FolderDto> _folders = new();

[RelayCommand]
private async Task NewFolderAsync()
{
    var folder = await _chatEdgeClient.CreateFolderAsync("New folder");
    Folders.Add(folder);
    // Optionally trigger inline rename
}

[RelayCommand]
private async Task MoveToFolderAsync(/* conv + folder params */)
{
    await _chatEdgeClient.MoveConversationToFolderAsync(conv.Id, folder?.Id);
    conv.FolderId = folder?.Id;
    // Refresh sidebar grouping
    RefreshConversationGroups();
}

[RelayCommand]
private async Task DeleteFolderAsync(FolderDto folder)
{
    await _chatEdgeClient.DeleteFolderAsync(folder.Id);
    Folders.Remove(folder);
    // Unfile conversations that were in this folder
    foreach (var conv in Conversations.Where(c => c.FolderId == folder.Id))
        conv.FolderId = null;
    RefreshConversationGroups();
}

// Load folders on init
private async Task LoadFoldersAsync()
{
    var folders = await _chatEdgeClient.GetFoldersAsync();
    Folders = new ObservableCollection<FolderDto>(folders);
}
```

### 3d. Sidebar — แสดง Folders ก่อน Pinned/Time groups

```xml
<!-- SidebarView.xaml — Folder sections render FIRST (above PINNED) -->
<ItemsControl ItemsSource="{Binding Folders}">
    <ItemsControl.ItemTemplate>
        <DataTemplate DataType="{x:Type models:FolderDto}">
            <Expander IsExpanded="True">
                <Expander.Header>
                    <Grid>
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="Auto" />
                            <ColumnDefinition Width="*" />
                            <ColumnDefinition Width="Auto" />
                            <ColumnDefinition Width="Auto" />
                        </Grid.ColumnDefinitions>
                        <controls:IconView Grid.Column="0" 
                                           Geometry="{StaticResource Geo_Folder}"
                                           Stroke="{DynamicResource TextSecondaryBrush}"
                                           Width="14" Height="14" Margin="0,0,6,0" />
                        <TextBlock Grid.Column="1" Text="{Binding Name}"
                                   FontSize="12" FontWeight="SemiBold"
                                   Foreground="{DynamicResource TextSecondaryBrush}" />
                        <TextBlock Grid.Column="2" 
                                   Text="{Binding ConversationCount, StringFormat=({0})}"
                                   FontSize="11" Margin="4,0"
                                   Foreground="{DynamicResource TextTertiaryBrush}" />
                        <!-- Folder "..." menu -->
                        <Button Grid.Column="3" ...>
                            <!-- Rename, Delete folder -->
                        </Button>
                    </Grid>
                </Expander.Header>
                <!-- Conversations in this folder -->
                <ItemsControl ItemsSource="{Binding DataContext.GetConversationsInFolder(Id),
                              RelativeSource={RelativeSource AncestorType=UserControl}}" />
            </Expander>
        </DataTemplate>
    </ItemsControl.ItemTemplate>
</ItemsControl>
```

### 3e. ConversationItemViewModel — เพิ่ม FolderId

```csharp
[ObservableProperty] private string? _folderId;
```

Conversations ที่มี `FolderId != null` แสดงเฉพาะใน folder section ไม่แสดงใน time-bucket groups

### 3f. "Move to folder" sub-menu ใน conversation context menu

Sub-menu แสดง:
- รายชื่อ folder ทั้งหมด (folder ปัจจุบันมี checkmark + disabled)
- "New folder" option (สร้าง folder ใหม่ + ย้ายเข้าทันที)
- "Remove from folder" (เมื่อ conversation อยู่ใน folder แล้ว)

---

## SUMMARY TABLE

| # | Fix | Files | What Changes |
|---|-----|-------|--------------|
| 1 | ลบ Agent toggle | `MainWindow.xaml` | ลบ ToggleButton Column 2, ปรับ grid เหลือ 4 columns |
| 2a | "..." hover button | `SidebarView.xaml` | เพิ่ม overflow button + hover opacity trigger |
| 2b | Context menu 4 items | `SidebarView.xaml` | Pin/Rename/Move to folder/Delete พร้อม icons |
| 2c | RenameCommand | `ConversationListViewModel.cs` | เปลี่ยนจาก method เป็น RelayCommand |
| 3a | Folder API client | `IChatEdgeApiClient.cs`, `ChatEdgeApiClient.cs` | GET/POST/PUT/DELETE folders + move conversation |
| 3b | Folder model | `FolderDto.cs` | Id, Name, Position, ConversationCount |
| 3c | Folder commands | `ConversationListViewModel.cs` | NewFolder, MoveToFolder, DeleteFolder, LoadFolders |
| 3d | Folder UI | `SidebarView.xaml` | Collapsible folder sections above Pinned |
| 3e | FolderId property | `ConversationItemViewModel.cs` | Filter conversations by folder |

## VERIFICATION CHECKLIST

1. [ ] Header: Agent toggle ไม่มีแล้ว (เหลือแค่ sidebar toggle + Memory + "...")
2. [ ] Conversation hover: "..." button แสดง on hover
3. [ ] "..." click: แสดง menu (Pin, Rename, Move to folder, Delete)
4. [ ] Right-click conversation: แสดง menu เดียวกัน
5. [ ] Pin/Unpin: toggle สำเร็จ, icon เปลี่ยน
6. [ ] Rename: เปลี่ยนชื่อสำเร็จ, title update ทันที
7. [ ] Delete: มี confirmation, ลบสำเร็จ, สีแดง
8. [ ] New folder: สร้างได้, แสดงใน sidebar
9. [ ] Move to folder: sub-menu แสดง folder list, ย้ายสำเร็จ
10. [ ] Folder header: Collapse/expand, Rename, Delete
11. [ ] Conversations ใน folder ไม่แสดงซ้ำใน time groups
12. [ ] Dark + Light theme ถูกต้อง
13. [ ] 98 tests ยังผ่าน

---

*End of Round 3 Fixes Prompt*
