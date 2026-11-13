package service

import (
	"context"
	"encoding/json"
	"github.com/google/uuid"
)

func (s *MessageService) SetFileEditor(files *FileService) {
	if s.toolExec != nil {
		s.toolExec.editor = files
	}
}

func fileEditorToolDef() GatewayTool {
	str := map[string]any{"type": "string"}
	num := map[string]any{"type": "number"}
	flag := map[string]any{"type": "boolean"}
	box := map[string]any{"type": "object", "properties": map[string]any{"left": num, "top": num, "width": num, "height": num}, "required": []string{"left", "top", "width", "height"}, "additionalProperties": false}
	style := map[string]any{"type": "object", "properties": map[string]any{"size": num, "bold": flag, "italic": flag, "underline": flag, "color": str, "align": map[string]any{"type": "string", "enum": []string{"left", "center", "right", "justify"}}, "space_after": num}, "additionalProperties": false}
	return GatewayTool{Type: "function", Function: GatewayToolFunction{Name: "edit_office_file", Description: "Edit conversation files. visual_inspect returns DOCX paragraphs, PPTX text frames, native PDF text objects or image controls. visual_edit applies plain text/style/box edits using inspected sha256 and page. PDF excludes scans, forms and rotated text. ai_repair uses configured vision review on rendered Word/PPT/images, preserves Office text, and creates a proposal for user review, never auto-publishes (may use model credits). Inspect first. Use inspect/edit for XLSX typed cells/formulas, CSV and text; keep sheet/offset. history lists versions; restore creates a new version from target_id. Conflicts require reloading latest version.", Parameters: map[string]any{"type": "object", "properties": map[string]any{
		"file_id": str, "mode": map[string]any{"type": "string", "enum": []string{"inspect", "edit", "history", "restore", "visual_inspect", "visual_edit", "ai_repair"}}, "sha256": str, "sheet": str, "offset": map[string]any{"type": "integer"}, "page": map[string]any{"type": "integer", "minimum": 0}, "target_id": str,
		"image":   map[string]any{"type": "object", "properties": map[string]any{"rotation": map[string]any{"type": "integer", "enum": []int{0, 90, 180, 270}}, "brightness": num, "contrast": num}},
		"changes": map[string]any{"type": "array", "items": map[string]any{"type": "object", "properties": map[string]any{"id": str, "value": str, "type": map[string]any{"type": "string", "enum": []string{"text", "number", "formula"}}, "style": style, "box": box}, "required": []string{"id", "value"}}}}, "required": []string{"file_id", "mode"}}}}
}

func (e *ToolExecutor) execFileEditor(ctx context.Context, args string, ec ToolExecContext) string {
	if e.editor == nil {
		return "File editor unavailable."
	}
	var request struct {
		FileID uuid.UUID `json:"file_id"`
		FileEditRequest
	}
	if err := json.Unmarshal([]byte(args), &request); err != nil {
		return "Invalid file editor arguments."
	}
	request.ConversationID = ec.ConversationID
	if request.Mode == "ai_repair" {
		request.Mode = "ai_propose"
	}
	result, err := e.editor.EditFile(ctx, ec.TenantID, ec.UserID, request.FileID, request.FileEditRequest)
	if err != nil {
		return "File editor failed: " + err.Error()
	}
	out := map[string]any{"inspection": result.Inspection, "quality": result.Quality, "review": result.Review}
	if result.Work != nil {
		out["work"] = result.Work
		out["review_required"] = "Open the original file to review the proposal. It is not saved until the user accepts."
		out["file_id"] = request.FileID // Refer to the original without reattaching/moving it.
	}
	if result.Visual != nil {
		// Binary page previews belong in the UI, not in tool-result text tokens.
		result.Visual.Background = ""
		result.Visual.Pages = nil
		result.Visual.Blocks = nil
		out["visual"] = result.Visual
	}
	if result.File != nil {
		out["file_id"] = result.File.ID
		out["version"] = result.File.Version
		// Attach the new revision to the assistant turn through the normal readback.
		if ec.MessageID != uuid.Nil {
			if _, err := e.editor.files.AttachToMessage(ctx, ec.TenantID, ec.ConversationID, ec.MessageID, []uuid.UUID{result.File.ID}); err != nil {
				out["attachment_warning"] = "Saved revision; open it in file history"
			}
		}
	}
	if result.Versions != nil {
		versions := make([]map[string]any, 0, len(result.Versions))
		for _, v := range result.Versions {
			versions = append(versions, map[string]any{"id": v.ID, "version": v.Version, "parent_file_id": v.ParentFileID, "filename": v.OriginalFilename, "change_log": v.ChangeLog, "created_at": v.CreatedAt})
		}
		out["versions"] = versions
	}
	raw, _ := json.Marshal(out)
	return string(raw)
}
