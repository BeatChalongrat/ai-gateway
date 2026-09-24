package handler

// FIX131 A2/A3 — what the execute routes answer when a plan fails, and the
// outcome they keep so the card still says so after a reload.
//
// The error envelope keeps its code/message (the desktop matches on them)
// and gains `reason` — the closed set the page words in Thai with a next
// step — and the input file it names. `message` is a fixed English line for
// developers: never the generator's own error, never docker's stderr (both
// go to the log through service.LogPlanFailure).

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type planErrorEnvelope struct {
	Error struct {
		Code           string `json:"code"`
		Message        string `json:"message"`
		Reason         string `json:"reason"`
		SourceFileID   string `json:"source_file_id,omitempty"`
		SourceFilename string `json:"source_filename,omitempty"`
	} `json:"error"`
}

// planErrorMessages is the developer line per reason (the page never shows
// it; it shows plan.fail.<reason>).
var planErrorMessages = map[string]string{
	service.PlanFailSourceEncrypted:  "the source file is password-protected",
	service.PlanFailSourceScanned:    "the source file is a scan with no readable text",
	service.PlanFailSourceDamaged:    "the source file is damaged",
	service.PlanFailSourceUnreadable: "the source file could not be read",
	service.PlanFailSourceNotFound:   "source file not found",
	service.PlanFailInvalidPlan:      "the plan is not valid",
	service.PlanFailGenerator:        "the file could not be generated",
	service.PlanFailTimeout:          "generating the file took too long",
	service.PlanFailValidation:       "the generated file failed validation",
	service.PlanFailSandbox:          "the document generator is not available",
	service.PlanFailInternal:         "internal error",
}

// writePlanError answers a failed execution.
func writePlanError(w http.ResponseWriter, err error) {
	reason := service.PlanFailInternal
	var src *model.ConversationFile
	if pe, ok := service.AsPlanExecError(err); ok {
		reason = pe.Reason
		src = pe.Source
	}
	status := service.PlanFailureStatus(reason)
	var env planErrorEnvelope
	switch status {
	case http.StatusNotFound:
		env.Error.Code = CodeNotFound
	case http.StatusUnprocessableEntity:
		env.Error.Code = CodeValidationError
	default:
		env.Error.Code = CodeInternalError
	}
	env.Error.Message = planErrorMessages[reason]
	env.Error.Reason = reason
	if src != nil {
		env.Error.SourceFileID = src.ID.String()
		env.Error.SourceFilename = src.OriginalFilename
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(env)
}

// recordPlanOutcome keeps a failure (or clears one after a success) for the
// plan's message. Nothing is kept when no message owns the plan — the same
// rule as the generated file's link (FIX126 F4).
func recordPlanOutcome(ctx context.Context, store service.PlanOutcomeStore, convID uuid.UUID,
	owner *uuid.UUID, format, outputFilename string, sourceFileID uuid.UUID, err error,
) {
	if store == nil || owner == nil {
		return
	}
	if err != nil {
		store.RecordFailure(ctx, convID, service.PlanOutcomeFromError(*owner, format, outputFilename, sourceFileID, err))
		return
	}
	store.ClearFailure(ctx, convID, *owner, service.PlanFenceForFormat(format), outputFilename)
}

// madeFromResponse (FIX131 A4) — the file a generated file was made from,
// and how: "edit" (a copy of it was edited) or "input" (it was read and a
// new file built). The card says "จาก fixture.pdf → สร้าง summary.docx".
type madeFromResponse struct {
	ID       string `json:"id"`
	Filename string `json:"filename"`
	Role     string `json:"role"`
}

// madeFrom describes out's provenance from parent; nil when out is not an
// Office/PDF file made from one.
func madeFrom(out, parent *model.ConversationFile) *madeFromResponse {
	if out == nil || parent == nil || !out.IsGenerated {
		return nil
	}
	kind := service.OfficeKind(out.ContentType, out.OriginalFilename)
	if kind == "other" {
		return nil
	}
	return &madeFromResponse{
		ID:       parent.ID.String(),
		Filename: parent.OriginalFilename,
		Role:     string(service.PlanSourceRoleFor(kind, parent)),
	}
}
