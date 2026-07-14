# SRAN Desktop — Last 2 Fixes

> ให้ Claude Fable ทำ พร้อม ai-gateway codebase attached

---

## Fix 1: New Chat Deduplication — ห้ามสร้าง conversation ว่างซ้ำ

**ปัญหา:** กด "+ New chat" ได้ไม่จำกัด แต่ละครั้งสร้าง conversation ใหม่บน server + local DB ทำให้ sidebar เต็มไปด้วย "New conversation" ว่างๆ ที่ไม่มี message

**Web behavior:** กด New chat → ถ้า conversation ปัจจุบันยังว่าง (ไม่มี message) → ไม่สร้างใหม่ แค่ focus ที่ conversation เดิม

**แก้ `ConversationListViewModel.cs` method `NewChatAsync()`:**

```csharp
[RelayCommand]
private async Task NewChatAsync()
{
    // Guard: ถ้า conversation ที่เลือกอยู่ยังว่าง (ไม่มี message) → ไม่สร้างใหม่
    if (SelectedConversation != null)
    {
        var messages = await _conversationStore.GetMessagesAsync(SelectedConversation.Id);
        if (messages == null || !messages.Any())
        {
            // Conversation ปัจจุบันว่างอยู่แล้ว — แค่ focus input
            return;
        }
    }

    // สร้าง conversation ใหม่ (existing logic)
    try
    {
        var dto = await _chatEdgeClient.CreateConversationAsync();
        // ... rest of existing code
    }
    catch (Exception ex)
    {
        // ... existing error handling
    }
}
```

**Alternative ที่ดีกว่า (ถ้า GetMessages เป็น async ที่ช้า):**

Track message count ใน ConversationViewModel โดยไม่ต้อง query DB:

```csharp
[RelayCommand(CanExecute = nameof(CanCreateNewChat))]
private async Task NewChatAsync() { ... }

private bool CanCreateNewChat()
{
    // ถ้าไม่มี conversation เลือกอยู่ → สร้างได้
    if (SelectedConversation == null) return true;
    
    // ถ้า conversation ปัจจุบันมี messages แล้ว → สร้างได้
    // (ChatViewModel ควรมี property Messages.Count หรือ HasMessages)
    return _chatViewModel.Messages.Count > 0;
}
```

แล้วเรียก `NewChatCommand.NotifyCanExecuteChanged()` ทุกครั้งที่:
- `SelectedConversation` เปลี่ยน
- Message ถูกเพิ่มใน conversation ปัจจุบัน

**เพิ่ม: ลบ conversation ว่างเก่าอัตโนมัติ**

```csharp
// เมื่อสร้าง conversation ใหม่สำเร็จ → ลบ conversation เก่าที่ว่าง
private async Task CleanupEmptyConversationsAsync()
{
    var allConversations = Conversations.ToList();
    foreach (var conv in allConversations)
    {
        if (conv == SelectedConversation) continue; // ข้ามอันที่เลือกอยู่
        
        var messages = await _conversationStore.GetMessagesAsync(conv.Id);
        if (messages == null || !messages.Any())
        {
            // ลบ conversation ว่าง
            try
            {
                await _chatEdgeClient.DeleteConversationAsync(conv.Id);
            }
            catch { /* server อาจลบไปแล้ว */ }
            
            await _conversationStore.DeleteConversationAsync(conv.Id);
            Conversations.Remove(conv);
        }
    }
}
```

เรียก `CleanupEmptyConversationsAsync()` ตอน app startup หรือหลังสร้าง conversation ใหม่

---

## Fix 2: Settings Dialog — Redesign ให้เหมือน Web

**ปัญหา:** Settings dialog เป็น flat StackPanel ไม่มี grouping, ไม่มี scroll, URL fields เปิดให้ user เห็นทั้งหมด

**Web settings (typical pattern):**
- Grouped sections ใน cards/borders
- URL/advanced fields ซ่อนอยู่หลัง "Advanced" toggle
- ScrollViewer สำหรับ content ที่ยาว
- Section headers ชัดเจน

**แก้ `SettingsView.xaml` ทั้งไฟล์:**

```xml
<Window x:Class="SranDesktop.Presentation.Views.SettingsView"
        xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        xmlns:controls="clr-namespace:SranDesktop.Presentation.Controls"
        xmlns:conv="clr-namespace:SranDesktop.Presentation.Converters"
        xmlns:vm="clr-namespace:SranDesktop.Presentation.ViewModels"
        d:DataContext="{d:DesignInstance Type=vm:SettingsViewModel}"
        Title="Settings"
        Width="500" Height="580"
        WindowStartupLocation="CenterOwner"
        ResizeMode="NoResize"
        ShowInTaskbar="False"
        Background="{DynamicResource BgPrimaryBrush}"
        Foreground="{DynamicResource TextPrimaryBrush}">

    <Window.Resources>
        <conv:BoolToVisibilityConverter x:Key="BoolToVisibility" />
        
        <!-- Section card style -->
        <Style x:Key="SectionCard" TargetType="Border">
            <Setter Property="Background" Value="{DynamicResource BgSecondaryBrush}" />
            <Setter Property="CornerRadius" Value="12" />
            <Setter Property="Padding" Value="16,14" />
            <Setter Property="Margin" Value="0,0,0,12" />
        </Style>
        
        <!-- Section header style -->
        <Style x:Key="SectionHeader" TargetType="TextBlock">
            <Setter Property="FontSize" Value="13" />
            <Setter Property="FontWeight" Value="SemiBold" />
            <Setter Property="Margin" Value="0,0,0,12" />
            <Setter Property="Foreground" Value="{DynamicResource TextPrimaryBrush}" />
        </Style>
        
        <!-- Field label style -->
        <Style x:Key="FieldLabel" TargetType="TextBlock">
            <Setter Property="FontSize" Value="12" />
            <Setter Property="Margin" Value="0,0,0,4" />
            <Setter Property="Foreground" Value="{DynamicResource TextSecondaryBrush}" />
        </Style>
    </Window.Resources>

    <Grid>
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto" />   <!-- Header -->
            <RowDefinition Height="*" />       <!-- Content (scrollable) -->
            <RowDefinition Height="Auto" />   <!-- Footer buttons -->
        </Grid.RowDefinitions>

        <!-- Header -->
        <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="24,18,24,12">
            <controls:IconView Geometry="{StaticResource Geo_Settings}"
                               Stroke="{DynamicResource TextPrimaryBrush}"
                               Width="20" Height="20" Margin="0,0,10,0" />
            <TextBlock Text="Settings" FontSize="18" FontWeight="SemiBold"
                       VerticalAlignment="Center" />
        </StackPanel>

        <!-- Scrollable content -->
        <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto"
                      Padding="24,0">
            <StackPanel>

                <!-- === General Section === -->
                <Border Style="{StaticResource SectionCard}">
                    <StackPanel>
                        <TextBlock Text="General" Style="{StaticResource SectionHeader}" />

                        <TextBlock Text="Theme" Style="{StaticResource FieldLabel}" />
                        <ComboBox ItemsSource="{Binding Themes}"
                                  SelectedItem="{Binding SelectedTheme}"
                                  Margin="0,0,0,12" />

                        <TextBlock Text="Default model" Style="{StaticResource FieldLabel}" />
                        <ComboBox ItemsSource="{Binding AvailableModels}"
                                  SelectedValue="{Binding SelectedModelId}"
                                  SelectedValuePath="Id" Margin="0,0,0,4">
                            <ComboBox.ItemTemplate>
                                <DataTemplate>
                                    <TextBlock>
                                        <Run Text="{Binding Name}" />
                                        <Run Text=" (" /><Run Text="{Binding Provider}" /><Run Text=")" 
                                             Foreground="{DynamicResource TextTertiaryBrush}" />
                                    </TextBlock>
                                </DataTemplate>
                            </ComboBox.ItemTemplate>
                        </ComboBox>
                    </StackPanel>
                </Border>

                <!-- === Desktop Integration Section === -->
                <Border Style="{StaticResource SectionCard}">
                    <StackPanel>
                        <TextBlock Text="Desktop Integration" Style="{StaticResource SectionHeader}" />

                        <CheckBox Content="Minimize to system tray on close"
                                  IsChecked="{Binding MinimizeToTray}" Margin="0,0,0,8" />
                        <CheckBox Content="Start with Windows"
                                  IsChecked="{Binding StartWithWindows}" Margin="0,0,0,8" />
                        <CheckBox Content="Enable Agent Mode by default for new chats"
                                  IsChecked="{Binding AgentModeDefault}" Margin="0,0,0,10" />

                        <TextBlock Text="Global hotkey" Style="{StaticResource FieldLabel}" />
                        <TextBlock Text="Ctrl + Shift + Space (show / hide from anywhere)"
                                   FontSize="12" Margin="0,0,0,4"
                                   Foreground="{DynamicResource TextTertiaryBrush}" />
                    </StackPanel>
                </Border>

                <!-- === Advanced Section (collapsed by default) === -->
                <Border Style="{StaticResource SectionCard}">
                    <StackPanel>
                        <ToggleButton x:Name="AdvancedToggle" 
                                      Background="Transparent" BorderThickness="0"
                                      HorizontalContentAlignment="Left" Padding="0">
                            <StackPanel Orientation="Horizontal">
                                <TextBlock Text="Advanced" Style="{StaticResource SectionHeader}"
                                           Margin="0" />
                                <controls:IconView x:Name="AdvChevron"
                                    Geometry="{StaticResource Geo_ChevronDown}"
                                    Stroke="{DynamicResource TextSecondaryBrush}"
                                    Width="12" Height="12" Margin="8,0,0,0" />
                            </StackPanel>
                        </ToggleButton>

                        <StackPanel Visibility="{Binding IsChecked, ElementName=AdvancedToggle, 
                                                  Converter={StaticResource BoolToVisibility}}"
                                    Margin="0,12,0,0">
                            <TextBlock Text="Chat server URL (chat-edge-api)" 
                                       Style="{StaticResource FieldLabel}" />
                            <TextBox Text="{Binding ChatServerUrl}" Margin="0,0,0,10" />

                            <TextBlock Text="Gateway URL (auth + agent mode)" 
                                       Style="{StaticResource FieldLabel}" />
                            <TextBox Text="{Binding GatewayUrl}" Margin="0,0,0,10" />

                            <TextBlock Text="Changes to server URLs require restart."
                                       FontSize="11" FontStyle="Italic"
                                       Foreground="{DynamicResource TextTertiaryBrush}" />
                        </StackPanel>
                    </StackPanel>
                </Border>

            </StackPanel>
        </ScrollViewer>

        <!-- Footer -->
        <StackPanel Grid.Row="2" Orientation="Horizontal" 
                    HorizontalAlignment="Right" Margin="24,12,24,18">
            <Button Content="Cancel" Padding="20,8" Margin="0,0,8,0"
                    Command="{Binding CancelCommand}"
                    Background="Transparent"
                    BorderBrush="{DynamicResource BorderBrush}" />
            <Button Content="Save" Padding="20,8"
                    Command="{Binding SaveCommand}"
                    Background="{DynamicResource AccentBrush}"
                    Foreground="{DynamicResource OnAccentBrush}" />
        </StackPanel>
    </Grid>
</Window>
```

**Key changes:**
- **3 card sections** (General, Desktop Integration, Advanced) แทน flat list
- **Advanced collapsed** by default — server URLs ซ่อนอยู่
- **ScrollViewer** สำหรับ content ยาว
- **Section headers** ชัดเจนใน cards
- **Rounded cards** (CornerRadius 12) + bg surface color

---

## VERIFICATION CHECKLIST

1. [ ] กด New chat ซ้ำๆ → ไม่สร้าง conversation ว่างเพิ่ม (ถ้าอันปัจจุบันว่าง)
2. [ ] Conversation ว่างเก่าๆ ถูก cleanup (หรืออย่างน้อยไม่สร้างเพิ่ม)
3. [ ] กด New chat ตอนมี messages ใน conversation → สร้างใหม่ได้ปกติ
4. [ ] Settings dialog: 3 sections ใน cards (General, Desktop Integration, Advanced)
5. [ ] Advanced section collapsed by default — กดแล้ว expand แสดง URL fields
6. [ ] Settings dialog มี ScrollViewer
7. [ ] Save/Cancel buttons ยังทำงาน
8. [ ] 98 tests ยังผ่าน

---

*End of Last 2 Fixes Prompt*
