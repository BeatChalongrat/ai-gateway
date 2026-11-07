package runner

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── AM2: reading documents ───────────────────────────────────────────────
//
// Two things an extraction agent (Invoice Processing) needs beyond AM1's
// steps, both handed to the package's preflight and compute as small JSON
// inputs, and both part of those steps' idempotency keys:
//
//   history.json    the install's duplicate index (migration 002): the
//                   documents earlier runs recorded. Written by the runner
//                   only when a run succeeds, never for a self-test.
//   proposals.json  a vision model's reading of each scan or photo. The
//                   package treats it as a candidate only — it can raise
//                   the confidence of a value OCR also read, or become a
//                   question's proposal; it never becomes a number itself.
//
// The vision step sends page images to one model only: the one the
// operator named (AGENT_MARKET_VISION_MODEL), and only when it is in the
// self-hosted list — a customer's documents never go to an outside
// provider, and there is no fallback model for them. A failed reading
// never fails the run: the questions are still asked.

const maxHistory = 5000

// visionFields are the keys a vision reading may carry (engine/e2 field
// names); anything else the model adds is dropped.
var visionFields = map[string]bool{
	"document_type": true, "seller_name": true, "seller_tax_id": true, "seller_branch": true, "buyer_name": true,
	"buyer_tax_id": true, "document_number": true, "document_date": true, "subtotal": true, "vat_amount": true,
	"total": true, "wht_amount": true, "net_payable": true, "currency": true,
}

var moneyField = map[string]bool{"subtotal": true, "vat_amount": true, "total": true, "wht_amount": true, "net_payable": true}

type visionReading struct {
	DocKey     string            `json:"doc_key"`
	Model      string            `json:"model"`
	SelfHosted bool              `json:"self_hosted"`
	Fields     map[string]string `json:"fields,omitempty"`
	Error      string            `json:"error,omitempty"`
	Pages      int               `json:"pages"`
	Tokens     int               `json:"tokens"`
	CostUSD    float64           `json:"cost_usd"`
}

type runExtras struct {
	History   []am.IndexEntry          // nil: the package keeps no index (or a self-test)
	Proposals map[string]visionReading // doc_key → reading
	// Web (AM12): a round on the web — what a model understood of the request and, once the search is
	// done, every page captured and what a model says it read there (web.go). It takes the place of
	// Proposals for such a package: both are a model's readings, and neither is ever a value by itself.
	Web *webResearch
}

// keyParts is what the extras add to a step's idempotency key: nothing for
// a package that uses neither, so AM1's keys (and parked runs) are unchanged.
func (x runExtras) keyParts() []any {
	if x.Web != nil {
		return []any{map[string]any{"web": x.Web.key()}}
	}
	if x.History == nil && len(x.Proposals) == 0 {
		return nil
	}
	return []any{map[string]any{"history": x.History, "proposals": x.Proposals}}
}

// attach writes the extras as step inputs and names them in the request.
func (x runExtras) attach(req map[string]any, inputs []Input) []Input {
	if x.History != nil {
		b, _ := json.Marshal(x.History)
		inputs = append(inputs, Input{Name: "history.json", Bytes: b})
		req["history"] = "history.json"
	}
	if x.Web != nil {
		return x.Web.attach(req, inputs)
	}
	if len(x.Proposals) > 0 {
		b, _ := json.Marshal(x.Proposals)
		inputs = append(inputs, Input{Name: "proposals.json", Bytes: b})
		req["proposals"] = "proposals.json"
	}
	return inputs
}

func (rc *runCtx) extras(ctx context.Context, parsed []parsedFile) (runExtras, error) {
	var x runExtras
	if rc.pkg.Manifest.IndexRetention() > 0 && rc.run.Kind != am.KindSelfTest {
		h, err := rc.r.Store.DocumentIndex(ctx, rc.install.ID, rc.run.ID, maxHistory)
		if err != nil {
			return x, err
		}
		x.History = h
	}
	p, err := rc.readDocuments(ctx, parsed)
	x.Proposals = p
	return x, err
}

// readDocuments has the round's vision model (AM4: the person's choice, as
// the organisation allows — choice.go) read each scan or photo as a second
// reader; never the source of a number, never a fallback model.
func (rc *runCtx) readDocuments(ctx context.Context, parsed []parsedFile) (map[string]visionReading, error) {
	model, _ := rc.visionModel()
	if model == "" {
		return nil, nil
	}
	self := rc.r.Model.IsSelfHosted(model)
	prompt, err := rc.engine.PackageFile(rc.pkg, "prompts/vision.md")
	if err != nil {
		return nil, nil // a package without a vision prompt reads no images
	}
	ph := sha256.Sum256(prompt)
	out := map[string]visionReading{}
	for _, p := range parsed {
		if len(p.Vision) == 0 {
			continue
		}
		docKey, _ := p.Summary["doc_key"].(string)
		pages := p.Vision
		key := am.StepKey(rc.run.ID, "vision", p.Key, model, hex.EncodeToString(ph[:]))
		raw, err := rc.step(ctx, "vision:"+p.File.Name, "ai", key, map[string]any{"model": model, "pages": len(pages)}, true,
			func(ctx context.Context) (any, float64, string, string, error) {
				var images [][]byte
				for _, ref := range pages {
					b, err := rc.download(ctx, ref.Key)
					if err != nil {
						return nil, 0, "", "", err
					}
					images = append(images, b)
				}
				reading := visionReading{DocKey: docKey, Model: model, SelfHosted: self, Pages: len(images)}
				var fields map[string]string
				c, err := rc.r.Model.Vision(ctx, model, string(prompt), images, func(s string) error {
					f, err := visionJSON(s)
					if err != nil {
						return err
					}
					fields = f
					return nil
				})
				reading.Tokens, reading.CostUSD = c.Tokens, c.CostUSD
				credits := c.CostUSD * rc.r.CreditsPerUSD
				if errors.Is(err, ErrModelUnreachable) && ctx.Err() == nil {
					// AM15: the model that reads the scan cannot be reached. Until now the round went on
					// from OCR alone and this step still named the model: the run waits for it instead.
					return nil, credits, "", model, err
				}
				if err != nil {
					reading.Model, reading.Error = "", trim(err.Error(), 300)
					return reading, credits, "", model, nil
				}
				reading.Fields = fields
				_ = rc.r.Store.SetRunFields(ctx, rc.run.ID, nil, &model, &self)
				return reading, credits, "", model, nil
			})
		if err != nil {
			return out, err
		}
		var v visionReading
		if err := json.Unmarshal(raw, &v); err == nil && v.Model != "" && len(v.Fields) > 0 && docKey != "" {
			out[docKey] = v
		}
	}
	return out, nil
}

// visionJSON keeps the known keys of the model's JSON object, as strings.
func visionJSON(s string) (map[string]string, error) {
	var obj map[string]any
	if err := json.Unmarshal([]byte(extractJSON(s)), &obj); err != nil {
		return nil, fmt.Errorf("ไม่ใช่ JSON object")
	}
	out := map[string]string{}
	for k, v := range obj {
		if !visionFields[k] || v == nil {
			continue
		}
		switch t := v.(type) {
		case string:
			if t = strings.TrimSpace(t); t != "" && t != "null" {
				out[k] = t
			}
		case float64:
			if moneyField[k] {
				out[k] = fmt.Sprintf("%.2f", t)
			} else {
				out[k] = strconv.FormatFloat(t, 'f', -1, 64)
			}
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("ไม่มีค่าที่อ่านได้")
	}
	return out, nil
}

func (rc *runCtx) download(ctx context.Context, key string) ([]byte, error) {
	body, _, _, err := rc.r.Files.Download(ctx, key)
	if err != nil {
		return nil, err
	}
	defer body.Close()
	return io.ReadAll(io.LimitReader(body, 16<<20))
}

// recordIndex keeps a succeeded run's documents in the install's duplicate
// index (never a self-test's). Idempotent: a resumed run writes nothing twice.
// AM6: a round that computes again replaces what it recorded (docindex.go).
func (rc *runCtx) recordIndex(ctx context.Context, computed blobRef) error {
	retention := rc.pkg.Manifest.IndexRetention()
	if retention <= 0 || rc.run.Kind == am.KindSelfTest {
		return nil
	}
	b, err := rc.download(ctx, computed.Key)
	if err != nil {
		return err
	}
	var c struct {
		IndexEntries []am.IndexEntry `json:"index_entries"`
	}
	if err := json.Unmarshal(b, &c); err != nil {
		return err
	}
	n, err := rc.r.Store.AddDocumentIndex(ctx, rc.run, c.IndexEntries, retention)
	if err == nil && n > 0 {
		rc.log.Info("duplicate index updated", "documents", n)
	}
	return err
}

// recordExceptions stores what the package left for a person to decide
// (computed.json run_exceptions → agent_run_exceptions, AM2): the expense
// audit's over-limit, missing-receipt and duplicate claims. Never a
// rejection — each row stays open until a person picks one of its choices.
func (rc *runCtx) recordExceptions(ctx context.Context, computed blobRef) error {
	b, err := rc.download(ctx, computed.Key)
	if err != nil {
		return err
	}
	var c struct {
		RunExceptions []am.ExceptionIn `json:"run_exceptions"`
	}
	if err := json.Unmarshal(b, &c); err != nil || len(c.RunExceptions) == 0 {
		return nil
	}
	n, err := rc.r.Store.ReplaceRunExceptions(ctx, rc.run.ID, c.RunExceptions)
	if err == nil {
		rc.log.Info("run exceptions stored", "open", n)
	}
	return err
}

// visionPages uploads the page images a parse step wrote for the vision step.
func (rc *runCtx) visionPages(ctx context.Context, res *StepResult, stepKey string, summary map[string]any) ([]blobRef, error) {
	names, _ := summary["vision_pages"].([]any)
	var refs []blobRef
	var sorted []string
	for _, n := range names {
		if s, ok := n.(string); ok && strings.HasPrefix(s, "vision_") && strings.HasSuffix(s, ".jpg") {
			sorted = append(sorted, s)
		}
	}
	sort.Strings(sorted)
	for _, name := range sorted {
		data, err := readArtifact(res, name)
		if err != nil {
			return nil, err
		}
		key := am.BlobKey(rc.run.TenantID, rc.run.ID, stepKey, name)
		if err := rc.r.Files.Upload(ctx, key, strings.NewReader(string(data)), "image/jpeg", int64(len(data))); err != nil {
			return nil, err
		}
		sum := sha256.Sum256(data)
		refs = append(refs, blobRef{Key: key, SHA256: hex.EncodeToString(sum[:]), Size: int64(len(data))})
	}
	return refs, nil
}
