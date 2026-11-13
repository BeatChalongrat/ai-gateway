package service

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
	"github.com/google/uuid"
	"mime"
	"time"
)

var workspaceTemplates = map[string]string{
	"pptx.executive_brief": "pptx", "pptx.project_update": "pptx", "pptx.proposal": "pptx",
	"docx.business_report": "docx", "docx.proposal": "docx", "docx.meeting_minutes": "docx",
	"pdf.business_report": "pdf", "pdf.invoice": "pdf", "xlsx.budget": "xlsx", "xlsx.tracker": "xlsx", "xlsx.analysis": "xlsx", "csv.data_export": "csv",
}

type WorkspaceTemplateRequest struct {
	Template string          `json:"template"`
	Data     json.RawMessage `json:"data"`
}

func (s *FileService) CreateWorkspaceTemplate(ctx context.Context, tenant, user, conversation uuid.UUID, req WorkspaceTemplateRequest) (*FileEditResult, error) {
	conv, e := s.conversations.GetByID(ctx, tenant, conversation)
	if e != nil || conv == nil || conv.UserID != user {
		return nil, ErrFileNotFound
	}
	ext, ok := workspaceTemplates[req.Template]
	if !ok || len(req.Data) > 1<<20 || !json.Valid(req.Data) {
		return nil, ErrEditorInput
	}
	if s.sandbox == nil {
		return nil, ErrFileSandboxUnavailable
	}
	raw, _ := json.Marshal(req)
	filename := "starter." + ext
	result, e := s.sandbox.Execute(ctx, sandbox.Spec{Mode: sandbox.ModeScript, Script: "workspace_template.py", Args: []string{"request.json", "out/" + filename}, Inputs: []sandbox.InputFile{{Name: "request.json", Bytes: raw}}, Limits: sandbox.Limits{Wall: 90 * time.Second, MaxOutputB: 1 << 20}})
	if result != nil {
		defer result.Cleanup()
	}
	if e != nil {
		return nil, e
	}
	if result == nil {
		return nil, ErrFileSandboxUnavailable
	}
	if result.Exit != 0 || result.TimedOut || result.Truncated {
		var failure struct {
			Error string `json:"error"`
		}
		_ = json.Unmarshal(result.Stdout, &failure)
		return nil, fmt.Errorf("%w: %.500s", ErrEditorInput, failure.Error)
	}
	artifact := result.Artifact(filename)
	if artifact == nil {
		return nil, fmt.Errorf("template produced no file")
	}
	data, e := ReadArtifact(artifact.Path)
	if e != nil {
		return nil, e
	}
	var quality *OfficeQualityReport
	if officeExtension(filename) {
		reviewed, err := s.officeQuality.review(ctx, filename, data)
		if err != nil {
			return nil, err
		}
		data = reviewed.Data
		quality = &reviewed.Report
	}
	title := struct {
		Title string `json:"title"`
	}{}
	_ = json.Unmarshal(req.Data, &title)
	if title.Title != "" {
		filename = sanitizeFilename(title.Title) + "." + ext
	}
	file, e := s.SaveGeneratedFile(ctx, GeneratedFileInput{TenantID: tenant, UserID: user, ConversationID: conversation, OriginalFilename: filename, ContentType: mime.TypeByExtension("." + ext), Data: data, ChangeLog: "Created from template " + req.Template})
	if e != nil {
		return nil, e
	}
	return &FileEditResult{File: file, Quality: quality}, nil
}
