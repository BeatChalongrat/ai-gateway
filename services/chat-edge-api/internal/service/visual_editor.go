package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

type VisualBox struct {
	Left   float64 `json:"left"`
	Top    float64 `json:"top"`
	Width  float64 `json:"width"`
	Height float64 `json:"height"`
}
type VisualField struct {
	ID       string             `json:"id"`
	Value    string             `json:"value"`
	Page     int                `json:"page"`
	Style    map[string]any     `json:"style"`
	Box      *VisualBox         `json:"box,omitempty"`
	Insets   map[string]float64 `json:"insets,omitempty"`
	Vertical string             `json:"vertical,omitempty"`
}
type VisualPage struct {
	Page   int     `json:"page"`
	Width  float64 `json:"width"`
	Height float64 `json:"height"`
	Image  string  `json:"image"`
}
type VisualInspection struct {
	Kind        string             `json:"kind"`
	SHA256      string             `json:"sha256"`
	Width       float64            `json:"width"`
	Height      float64            `json:"height"`
	Fields      []VisualField      `json:"fields"`
	Blocks      json.RawMessage    `json:"blocks,omitempty"`
	Margins     map[string]float64 `json:"margins,omitempty"`
	Page        int                `json:"page"`
	PageCount   int                `json:"page_count"`
	Background  string             `json:"background,omitempty"`
	Pages       []VisualPage       `json:"pages,omitempty"`
	AIAvailable bool               `json:"ai_available"`
	AIModel     string             `json:"ai_model,omitempty"`
	Transparent bool               `json:"transparent,omitempty"`
}

func (s *FileService) SetVisualGateway(g visualGateway, model, imageModel string) {
	s.visualGateway = g
	s.visualModel = model
	s.visualImageModel = imageModel
}
func visualHash(data []byte) string { sum := sha256.Sum256(data); return hex.EncodeToString(sum[:]) }

// runVisual never executes model code. A fixed sandbox script owns validation.
func (s *FileService) runVisual(ctx context.Context, name string, data []byte, params map[string]any) (*VisualInspection, []byte, []byte, error) {
	if s.sandbox == nil {
		return nil, nil, nil, ErrFileSandboxUnavailable
	}
	raw, err := json.Marshal(params)
	if err != nil || len(raw) > 2<<20 {
		return nil, nil, nil, ErrEditorInput
	}
	ext := strings.ToLower(filepath.Ext(name))
	outputExt := ext
	if visualImage(name) {
		outputExt = ".png"
	}
	res, err := s.sandbox.Execute(ctx, sandbox.Spec{Mode: sandbox.ModeScript, Script: "visual_editor.py", Args: []string{"source" + ext, "request.json", "out/visual" + outputExt}, Inputs: []sandbox.InputFile{{Name: "source" + ext, Bytes: data}, {Name: "request.json", Bytes: raw}}, Limits: sandbox.Limits{Wall: 90 * time.Second, MaxOutputB: 16 << 20}})
	if res != nil {
		defer res.Cleanup()
	}
	if err != nil {
		return nil, nil, nil, err
	}
	if res == nil {
		return nil, nil, nil, ErrFileSandboxUnavailable
	}
	if res.Exit != 0 || res.Truncated || res.TimedOut {
		var failure struct {
			Error string `json:"error"`
		}
		_ = json.Unmarshal(res.Stdout, &failure)
		if failure.Error == "" {
			failure.Error = "Visual editor did not complete"
		}
		return nil, nil, nil, fmt.Errorf("%w: %.1000s", ErrEditorInput, failure.Error)
	}
	var inspection VisualInspection
	if err = json.Unmarshal(res.Stdout, &inspection); err != nil {
		return nil, nil, nil, err
	}
	var edited, background []byte
	if art := res.Artifact("visual" + outputExt); art != nil {
		edited, err = ReadArtifact(art.Path)
		if err != nil {
			return nil, nil, nil, err
		}
	}
	if art := res.Artifact("background.pptx"); art != nil {
		background, err = ReadArtifact(art.Path)
		if err != nil {
			return nil, nil, nil, err
		}
	}
	return &inspection, edited, background, nil
}

func visualImage(name string) bool {
	switch strings.ToLower(filepath.Ext(name)) {
	case ".png", ".jpg", ".jpeg", ".webp":
		return true
	}
	return false
}
func (s *FileService) renderVisual(ctx context.Context, name string, data []byte, page int, all bool) (*VisualInspection, error) {
	if visualImage(name) {
		v, _, _, err := s.runVisual(ctx, name, data, map[string]any{"mode": "inspect"})
		return v, err
	}
	pdf := data
	if !strings.EqualFold(filepath.Ext(name), ".pdf") {
		if s.officeQuality == nil || s.officeQuality.converter == nil || !s.officeQuality.converter.Available() {
			return nil, fmt.Errorf("%w: Office rendering is unavailable", ErrEditorInput)
		}
		var err error
		pdf, err = s.officeQuality.converter.Convert(ctx, name, data, "pdf")
		if err != nil {
			return nil, err
		}
	}
	result, _, _, err := s.runVisual(ctx, "render.pdf", pdf, map[string]any{"mode": "render", "page": page, "all_pages": all})
	return result, err
}

func (s *FileService) visualFile(ctx context.Context, f *model.ConversationFile, req FileEditRequest) (*FileEditResult, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Minute)
	defer cancel()
	path, cleanup, err := s.storage.OpenLocalCopy(ctx, f.StoragePath)
	if err != nil {
		return nil, err
	}
	defer cleanup()
	data, err := ReadArtifact(path)
	if err != nil {
		return nil, err
	}
	if req.Mode != "visual_inspect" && req.Mode != "visual_preview" {
		if req.SHA256 != visualHash(data) {
			return nil, fmt.Errorf("%w: Source changed; reload before editing", ErrEditorInput)
		}
		versions, e := s.files.ListVersions(ctx, f.TenantID, f.ID)
		if e != nil {
			return nil, e
		}
		for _, v := range versions {
			if v.ParentFileID != nil && *v.ParentFileID == f.ID {
				return nil, repository.ErrFileRevisionConflict
			}
		}
	}
	if req.Mode == "ai_repair" {
		return s.repairVisual(ctx, f, data)
	}
	if req.Mode == "visual_preview" {
		view, e := s.renderVisual(ctx, f.OriginalFilename, data, req.Page, false)
		return &FileEditResult{Visual: view}, e
	}
	if req.Mode == "visual_inspect" {
		view, _, background, e := s.runVisual(ctx, f.OriginalFilename, data, map[string]any{"mode": "inspect", "page": req.Page})
		if e != nil {
			return nil, e
		}
		view.AIAvailable = s.visualGateway != nil && s.visualModel != ""
		if view.Kind == "docx" || view.Kind == "pptx" {
			view.AIAvailable = view.AIAvailable && s.officeQuality != nil && s.officeQuality.converter != nil && s.officeQuality.converter.Available()
		}
		view.AIModel = s.visualModel
		if view.Kind == "pptx" {
			rendered, e := s.renderVisual(ctx, "background.pptx", background, req.Page, false)
			if e != nil {
				return nil, e
			}
			if len(rendered.Pages) != 1 {
				return nil, fmt.Errorf("missing slide render")
			}
			view.Background = rendered.Pages[0].Image
		}
		return &FileEditResult{Visual: view}, nil
	}
	_, edited, _, err := s.runVisual(ctx, f.OriginalFilename, data, map[string]any{"mode": "edit", "page": req.Page, "sha256": req.SHA256, "changes": req.Changes, "image": req.Image})
	if err != nil {
		return nil, err
	}
	if len(edited) == 0 {
		return nil, fmt.Errorf("visual editor produced no output")
	}
	return s.saveVisualRevision(ctx, f, edited, "visual_edit", nil)
}

func (s *FileService) saveVisualRevision(ctx context.Context, f *model.ConversationFile, data []byte, action string, review *VisualReviewReport) (*FileEditResult, error) {
	name, contentType := f.OriginalFilename, f.ContentType
	if visualImage(name) {
		name = strings.TrimSuffix(name, filepath.Ext(name)) + ".png"
		contentType = "image/png"
	}
	var quality *OfficeQualityReport
	if officeExtension(name) {
		checked, err := s.officeQuality.reviewOnce(ctx, name, data)
		if err != nil {
			return nil, err
		}
		quality = &checked.Report
	}
	changes, _ := json.Marshal(map[string]any{"action": action, "review": review, "quality": quality})
	row, err := s.SaveGeneratedFile(ctx, GeneratedFileInput{TenantID: f.TenantID, UserID: f.UserID, ConversationID: f.ConversationID, ParentFileID: &f.ID, OriginalFilename: name, ContentType: contentType, Data: data, ChangeLog: string(changes)})
	if err != nil {
		return nil, err
	}
	return &FileEditResult{File: row, Quality: quality, Review: review}, nil
}
