# SRAN Desktop — UI Alignment Prompt: Match Web-Chat Design

> Give this to Claude Fable with the ai-gateway codebase attached.
> Goal: Make SRAN Desktop look and feel like the SRAN web-chat app.  
> The desktop currently uses 100% emoji/unicode for icons — no icon library, no SVGs.  
> The web uses Lucide icons + custom SVGs with a carefully designed color system.

---

## THE PROBLEM

The desktop app looks "homemade" compared to the web app. Specific gaps:

1. **All icons are emoji** (📎 ⚙ ☰ 🤖 📌 etc.) — web uses crisp vector SVG icons
2. **Color palette is different** — desktop uses Tailwind Slate palette, web uses a neutral gray palette (#212121-based dark, #FFFFFF-based light)
3. **User bubble style** — desktop uses right-aligned blue pill, web uses right-aligned gray pill (20px border-radius)
4. **Assistant messages** — desktop has blue "S" avatar + gray bubble, web has no avatar and no bubble (just prose)
5. **Empty state** — desktop has plain "Select a chat" text, web has logo + greeting + suggestion chips
6. **Model selector** — desktop has no model selector in chat view, web has it in the composer
7. **Missing features** — no edit message, no regenerate, no feedback (thumbs up/down), no export, no branch navigation
8. **Sidebar branding** — desktop has none, web has "SRAN Chat" wordmark
9. **Keyboard shortcut hints** — not displayed anywhere
10. **Font** — desktop uses Segoe UI, web uses Outfit (brand) + JetBrains Mono (code)

---

## WHAT TO DO

### A. Icon System — Replace ALL emoji with SVG PathGeometry

Create a new resource dictionary with all icons as `DrawingImage` resources using SVG-equivalent `PathGeometry`. This is the WPF-native way to have crisp, scalable, theme-aware vector icons.

**File:** `src/SranDesktop.Presentation/Resources/Icons.xaml`

Define every icon the app needs. Below are the exact path data for each icon (derived from Lucide icon set, 24x24 viewbox, stroke-width 2, stroke-linecap round, stroke-linejoin round):

```xml
<ResourceDictionary xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
                    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">

    <!-- 
         ICON CONVENTION:
         - All icons are DrawingImage with GeometryDrawing
         - Stroke color uses {DynamicResource TextPrimaryBrush} or TextSecondaryBrush
         - Size: design at 24x24 viewbox, display at 16-20px via Image width/height
         - Name: Icon_{PascalCaseName}
    -->

    <!-- ═══ SIDEBAR ═══ -->

    <!-- Plus (new chat) — circle with + -->
    <DrawingImage x:Key="Icon_Plus">
        <DrawingImage.Drawing>
            <GeometryDrawing Brush="{DynamicResource TextPrimaryBrush}"
                             Geometry="M 12 5 L 12 19 M 5 12 L 19 12" />
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Search (magnifying glass) -->
    <DrawingImage x:Key="Icon_Search">
        <DrawingImage.Drawing>
            <DrawingGroup>
                <GeometryDrawing Brush="Transparent" Pen="{x:Null}">
                    <GeometryDrawing.Geometry>
                        <EllipseGeometry Center="11,11" RadiusX="8" RadiusY="8" />
                    </GeometryDrawing.Geometry>
                </GeometryDrawing>
                <GeometryDrawing>
                    <GeometryDrawing.Pen>
                        <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                             StartLineCap="Round" EndLineCap="Round" />
                    </GeometryDrawing.Pen>
                    <GeometryDrawing.Geometry>
                        <GeometryGroup>
                            <EllipseGeometry Center="11,11" RadiusX="7" RadiusY="7" />
                            <LineGeometry StartPoint="16.5,16.5" EndPoint="21,21" />
                        </GeometryGroup>
                    </GeometryDrawing.Geometry>
                </GeometryDrawing>
            </DrawingGroup>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Pin -->
    <DrawingImage x:Key="Icon_Pin">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 12 17 L 12 22 M 5 17 L 19 17 M 15 3.13 L 9 3.13 C 8.45 3.13 8 3.58 8 4.13 L 8 10 C 6.34 10 5 12.69 5 14 L 5 17 L 19 17 L 19 14 C 19 12.69 17.66 10 16 10 L 16 4.13 C 16 3.58 15.55 3.13 15 3.13 Z" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Settings (gear) -->
    <DrawingImage x:Key="Icon_Settings">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <GeometryGroup>
                        <EllipseGeometry Center="12,12" RadiusX="3" RadiusY="3" />
                        <PathGeometry Figures="M 12.22 2 L 11.78 2 C 11.23 2 10.78 2.45 10.78 3 L 10.78 3.18 C 10.78 3.59 10.53 3.96 10.14 4.12 C 10.04 4.16 9.94 4.21 9.84 4.26 C 9.47 4.44 9.02 4.39 8.72 4.11 L 8.59 3.98 C 8.2 3.59 7.57 3.59 7.18 3.98 L 6.87 4.29 C 6.48 4.68 6.48 5.31 6.87 5.7 L 7 5.83 C 7.28 6.13 7.33 6.58 7.15 6.95 C 7.1 7.05 7.05 7.15 7.01 7.25 C 6.85 7.64 6.48 7.89 6.07 7.89 L 5.89 7.89 C 5.34 7.89 4.89 8.34 4.89 8.89 L 4.89 9.11 C 4.89 9.66 5.34 10.11 5.89 10.11 L 6.07 10.11" />
                    </GeometryGroup>
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Sidebar toggle (PanelLeft) -->
    <DrawingImage x:Key="Icon_SidebarToggle">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <GeometryGroup>
                        <RectangleGeometry Rect="3,3,18,18" RadiusX="2" RadiusY="2" />
                        <LineGeometry StartPoint="9,3" EndPoint="9,21" />
                    </GeometryGroup>
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Logout (arrow out of door) -->
    <DrawingImage x:Key="Icon_Logout">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 9 21 L 5 21 C 4.47 21 3.96 20.79 3.59 20.41 C 3.21 20.04 3 19.53 3 19 L 3 5 C 3 4.47 3.21 3.96 3.59 3.59 C 3.96 3.21 4.47 3 5 3 L 9 3 M 16 17 L 21 12 L 16 7 M 21 12 L 9 12" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- ═══ CHAT ACTIONS ═══ -->

    <!-- Copy (overlapping rectangles) -->
    <DrawingImage x:Key="Icon_Copy">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 20 9 L 20 4 C 20 3.45 19.55 3 19 3 L 9 3 C 8.45 3 8 3.45 8 4 L 8 14 C 8 14.55 8.45 15 9 15 L 14 15 M 16 9 L 20 9 L 20 20 C 20 20.55 19.55 21 19 21 L 9 21 C 8.45 21 8 20.55 8 20 L 8 16" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Check (after copy) -->
    <DrawingImage x:Key="Icon_Check">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource AccentGreenBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 20 6 L 9 17 L 4 12" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Edit (pencil) -->
    <DrawingImage x:Key="Icon_Edit">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 17 3 C 17.53 2.47 18.24 2.18 18.97 2.18 C 19.71 2.18 20.42 2.47 20.95 3 C 21.47 3.53 21.77 4.24 21.77 4.97 C 21.77 5.71 21.47 6.42 20.95 6.95 L 7.5 20.4 L 2 22 L 3.6 16.5 L 17 3 Z" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Regenerate (circular arrows / RefreshCw) -->
    <DrawingImage x:Key="Icon_Regenerate">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 21 2 L 21 8 L 15 8 M 3 22 L 3 16 L 9 16 M 3.51 9 C 3.85 7.86 4.43 6.81 5.21 5.93 C 5.99 5.05 6.94 4.37 8.01 3.92 C 9.08 3.47 10.24 3.27 11.39 3.34 C 12.55 3.41 13.68 3.74 14.68 4.31 L 21 8 M 20.49 15 C 20.15 16.14 19.57 17.19 18.79 18.07 C 18.01 18.95 17.06 19.63 15.99 20.08 C 14.92 20.53 13.76 20.73 12.61 20.66 C 11.45 20.59 10.32 20.26 9.32 19.69 L 3 16" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Thumbs Up -->
    <DrawingImage x:Key="Icon_ThumbUp">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 7 10 L 7 22 M 14 22 L 5.97 22 C 5.14 22 4.39 21.52 4.04 20.78 L 1.68 16.07 C 1.24 15.19 1.85 14.16 2.83 14.13 L 7 14 L 7 5.5 C 7 4.39 7.9 3.5 9 3.5 L 9.09 3.5 C 9.59 3.5 10 3.91 10 4.41 L 10 7 L 18.12 7 C 18.93 7 19.58 7.65 19.58 8.46 L 18.84 18.77 C 18.71 20.06 17.64 21.06 16.34 21.06 L 14 22 Z" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Thumbs Down -->
    <DrawingImage x:Key="Icon_ThumbDown">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 17 14 L 17 2 M 10 2 L 18.03 2 C 18.86 2 19.61 2.48 19.96 3.22 L 22.32 7.93 C 22.76 8.81 22.15 9.84 21.17 9.87 L 17 10 L 17 18.5 C 17 19.61 16.1 20.5 15 20.5 L 14.91 20.5 C 14.41 20.5 14 20.09 14 19.59 L 14 17 L 5.88 17 C 5.07 17 4.42 16.35 4.42 15.54 L 5.16 5.23 C 5.29 3.94 6.36 2.94 7.66 2.94 L 10 2 Z" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- ═══ COMPOSER ═══ -->

    <!-- Paperclip (attach) -->
    <DrawingImage x:Key="Icon_Attach">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 21.44 11.05 L 12.25 20.24 C 11.12 21.37 9.58 22 7.97 22 C 6.37 22 4.82 21.37 3.69 20.24 C 2.56 19.11 1.93 17.57 1.93 15.96 C 1.93 14.36 2.56 12.81 3.69 11.68 L 12.88 2.49 C 13.63 1.74 14.64 1.32 15.69 1.32 C 16.75 1.32 17.76 1.74 18.51 2.49 C 19.26 3.24 19.68 4.25 19.68 5.31 C 19.68 6.36 19.26 7.37 18.51 8.12 L 9.31 17.31 C 8.94 17.69 8.43 17.9 7.9 17.9 C 7.38 17.9 6.87 17.69 6.5 17.31 C 6.12 16.94 5.91 16.43 5.91 15.9 C 5.91 15.38 6.12 14.87 6.5 14.5 L 15.07 5.93" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Send (arrow up) -->
    <DrawingImage x:Key="Icon_Send">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="White" Thickness="2.5"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 12 19 L 12 5 M 5 12 L 12 5 L 19 12" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Stop (filled square) -->
    <DrawingImage x:Key="Icon_Stop">
        <DrawingImage.Drawing>
            <GeometryDrawing Brush="White">
                <GeometryDrawing.Geometry>
                    <RectangleGeometry Rect="6,6,12,12" RadiusX="2" RadiusY="2" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Bot/Agent (BrainCircuit) -->
    <DrawingImage x:Key="Icon_Agent">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource AccentBlueBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 12 5 C 8.13 5 5 8.13 5 12 C 5 15.87 8.13 19 12 19 C 15.87 19 19 15.87 19 12 C 19 8.13 15.87 5 12 5 Z M 12 8 L 12 12 M 12 12 L 15 15 M 9 3 L 15 3 M 12 3 L 12 5" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Sparkles (model selector) -->
    <DrawingImage x:Key="Icon_Sparkles">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 9.94 3 L 12 9.94 L 18.94 12 L 12 14.06 L 9.94 21 L 7.88 14.06 L 1 12 L 7.88 9.94 L 9.94 3 Z M 19 2 L 20 5 L 23 6 L 20 7 L 19 10 L 18 7 L 15 6 L 18 5 L 19 2 Z" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- ChevronDown -->
    <DrawingImage x:Key="Icon_ChevronDown">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 6 9 L 12 15 L 18 9" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- X / Close -->
    <DrawingImage x:Key="Icon_Close">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <GeometryGroup>
                        <LineGeometry StartPoint="18,6" EndPoint="6,18" />
                        <LineGeometry StartPoint="6,6" EndPoint="18,18" />
                    </GeometryGroup>
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Trash / Delete -->
    <DrawingImage x:Key="Icon_Trash">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource AccentRedBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 3 6 L 21 6 M 19 6 L 19 20 C 19 21.1 18.1 22 17 22 L 7 22 C 5.9 22 5 21.1 5 20 L 5 6 M 8 6 L 8 4 C 8 2.9 8.9 2 10 2 L 14 2 C 15.1 2 16 2.9 16 4 L 16 6" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- MoreHorizontal (3-dot kebab) -->
    <DrawingImage x:Key="Icon_More">
        <DrawingImage.Drawing>
            <DrawingGroup>
                <GeometryDrawing Brush="{DynamicResource TextSecondaryBrush}">
                    <GeometryDrawing.Geometry>
                        <GeometryGroup>
                            <EllipseGeometry Center="12,12" RadiusX="1" RadiusY="1" />
                            <EllipseGeometry Center="19,12" RadiusX="1" RadiusY="1" />
                            <EllipseGeometry Center="5,12" RadiusX="1" RadiusY="1" />
                        </GeometryGroup>
                    </GeometryDrawing.Geometry>
                </GeometryDrawing>
            </DrawingGroup>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Rename (PenLine) -->
    <DrawingImage x:Key="Icon_Rename">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 12 20 L 20 20 M 16.38 3.62 C 16.77 3.24 17.29 3.02 17.83 3.02 C 18.37 3.02 18.89 3.24 19.27 3.62 C 19.66 4.01 19.88 4.53 19.88 5.07 C 19.88 5.61 19.66 6.13 19.27 6.51 L 7 18.88 L 3 20 L 4.12 16 L 16.38 3.62 Z" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Folder -->
    <DrawingImage x:Key="Icon_Folder">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 22 19 C 22 20.1 21.1 21 20 21 L 4 21 C 2.9 21 2 20.1 2 19 L 2 5 C 2 3.9 2.9 3 4 3 L 9 3 L 11 6 L 20 6 C 21.1 6 22 6.9 22 8 L 22 19 Z" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- Export / Share (external link) -->
    <DrawingImage x:Key="Icon_Export">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 18 13 L 18 19 C 18 19.53 17.79 20.04 17.41 20.41 C 17.04 20.79 16.53 21 16 21 L 5 21 C 4.47 21 3.96 20.79 3.59 20.41 C 3.21 20.04 3 19.53 3 19 L 3 8 C 3 7.47 3.21 6.96 3.59 6.59 C 3.96 6.21 4.47 6 5 6 L 11 6 M 15 3 L 21 3 L 21 9 M 10 14 L 21 3" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <!-- MCP Tool categories -->
    <DrawingImage x:Key="Icon_FileSystem">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 14 2 L 6 2 C 4.9 2 4 2.9 4 4 L 4 20 C 4 21.1 4.9 22 6 22 L 18 22 C 19.1 22 20 21.1 20 20 L 20 8 L 14 2 Z M 14 2 L 14 8 L 20 8" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <DrawingImage x:Key="Icon_Browser">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <GeometryGroup>
                        <EllipseGeometry Center="12,12" RadiusX="10" RadiusY="10" />
                        <LineGeometry StartPoint="2,12" EndPoint="22,12" />
                        <PathGeometry Figures="M 12 2 C 14.5 5 15.7 8.4 15.7 12 C 15.7 15.6 14.5 19 12 22 M 12 2 C 9.5 5 8.3 8.4 8.3 12 C 8.3 15.6 9.5 19 12 22" />
                    </GeometryGroup>
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <DrawingImage x:Key="Icon_Clipboard">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 16 4 L 18 4 C 19.1 4 20 4.9 20 6 L 20 20 C 20 21.1 19.1 22 18 22 L 6 22 C 4.9 22 4 21.1 4 20 L 4 6 C 4 4.9 4.9 4 6 4 L 8 4 M 9 2 L 15 2 C 15.55 2 16 2.45 16 3 L 16 5 C 16 5.55 15.55 6 15 6 L 9 6 C 8.45 6 8 5.55 8 5 L 8 3 C 8 2.45 8.45 2 9 2 Z" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <DrawingImage x:Key="Icon_Terminal">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 4 17 L 10 11 L 4 5 M 12 19 L 20 19" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

    <DrawingImage x:Key="Icon_Screenshot">
        <DrawingImage.Drawing>
            <GeometryDrawing>
                <GeometryDrawing.Pen>
                    <Pen Brush="{DynamicResource TextSecondaryBrush}" Thickness="2"
                         StartLineCap="Round" EndLineCap="Round" LineJoin="Round" />
                </GeometryDrawing.Pen>
                <GeometryDrawing.Geometry>
                    <PathGeometry Figures="M 23 19 C 23 20.1 22.1 21 21 21 L 3 21 C 1.9 21 1 20.1 1 19 L 1 8 C 1 6.9 1.9 6 3 6 L 7 6 L 9 3 L 15 3 L 17 6 L 21 6 C 22.1 6 23 6.9 23 8 L 23 19 Z M 12 17 C 14.21 17 16 15.21 16 13 C 16 10.79 14.21 9 12 9 C 9.79 9 8 10.79 8 13 C 8 15.21 9.79 17 12 17 Z" />
                </GeometryDrawing.Geometry>
            </GeometryDrawing>
        </DrawingImage.Drawing>
    </DrawingImage>

</ResourceDictionary>
```

**IMPORTANT NOTES on DrawingImage approach:**

The path data above is **conceptual** — WPF GeometryDrawing with `Pen` for stroked icons can be complex. A simpler and more reliable approach:

**RECOMMENDED: Use `Path` elements directly in XAML with `Data` attribute** instead of DrawingImage resources. This is much simpler:

```xml
<!-- In Icons.xaml, define as Geometry resources instead: -->
<Geometry x:Key="Geo_Plus">M 12 5 L 12 19 M 5 12 L 19 12</Geometry>
<Geometry x:Key="Geo_Search">M 11 18 C 14.87 18 18 14.87 18 11 C 18 7.13 14.87 4 11 4 C 7.13 4 4 7.13 4 11 C 4 14.87 7.13 18 11 18 Z M 21 21 L 16.65 16.65</Geometry>
<Geometry x:Key="Geo_Copy">M 20 9 L 9 9 C 7.9 9 7 9.9 7 11 L 7 20 C 7 21.1 7.9 22 9 22 L 20 22 C 21.1 22 22 21.1 22 20 L 22 11 C 22 9.9 21.1 9 20 9 Z M 5 15 L 4 15 C 2.9 15 2 14.1 2 13 L 2 4 C 2 2.9 2.9 2 4 2 L 13 2 C 14.1 2 15 2.9 15 4 L 15 5</Geometry>
<!-- ... etc for all icons -->
```

Then use them in XAML as:
```xml
<Path Data="{StaticResource Geo_Search}" 
      Stroke="{DynamicResource TextSecondaryBrush}" StrokeThickness="2"
      StrokeStartLineCap="Round" StrokeEndLineCap="Round" StrokeLineJoin="Round"
      Width="16" Height="16" Stretch="Uniform" />
```

**Pick whichever approach compiles cleanly. The key requirement is: replace every emoji with a vector Path icon that respects theme colors via DynamicResource brushes.**

### B. Color Palette — Align with Web

**Replace theme colors to match the web app palette:**

**Dark.xaml — update to match web dark:**
```xml
<Color x:Key="BgPrimaryColor">#212121</Color>      <!-- was #0F172A (slate) -->
<Color x:Key="BgSecondaryColor">#2F2F2F</Color>     <!-- was #1E293B -->
<Color x:Key="BgSidebarColor">#171717</Color>       <!-- was #1E293B -->
<Color x:Key="TextPrimaryColor">#ECECEC</Color>     <!-- was #F8FAFC -->
<Color x:Key="TextSecondaryColor">#A0A0A0</Color>   <!-- was #94A3B8 -->
<Color x:Key="AccentGreenColor">#10A37F</Color>     <!-- was #4ADE80 — match web success -->
<Color x:Key="BorderColor">#3A3A3A</Color>          <!-- was #334155 -->
<Color x:Key="UserBubbleColor">#303030</Color>      <!-- was #2563EB — web uses gray, not blue -->
<Color x:Key="AssistantBubbleColor">#212121</Color>  <!-- was #1E293B — web has no bubble -->
<Color x:Key="CodeBgColor">#1A1A1A</Color>          <!-- was #0F172A -->
<Color x:Key="BgSelectedColor">#3A3A3A</Color>      <!-- was #334155 -->
```

**Light.xaml — update to match web light:**
```xml
<Color x:Key="BgPrimaryColor">#FFFFFF</Color>       <!-- same -->
<Color x:Key="BgSecondaryColor">#F0F0F0</Color>     <!-- was #F8FAFC -->
<Color x:Key="BgSidebarColor">#F9F9F9</Color>       <!-- was #F1F5F9 -->
<Color x:Key="TextPrimaryColor">#0D0D0D</Color>     <!-- was #1E293B -->
<Color x:Key="TextSecondaryColor">#5B5B5B</Color>   <!-- was #64748B -->
<Color x:Key="AccentGreenColor">#10A37F</Color>     <!-- was #22C55E -->
<Color x:Key="BorderColor">#E5E5E5</Color>          <!-- was #E2E8F0 -->
<Color x:Key="UserBubbleColor">#F0F0F0</Color>      <!-- was #3B82F6 — web uses gray -->
<Color x:Key="AssistantBubbleColor">#FFFFFF</Color>  <!-- was #F1F5F9 -->
<Color x:Key="CodeBgColor">#1A1A1A</Color>          <!-- was #1E293B -->
<Color x:Key="BgSelectedColor">#E5E5E5</Color>      <!-- was #E2E8F0 -->
```

**Add new color tokens to both themes:**
```xml
<Color x:Key="TextTertiaryColor">...</Color>  <!-- Dark: #6B6B6B, Light: #8C8C8C -->
<SolidColorBrush x:Key="TextTertiaryBrush" Color="{StaticResource TextTertiaryColor}" />
```

### C. User Message Bubble — Match Web Style

Web uses: right-aligned, gray background (not blue), 20px border-radius, soft shadow, max 85% width.

**Modify** `MessageBubble.xaml` user message section:
```xml
<!-- User messages: gray pill, right-aligned (like web) -->
<Border Background="{DynamicResource UserBubbleBrush}"
        CornerRadius="20"
        Padding="16,10"
        MaxWidth="{Binding ActualWidth, RelativeSource={RelativeSource AncestorType=ItemsControl}, 
                   Converter={StaticResource PercentageConverter}, ConverterParameter=0.85}"
        HorizontalAlignment="Right">
    <!-- Effect: subtle shadow -->
    <Border.Effect>
        <DropShadowEffect ShadowDepth="1" BlurRadius="3" Opacity="0.08" Color="Black" />
    </Border.Effect>
    <!-- Content: text in TextPrimary (not white!) -->
    <TextBlock Text="{Binding Content}" TextWrapping="Wrap"
               Foreground="{DynamicResource TextPrimaryBrush}" />
</Border>
```

**User message text color must be TextPrimary now** (not white) since the bubble is gray, not blue.

### D. Assistant Messages — No Bubble, Full-Width Prose (like web)

Web shows assistant messages as: full-width, no background, no avatar, prose typography with generous line-height.

**Modify** `MessageBubble.xaml` assistant section:
- Remove the blue "S" avatar circle
- Remove the gray background `AssistantBubbleBrush`
- Use full-width prose layout
- Add action buttons row: Copy, Edit (user), Regenerate (last assistant), Thumbs up/down

```xml
<!-- Assistant messages: no bubble, full-width -->
<StackPanel HorizontalAlignment="Left" MaxWidth="720">
    <!-- Markdown content (no background, no border) -->
    <controls:MarkdownViewer Content="{Binding Content}" />

    <!-- Action row (visible on hover or when streaming complete) -->
    <StackPanel Orientation="Horizontal" Margin="0,4,0,0" Opacity="0"
                x:Name="ActionRow">
        <Button Style="{DynamicResource IconButton}" ToolTip="Copy" Width="28" Height="28"
                Command="{Binding CopyCommand}">
            <Image Source="{DynamicResource Icon_Copy}" Width="14" Height="14" />
        </Button>
        <Button Style="{DynamicResource IconButton}" ToolTip="Regenerate" Width="28" Height="28"
                Visibility="{Binding IsLastAssistant, Converter=...}"
                Command="{Binding RegenerateCommand}">
            <Image Source="{DynamicResource Icon_Regenerate}" Width="14" Height="14" />
        </Button>
        <Button Style="{DynamicResource IconButton}" ToolTip="Good response" Width="28" Height="28">
            <Image Source="{DynamicResource Icon_ThumbUp}" Width="14" Height="14" />
        </Button>
        <Button Style="{DynamicResource IconButton}" ToolTip="Bad response" Width="28" Height="28">
            <Image Source="{DynamicResource Icon_ThumbDown}" Width="14" Height="14" />
        </Button>
    </StackPanel>
</StackPanel>
<!-- Hover trigger: fade ActionRow to Opacity 1 -->
```

### E. Empty State — Logo + Greeting + Suggestion Chips

Web shows: SRAN logo, time-of-day greeting, 4 suggestion chips.

**Modify** `MainWindow.xaml` empty state section:

```xml
<StackPanel HorizontalAlignment="Center" VerticalAlignment="Center"
            MaxWidth="600">
    <!-- SRAN Logo (use app icon or a larger brand mark) -->
    <Border Width="80" Height="80" CornerRadius="20" Margin="0,0,0,16"
            Background="{DynamicResource AccentBlueBrush}"
            HorizontalAlignment="Center">
        <TextBlock Text="S" FontSize="36" FontWeight="Bold" Foreground="White"
                   HorizontalAlignment="Center" VerticalAlignment="Center" />
    </Border>

    <!-- Time-based greeting -->
    <TextBlock Text="{Binding Greeting}" FontSize="22" FontWeight="SemiBold"
               HorizontalAlignment="Center"
               Foreground="{DynamicResource TextPrimaryBrush}" />
    <TextBlock Text="How can I help you today?"
               Margin="0,6,0,24"
               HorizontalAlignment="Center"
               Foreground="{DynamicResource TextSecondaryBrush}" />

    <!-- 4 Suggestion chips in a 2x2 grid -->
    <UniformGrid Columns="2" HorizontalAlignment="Center" MaxWidth="480">
        <Button Margin="4" Padding="14,12" HorizontalContentAlignment="Left"
                Command="{Binding SuggestionCommand}" CommandParameter="Help me write an email">
            <StackPanel>
                <Path Data="{StaticResource Geo_Edit}" Stroke="{DynamicResource AccentBlueBrush}"
                      StrokeThickness="1.5" Width="16" Height="16" Stretch="Uniform"
                      HorizontalAlignment="Left" Margin="0,0,0,6" />
                <TextBlock Text="Help me write" FontWeight="SemiBold" FontSize="13"
                           Foreground="{DynamicResource TextPrimaryBrush}" />
                <TextBlock Text="an email or message" FontSize="12"
                           Foreground="{DynamicResource TextSecondaryBrush}" />
            </StackPanel>
        </Button>
        <!-- Repeat for: Learn, Code, Analyze -->
    </UniformGrid>
</StackPanel>
```

### F. Model Selector in Composer

Add a model selector pill above or beside the input, matching the web's Sparkles + model name + chevron pattern.

```xml
<!-- In ChatView.xaml, above the input border -->
<Button HorizontalAlignment="Left" Margin="0,0,0,4"
        Style="{DynamicResource IconButton}" Padding="8,4"
        Click="ModelSelector_Click">
    <StackPanel Orientation="Horizontal">
        <Path Data="{StaticResource Geo_Sparkles}"
              Stroke="{DynamicResource TextSecondaryBrush}"
              StrokeThickness="1.5" Width="14" Height="14" Stretch="Uniform" />
        <TextBlock Text="{Binding CurrentModelName}" Margin="6,0,4,0"
                   FontSize="12" Foreground="{DynamicResource TextSecondaryBrush}" />
        <Path Data="{StaticResource Geo_ChevronDown}"
              Stroke="{DynamicResource TextSecondaryBrush}"
              StrokeThickness="1.5" Width="10" Height="10" Stretch="Uniform" />
    </StackPanel>
</Button>
```

### G. Sidebar Branding

Add "SRAN Chat" wordmark at the top of the sidebar:

```xml
<!-- At the top of SidebarView, before New Chat button -->
<StackPanel Orientation="Horizontal" Margin="16,14,12,10">
    <TextBlock FontSize="16" FontWeight="Bold"
               Foreground="{DynamicResource TextPrimaryBrush}">
        <Run Text="SRAN" FontWeight="ExtraBold" />
        <Run Text=" Chat" FontWeight="Normal" />
    </TextBlock>
</StackPanel>
```

### H. Conversation Context Menu — Proper Icons

Replace text-only context menu items with icon + text:

```xml
<ContextMenu>
    <MenuItem>
        <MenuItem.Icon>
            <Path Data="{StaticResource Geo_Rename}" ... Width="14" Height="14" />
        </MenuItem.Icon>
        <MenuItem.Header>
            <TextBlock Text="Rename" />
        </MenuItem.Header>
    </MenuItem>
    <MenuItem>
        <MenuItem.Icon>
            <Path Data="{StaticResource Geo_Pin}" ... />
        </MenuItem.Icon>
        <MenuItem.Header>
            <TextBlock Text="Pin / Unpin" />
        </MenuItem.Header>
    </MenuItem>
    <Separator />
    <MenuItem>
        <MenuItem.Icon>
            <Path Data="{StaticResource Geo_Trash}" ... />
        </MenuItem.Icon>
        <MenuItem.Header>
            <TextBlock Text="Delete" Foreground="{DynamicResource AccentRedBrush}" />
        </MenuItem.Header>
    </MenuItem>
</ContextMenu>
```

### I. Keyboard Shortcut Hints

Display shortcut hints like the web does:

- New Chat button: show `Ctrl+N` hint
- Search: show `Ctrl+K` hint
- Sidebar toggle: show `Ctrl+Shift+S` hint

```xml
<!-- In New Chat button -->
<StackPanel Orientation="Horizontal" HorizontalAlignment="Center">
    <Path Data="{StaticResource Geo_Plus}" ... Width="14" Height="14" Margin="0,0,6,0" />
    <TextBlock Text="New chat" />
    <TextBlock Text="Ctrl+N" FontSize="10" Margin="10,0,0,0" Opacity="0.5"
               Foreground="{DynamicResource TextOnAccentBrush}" />
</StackPanel>
```

### J. Font — Outfit + JetBrains Mono

**Option 1 (recommended):** Bundle Outfit and JetBrains Mono as embedded resources:

```xml
<!-- In App.xaml or Styles.xaml -->
<FontFamily x:Key="FontBrand">pack://application:,,,/Assets/Fonts/#Outfit</FontFamily>
<FontFamily x:Key="FontCode">pack://application:,,,/Assets/Fonts/#JetBrains Mono</FontFamily>
```

**Option 2 (simpler):** Use system fonts that are close:
- Brand: `Segoe UI Variable` (Windows 11) or `Segoe UI` (Windows 10)
- Code: `Cascadia Code` (ships with Windows Terminal) or `Consolas`

If bundling Outfit is too much overhead, at minimum switch code font to `Cascadia Code, Cascadia Mono, Consolas` to match web's JetBrains Mono vibe.

---

## REPLACEMENT MAP — Every emoji → icon

| Location | Current | Replace With |
|---|---|---|
| SidebarView: New Chat | `+ New chat` text | `Icon_Plus` + "New chat" + `Ctrl+N` |
| SidebarView: Search | plain TextBox | `Icon_Search` prefix + watermark |
| SidebarView: Pin indicator | 📌 emoji | `Icon_Pin` (12x12, TextSecondary) |
| MainWindow: Settings | ⚙ emoji | `Icon_Settings` (16x16) |
| MainWindow: Sidebar toggle | ☰ emoji | `Icon_SidebarToggle` (16x16) |
| MainWindow: Log out | "Log out" text | `Icon_Logout` (14x14) + "Log out" |
| ChatView: Agent toggle | 🤖 emoji | `Icon_Agent` (16x16) |
| ChatView: Agent indicator | 🤖 + text | `Icon_Agent` (14x14) + text |
| ChatView: Attach | 📎 emoji | `Icon_Attach` (16x16) |
| ChatView: Send | "Send" text | `Icon_Send` (16x16, white on accent) |
| ChatView: Stop | "Stop" text | `Icon_Stop` (14x14, white on red) |
| ChatView: File chip 📎 | 📎 emoji | `Icon_Attach` (12x12) |
| ChatView: File ✓ | ✓ text | `Icon_Check` (12x12, green) |
| ChatView: File ✕ remove | ✕ text | `Icon_Close` (10x10) |
| MessageBubble: Copy | "Copy" text | `Icon_Copy` (14x14) → `Icon_Check` on copied |
| MessageBubble: Avatar "S" | Blue circle + "S" | **Remove** (web has no avatar) |
| Agent panel: status ✓/✕/⏳ | emoji chars | `Icon_Check` / `Icon_Close` / spinner |
| ToolConsent: category icon | emoji (📁🌐📋⚡🖥🔧) | `Icon_FileSystem` / `Icon_Browser` / `Icon_Clipboard` / `Icon_Terminal` / `Icon_Screenshot` / gear |
| ToolConsent: ⚠ warning | ⚠ emoji | Orange triangle Path icon |
| FirstRun: "S" branding | "S" in blue circle | Keep — this is the brand mark |

---

## NEW FEATURES TO ADD (from web)

### 1. Edit User Message
- Pencil icon on hover of user message
- Click → inline TextBox replacing the message text
- Save (Ctrl+Enter) / Cancel (Escape) buttons
- On save: re-send the edited message (creates new branch)

### 2. Regenerate Last Response  
- Circular-arrows icon below the last assistant message
- Click → re-sends the last user message for a new response

### 3. Feedback (Thumbs Up/Down)
- Below each assistant message (next to Copy)
- Click → sends feedback to API (if endpoint exists) or logs locally

### 4. Code Block Enhancement
- Language label in header (left-aligned)
- Copy button with icon (not text)
- Long blocks (>30 lines): collapsed by default, "Show more" toggle

### 5. Export Conversation
- Menu button in header bar: Export as Markdown, Export as JSON, Copy all

---

## BUILD ORDER

1. Create `Icons.xaml` with all Geometry resources
2. Merge `Icons.xaml` in `App.xaml` ResourceDictionaries
3. Update theme colors (Dark.xaml + Light.xaml) to match web palette
4. Replace ALL emoji icons with Path elements (see replacement map)
5. Restyle user message bubble (gray, 20px radius, TextPrimary text)
6. Restyle assistant messages (no bubble, no avatar, full-width prose)
7. Add message action row (copy icon, regenerate, thumbs up/down)
8. Build empty state (logo, greeting, suggestion chips)
9. Add model selector in composer
10. Add sidebar branding "SRAN Chat"
11. Add keyboard shortcut hints
12. Update context menu with icons
13. Add edit-message inline UI
14. Update code block header (language label + icon copy button)
15. Verify all icons render correctly in both themes
16. Run all 98 tests — ensure no regressions

---

## VERIFICATION CHECKLIST

1. [ ] Zero emoji characters remain in any XAML (grep for Unicode ranges U+1F000-1FFFF, U+2600-26FF, U+2700-27BF)
2. [ ] All icons are theme-aware (switch dark↔light, icons change color)
3. [ ] Icon sizes consistent: 12px (inline small), 14-16px (buttons), 18-20px (prominent)
4. [ ] Color palette matches web: dark bg #212121, sidebar #171717, text #ECECEC
5. [ ] User bubble is gray (not blue), text is TextPrimary (not white)
6. [ ] Assistant messages have no bubble background, no avatar
7. [ ] Message action row (copy/regenerate/thumbs) visible on hover
8. [ ] Empty state shows logo + greeting + 4 suggestion chips
9. [ ] Model selector pill visible in composer area
10. [ ] Sidebar has "SRAN Chat" wordmark
11. [ ] Context menu items have icons
12. [ ] Keyboard shortcut hints displayed (Ctrl+N, Ctrl+K)
13. [ ] Send button shows arrow icon (not "Send" text)
14. [ ] Stop button shows filled square icon (not "Stop" text)
15. [ ] Code blocks have language label + icon copy button
16. [ ] 98 existing tests still pass
17. [ ] Dark + light themes: every icon visible and correct color

---

*End of UI Alignment Prompt*
