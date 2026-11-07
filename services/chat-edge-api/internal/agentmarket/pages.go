package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

// ── a person sees the file before they send it (AM21 part 4) ─────────────
//
// After a run, the files card shows the finished document's first pages and
// the deck's slides as pictures, in the chat, without downloading. The
// pictures are of the delivered file itself — the runner converts the stored
// file to PDF with the same converter that makes the PDFs and rasterises its
// pages — never a re-render from the data, or they would stop being evidence
// of what was delivered. Made on demand, the first time someone asks, and
// kept beside the file in storage; every later look reads what was kept.
//
//	POST /artifacts/{id}/pages      ask for the pictures: ready (kept already),
//	                                pending (a job was queued), or unavailable
//	GET  /artifacts/{id}/pages      the same, without queueing anything
//	GET  /artifacts/{id}/pages/{n}  page n as a PNG

const (
	KindPages = "pages"
	// PagesMax is how many pages are rendered: the first pages of a document, the slides of a deck.
	PagesMax = 12
	// PagesDPI is the pictures' resolution: readable in a chat, small enough to keep.
	PagesDPI = 110
)

// PagesManifest is what the runner keeps beside the pictures.
type PagesManifest struct {
	Pages      int       `json:"pages"`
	Available  int       `json:"available"`
	DPI        int       `json:"dpi"`
	SHA256     string    `json:"sha256"`
	RenderedAt time.Time `json:"rendered_at"`
}

// PageKey is where page n's picture of an artifact lives; PagesManifestKey the manifest's.
func PageKey(artifactKey string, n int) string { return artifactKey + ".pages/" + itoa(n) + ".png" }

func PagesManifestKey(artifactKey string) string { return artifactKey + ".pages.json" }

func pagesIdem(artifactID uuid.UUID) string { return "pages:" + artifactID.String() }

// pagesManifest reads the manifest the runner kept; found=false when none is there.
func (a *API) pagesManifest(ctx context.Context, art Artifact) (PagesManifest, bool, error) {
	body, _, _, err := a.files.Download(ctx, PagesManifestKey(art.StorageKey))
	if err != nil {
		return PagesManifest{}, false, nil
	}
	defer body.Close()
	var m PagesManifest
	if err := json.NewDecoder(io.LimitReader(body, 1<<16)).Decode(&m); err != nil {
		return PagesManifest{}, false, nil
	}
	return m, true, nil
}

func (a *API) ownedArtifact(w http.ResponseWriter, r *http.Request) (Artifact, bool) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return Artifact{}, false
	}
	art, err := a.store.GetArtifact(r.Context(), c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "artifact")
		return Artifact{}, false
	}
	return art, true
}

// pagesAnswer is what both GET and POST say.
func (a *API) pagesAnswer(ctx context.Context, art Artifact, queue bool) (map[string]any, error) {
	if m, ok, _ := a.pagesManifest(ctx, art); ok {
		return map[string]any{"status": "ready", "pages": m.Pages, "available": m.Available, "dpi": m.DPI}, nil
	}
	job, err := a.store.RunByIdempotency(ctx, art.InstallID, pagesIdem(art.ID))
	if err != nil && !errors.Is(err, ErrNotFound) {
		return nil, err
	}
	if err == nil {
		switch job.Status {
		case RunFailed:
			return map[string]any{"status": "unavailable", "reason": job.StatusReason}, nil
		case RunSucceeded:
			// finished but nothing kept: the file could not be rendered
			return map[string]any{"status": "unavailable", "reason": "no pictures were made"}, nil
		default:
			return map[string]any{"status": "pending", "job": job.ID}, nil
		}
	}
	if !queue {
		return map[string]any{"status": "none"}, nil
	}
	if !a.runnerOffers(ctx, "deliverable.pdf") {
		return map[string]any{"status": "unavailable", "reason": "no document converter"}, nil
	}
	parent, err := a.store.RunByID(ctx, art.RunID)
	if err != nil {
		return nil, err
	}
	params, _ := json.Marshal(map[string]any{"artifact_id": art.ID})
	pid := art.RunID
	job, _, err = a.store.CreateRun(ctx, a.store.Pool, Run{InstallID: art.InstallID, ParentRunID: &pid, TenantID: parent.TenantID,
		UserID: parent.UserID, Kind: KindPages, Params: params, DefinitionVersion: parent.DefinitionVersion, Checksum: parent.Checksum,
		EngineChecksum: parent.EngineChecksum, BindingVersion: parent.BindingVersion}, pagesIdem(art.ID))
	if err != nil {
		return nil, err
	}
	return map[string]any{"status": "pending", "job": job.ID}, nil
}

func (a *API) requestPages(w http.ResponseWriter, r *http.Request) {
	art, ok := a.ownedArtifact(w, r)
	if !ok {
		return
	}
	out, err := a.pagesAnswer(r.Context(), art, true)
	if err != nil {
		a.fail(w, err, "pages")
		return
	}
	writeJSON(w, http.StatusOK, out)
}

func (a *API) getPages(w http.ResponseWriter, r *http.Request) {
	art, ok := a.ownedArtifact(w, r)
	if !ok {
		return
	}
	out, err := a.pagesAnswer(r.Context(), art, false)
	if err != nil {
		a.fail(w, err, "pages")
		return
	}
	writeJSON(w, http.StatusOK, out)
}

func (a *API) getPage(w http.ResponseWriter, r *http.Request) {
	art, ok := a.ownedArtifact(w, r)
	if !ok {
		return
	}
	n, err := strconv.Atoi(chi.URLParam(r, "n"))
	if err != nil || n < 1 || n > PagesMax {
		badRequest(w, "BAD_PAGE")
		return
	}
	body, size, _, err := a.files.Download(r.Context(), PageKey(art.StorageKey, n))
	if err != nil {
		say(w, http.StatusNotFound, "NOT_FOUND")
		return
	}
	defer body.Close()
	w.Header().Set("Content-Type", "image/png")
	w.Header().Set("Cache-Control", "private, max-age=3600")
	if size > 0 {
		w.Header().Set("Content-Length", fmt.Sprint(size))
	}
	_, _ = io.Copy(w, body)
}
