package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
	"github.com/google/uuid"
)

var ErrEditorInput = errors.New("invalid editor request")

type FileEditRequest struct {
	Page           int              `json:"page,omitempty"`
	Image          map[string]any   `json:"image,omitempty"`
	Mode           string           `json:"mode"`
	SHA256         string           `json:"sha256,omitempty"`
	Sheet          string           `json:"sheet,omitempty"`
	Offset         int              `json:"offset,omitempty"`
	Changes        []FileEditChange `json:"changes,omitempty"`
	TargetID       uuid.UUID        `json:"target_id,omitempty"`
	ConversationID uuid.UUID        `json:"conversation_id,omitempty"`
}
type FileEditChange struct {
	Style map[string]any `json:"style,omitempty"`
	Box   *VisualBox     `json:"box,omitempty"`
	ID    string         `json:"id"`
	Value string         `json:"value"`
	Type  string         `json:"type,omitempty"`
}
type FileEditResult struct {
	Work       *repository.FileWork     `json:"work,omitempty"`
	Visual     *VisualInspection        `json:"visual,omitempty"`
	Review     *VisualReviewReport      `json:"review,omitempty"`
	Inspection json.RawMessage          `json:"inspection,omitempty"`
	File       *model.ConversationFile  `json:"-"`
	Versions   []model.ConversationFile `json:"-"`
	Quality    *OfficeQualityReport     `json:"quality,omitempty"`
}

func (s *FileService) SetOfficeQuality(q *OfficeQuality) { s.officeQuality = q }

// Every mode starts with tenant AND user ownership. Agents additionally pin
// the conversation; a file ID in a model response never grants access.
func (s *FileService) EditFile(ctx context.Context, tenant, user, id uuid.UUID, req FileEditRequest) (*FileEditResult, error) {
	f, err := s.Get(ctx, tenant, user, id)
	if err != nil {
		return nil, err
	}
	if req.ConversationID != uuid.Nil && req.ConversationID != f.ConversationID {
		return nil, ErrFileNotFound
	}
	if req.Mode == "ai_propose" {
		work, err := s.StartArtifactWork(ctx, tenant, user, id, ArtifactWorkRequest{Action: "repair", SHA: req.SHA256, Page: req.Page})
		return &FileEditResult{Work: work}, err
	}
	if req.Mode == "visual_inspect" || req.Mode == "visual_edit" || req.Mode == "visual_preview" || req.Mode == "ai_repair" {
		return s.visualFile(ctx, f, req)
	}
	if req.Mode == "history" || req.Mode == "restore" {
		versions, err := s.files.ListVersions(ctx, tenant, id)
		if err != nil {
			return nil, err
		}
		safe := make([]model.ConversationFile, 0, len(versions))
		for _, v := range versions {
			if v.UserID == user && v.ConversationID == f.ConversationID {
				safe = append(safe, v)
			}
		}
		if req.Mode == "history" {
			return &FileEditResult{Versions: safe}, nil
		}
		var target *model.ConversationFile
		for i := range safe {
			if safe[i].ID == req.TargetID {
				target = &safe[i]
				break
			}
		}
		if target == nil || target.ID == id {
			return nil, fmt.Errorf("%w: choose another version in this file history", ErrEditorInput)
		}
		path, cleanup, err := s.storage.OpenLocalCopy(ctx, target.StoragePath)
		if err != nil {
			return nil, err
		}
		defer cleanup()
		data, err := ReadArtifact(path)
		if err != nil {
			return nil, err
		}
		row, err := s.SaveGeneratedFile(ctx, GeneratedFileInput{TenantID: tenant, UserID: user, ConversationID: f.ConversationID, Data: data, ContentType: target.ContentType, OriginalFilename: target.OriginalFilename, ParentFileID: &id, ChangeLog: "Restored from " + target.ID.String()})
		if err != nil {
			return nil, err
		}
		return &FileEditResult{File: row}, nil
	}
	if req.Mode != "inspect" && req.Mode != "edit" {
		return nil, fmt.Errorf("%w: unsupported mode", ErrEditorInput)
	}
	if s.sandbox == nil {
		return nil, ErrFileSandboxUnavailable
	}
	ext := strings.ToLower(filepath.Ext(f.OriginalFilename))
	switch ext {
	case ".docx", ".pptx", ".xlsx", ".pdf", ".csv", ".txt", ".md", ".json":
	default:
		return nil, ErrPreviewUnsupported
	}
	if req.Mode == "edit" && (len(req.SHA256) != 64 || len(req.Changes) < 1 || len(req.Changes) > 1000) {
		return nil, ErrEditorInput
	}
	path, cleanup, err := s.storage.OpenLocalCopy(ctx, f.StoragePath)
	if err != nil {
		return nil, err
	}
	defer cleanup()
	params, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	if len(params) > 2<<20 {
		return nil, ErrEditorInput
	}
	res, err := s.sandbox.Execute(ctx, sandbox.Spec{Mode: sandbox.ModeScript, Script: "file_editor.py", Args: []string{"source" + ext, "request.json", "out/edited" + ext}, Inputs: []sandbox.InputFile{{Name: "source" + ext, LocalPath: path}, {Name: "request.json", Bytes: params}}, Limits: sandbox.Limits{Wall: 90 * time.Second, MaxOutputB: 4 << 20}})
	if res != nil {
		defer res.Cleanup()
	}
	if err != nil {
		return nil, err
	}
	if res == nil {
		return nil, ErrFileSandboxUnavailable
	}
	if res.Exit != 0 || res.Truncated || res.TimedOut {
		var failure struct {
			Error string `json:"error"`
		}
		_ = json.Unmarshal(res.Stdout, &failure)
		if failure.Error == "" {
			failure.Error = strings.TrimSpace(string(res.Stderr))
		}
		if failure.Error == "" {
			failure.Error = "Sandbox editor did not complete"
		}
		return nil, fmt.Errorf("%w: %.1000s", ErrEditorInput, failure.Error)
	}
	if !json.Valid(res.Stdout) {
		return nil, errors.New("invalid editor inspection")
	}
	if req.Mode == "inspect" {
		return &FileEditResult{Inspection: json.RawMessage(res.Stdout)}, nil
	}
	art := res.Artifact("edited" + ext)
	if art == nil {
		return nil, errors.New("editor produced no file")
	}
	data, err := ReadArtifact(art.Path)
	if err != nil {
		return nil, err
	}
	var quality *OfficeQualityReport
	if officeExtension(f.OriginalFilename) {
		reviewed, e := s.officeQuality.review(ctx, f.OriginalFilename, data)
		if e != nil {
			return nil, e
		}
		data = reviewed.Data
		quality = &reviewed.Report
	}
	change, _ := json.Marshal(map[string]any{"action": "edit", "fields": len(req.Changes), "source_sha256": req.SHA256, "quality": quality})
	row, err := s.SaveGeneratedFile(ctx, GeneratedFileInput{TenantID: tenant, UserID: user, ConversationID: f.ConversationID, Data: data, ContentType: f.ContentType, OriginalFilename: f.OriginalFilename, ParentFileID: &id, ChangeLog: string(change)})
	if err != nil {
		return nil, err
	}
	return &FileEditResult{File: row, Quality: quality}, nil
}
