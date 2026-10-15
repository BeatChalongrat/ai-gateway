package runner

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// pagesJob (AM21 part 4) makes the pictures a person sees of a delivered file before sending it: the
// stored file, converted to PDF by the same converter that makes the PDFs when it is not one already,
// its first pages rasterised and kept beside it with a manifest. The picture is of the delivered file
// itself, never drawn again from the data.
func (r *Runner) pagesJob(ctx context.Context, job am.Run, log *slog.Logger) error {
	rc, err := r.load(ctx, job, log)
	if err != nil {
		return r.fail(ctx, job, "load", err)
	}
	var params struct {
		ArtifactID uuid.UUID `json:"artifact_id"`
	}
	_ = json.Unmarshal(job.Params, &params)
	if r.Converter == nil || !r.Converter.Available() {
		return r.fail(ctx, job, "pages", ErrNoConverter)
	}
	art, err := r.Store.GetArtifact(ctx, job.TenantID, job.UserID, params.ArtifactID)
	if err != nil {
		return r.fail(ctx, job, "pages", err)
	}
	body, _, _, err := r.Files.Download(ctx, art.StorageKey)
	if err != nil {
		return r.fail(ctx, job, "pages", err)
	}
	data, err := io.ReadAll(body)
	body.Close()
	if err != nil {
		return r.fail(ctx, job, "pages", err)
	}
	pdf := data
	if art.Format != "pdf" {
		if pdf, err = r.Converter.Convert(ctx, art.Name, data, "pdf"); err != nil {
			return r.fail(ctx, job, "pages", fmt.Errorf("convert %s: %w", art.Name, err))
		}
	}
	res, err := r.Converter.Pages(ctx, pdf, 1, am.PagesMax, am.PagesDPI)
	if err != nil {
		return r.fail(ctx, job, "pages", err)
	}
	for i, b64 := range res.PNGs {
		png, err := base64.StdEncoding.DecodeString(b64)
		if err != nil {
			return r.fail(ctx, job, "pages", fmt.Errorf("page %d: %w", i+1, err))
		}
		if err := r.Files.Upload(ctx, am.PageKey(art.StorageKey, i+1), bytes.NewReader(png), "image/png", int64(len(png))); err != nil {
			return r.fail(ctx, job, "pages", err)
		}
	}
	manifest, _ := json.Marshal(am.PagesManifest{Pages: res.Pages, Available: len(res.PNGs), DPI: res.DPI, SHA256: art.SHA256, RenderedAt: time.Now().UTC()})
	if err := r.Files.Upload(ctx, am.PagesManifestKey(art.StorageKey), bytes.NewReader(manifest), "application/json", int64(len(manifest))); err != nil {
		return r.fail(ctx, job, "pages", err)
	}
	log.Info("pages kept", "artifact", art.ID, "pages", res.Pages, "available", len(res.PNGs))
	return r.finish(rc, am.RunSucceeded, "")
}
