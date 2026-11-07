// AccountExportService — FIX132 C: "export all my data".
//
// One ZIP the user can open, built in the background:
//
//	README.txt          what is inside, what is not, and the caps
//	chat.html           every chat, readable in any browser (UTF-8, Thai-safe)
//	conversations.json  the same chats as data
//	memories.json       saved memories (not the deleted ones)
//	projects.json       projects: name, instructions, which chats
//	settings.json       custom instructions and skills
//	files/              the uploaded and generated files, bytes
//
// Messages are the ones the chat shows (the active branch of an edited
// conversation) in the form the chat shows them: the stored display
// text, or — when the DLP display lifecycle is on — the same owner
// representation the chat's reload uses (SetDisplayResolver). Only the
// caller's own rows are read; every query is scoped to (tenant, user).
//
// The row in chat.data_export_requests walks pending → processing →
// completed | failed. A completed file can be downloaded for 24 hours
// (exportExpiryWindow); ExportService.SweepExpired then removes it.
package service

import (
	"archive/zip"
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

var (
	// ErrExportDailyLimit — the user already asked for accountExportsPerDay
	// exports in the last 24 hours.
	ErrExportDailyLimit = errors.New("export: daily limit reached")
)

const (
	accountExportsPerDay  = 5
	accountExportTimeout  = 30 * time.Minute
	accountExportPageSize = 100
	// readmeSkippedListCap bounds the "not included" file list in README.
	readmeSkippedListCap = 500
)

// objectReader is the slice of the storage backend the export needs.
type objectReader interface {
	Download(ctx context.Context, key string) (io.ReadCloser, int64, string, error)
}

// DisplayResolver returns the text the chat shows the owner for m.
type DisplayResolver func(ctx context.Context, m model.Message, userID, tenantID uuid.UUID) string

type AccountExportService struct {
	convs    *repository.ConversationRepo
	msgs     *repository.MessageRepo
	files    *repository.FileRepo
	memories *repository.MemoryRepo
	projects *repository.ProjectRepo
	prefs    *repository.PreferencesRepo
	skills   *repository.SkillRepo
	exports  *repository.DataExportRepo
	settings *repository.TenantMemorySettingsRepo
	store    objectReader
	root     string

	display      DisplayResolver
	maxFileBytes int64
	maxConvs     int
	now          func() time.Time
	wg           sync.WaitGroup
}

// AccountExportSources bundles the repositories the export reads.
type AccountExportSources struct {
	Conversations *repository.ConversationRepo
	Messages      *repository.MessageRepo
	Files         *repository.FileRepo
	Memories      *repository.MemoryRepo
	Projects      *repository.ProjectRepo
	Preferences   *repository.PreferencesRepo
	Skills        *repository.SkillRepo
	Exports       *repository.DataExportRepo
	Settings      *repository.TenantMemorySettingsRepo
}

func NewAccountExportService(src AccountExportSources, store objectReader, storageRoot string, maxFileBytes int64, maxConvs int) *AccountExportService {
	if storageRoot == "" {
		storageRoot = "./data"
	}
	if maxFileBytes <= 0 {
		maxFileBytes = 500 << 20
	}
	if maxConvs <= 0 {
		maxConvs = 5000
	}
	return &AccountExportService{
		convs: src.Conversations, msgs: src.Messages, files: src.Files,
		memories: src.Memories, projects: src.Projects, prefs: src.Preferences,
		skills: src.Skills, exports: src.Exports, settings: src.Settings,
		store: store, root: storageRoot,
		display: func(_ context.Context, m model.Message, _, _ uuid.UUID) string {
			return m.ContentMasked
		},
		maxFileBytes: maxFileBytes, maxConvs: maxConvs, now: time.Now,
	}
}

// SetDisplayResolver makes the export read messages the way the chat's
// reload does (DLP display lifecycle).
func (s *AccountExportService) SetDisplayResolver(d DisplayResolver) {
	if d != nil {
		s.display = d
	}
}

// Limits reports the caps, for the README and the Settings page.
func (s *AccountExportService) Limits() (maxFileBytes int64, maxConvs int) {
	return s.maxFileBytes, s.maxConvs
}

// Start queues a full export for the caller and builds it in the
// background. An export already pending or building is returned as is.
func (s *AccountExportService) Start(ctx context.Context, userID, tenantID uuid.UUID) (*model.DataExportRequest, error) {
	settings, err := s.settings.Get(ctx, tenantID)
	if err != nil {
		return nil, err
	}
	if !settings.AllowDataExport {
		return nil, ErrExportDisabled
	}
	recent, err := s.exports.ListByUserAndType(ctx, userID, tenantID, model.ExportTypeFullAccount, 5)
	if err != nil {
		return nil, err
	}
	for i := range recent {
		if recent[i].Status == model.ExportStatusPending || recent[i].Status == model.ExportStatusProcessing {
			return &recent[i], nil
		}
	}
	n, err := s.exports.CountRequestedSince(ctx, userID, model.ExportTypeFullAccount, s.now().Add(-24*time.Hour))
	if err != nil {
		return nil, err
	}
	if n >= accountExportsPerDay {
		return nil, ErrExportDailyLimit
	}
	row := &model.DataExportRequest{
		TenantID: tenantID, UserID: userID,
		ExportType: model.ExportTypeFullAccount, Status: model.ExportStatusPending,
	}
	if err := s.exports.Create(ctx, row); err != nil {
		return nil, fmt.Errorf("create export: %w", err)
	}
	s.wg.Add(1)
	go func(r model.DataExportRequest) {
		defer s.wg.Done()
		bctx, cancel := context.WithTimeout(context.Background(), accountExportTimeout)
		defer cancel()
		if err := s.Build(bctx, &r); err != nil {
			slog.Warn("account export failed", "export", r.ID.String(), "err", err)
		}
	}(*row)
	return row, nil
}

// Wait blocks until every background build started so far has finished
// (tests; shutdown).
func (s *AccountExportService) Wait() { s.wg.Wait() }

// List returns the caller's newest full exports.
func (s *AccountExportService) List(ctx context.Context, userID, tenantID uuid.UUID) ([]model.DataExportRequest, error) {
	return s.exports.ListByUserAndType(ctx, userID, tenantID, model.ExportTypeFullAccount, 5)
}

// ---------------------------------------------------------------------------
// The build
// ---------------------------------------------------------------------------

type exportFileJSON struct {
	ID          string `json:"id"`
	Filename    string `json:"filename"`
	ContentType string `json:"content_type"`
	SizeBytes   int64  `json:"size_bytes"`
	Generated   bool   `json:"generated"`
	// PathInZip is where the bytes are inside the ZIP; empty when the file
	// was not included (the README says why).
	PathInZip string `json:"path_in_zip,omitempty"`
}

type exportMessageJSON struct {
	ID        string           `json:"id"`
	Role      string           `json:"role"`
	Content   string           `json:"content"`
	CreatedAt string           `json:"created_at"`
	ModelID   *string          `json:"model_id,omitempty"`
	Status    string           `json:"status,omitempty"`
	Files     []exportFileJSON `json:"files,omitempty"`
}

type exportConversationJSON struct {
	ID          string              `json:"id"`
	Title       string              `json:"title"`
	CreatedAt   string              `json:"created_at"`
	UpdatedAt   string              `json:"updated_at"`
	ModelID     string              `json:"model_id"`
	Archived    bool                `json:"archived,omitempty"`
	Pinned      bool                `json:"pinned,omitempty"`
	Temporary   bool                `json:"temporary,omitempty"`
	ProjectID   string              `json:"project_id,omitempty"`
	ProjectName string              `json:"project_name,omitempty"`
	Messages    []exportMessageJSON `json:"messages"`
	// Files uploaded into the chat but never sent with a message.
	UnsentFiles []exportFileJSON `json:"unsent_files,omitempty"`
}

type plannedFile struct {
	key, zipPath string
}

type buildState struct {
	planned       []plannedFile
	skipped       []string
	fileBytes     int64
	conversations int
	messages      int
	truncated     bool
	provided      []string // README lines from AccountDataProviders
}

// Build writes the ZIP for row and marks it completed; on any error the
// row is marked failed and the partial file removed.
func (s *AccountExportService) Build(ctx context.Context, row *model.DataExportRequest) (err error) {
	if err := s.exports.SetStatus(ctx, row.ID, model.ExportStatusProcessing); err != nil {
		return err
	}
	dir := filepath.Join(s.root, "exports", row.TenantID.String(), row.UserID.String())
	defer func() {
		if err != nil {
			_ = s.exports.SetStatus(context.WithoutCancel(ctx), row.ID, model.ExportStatusFailed)
		}
	}()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return fmt.Errorf("export dir: %w", err)
	}
	work, err := os.MkdirTemp(dir, ".build-")
	if err != nil {
		return fmt.Errorf("export work dir: %w", err)
	}
	defer os.RemoveAll(work)

	now := s.now().UTC()
	st := &buildState{}
	projectNames, projectsJSON, err := s.projectsDoc(ctx, row)
	if err != nil {
		return err
	}
	convJSONPath := filepath.Join(work, "conversations.json")
	htmlPath := filepath.Join(work, "chat.html")
	if err := s.writeConversations(ctx, row, projectNames, convJSONPath, htmlPath, now, st); err != nil {
		return err
	}
	memoriesJSON, err := s.memoriesDoc(ctx, row)
	if err != nil {
		return err
	}
	settingsJSON, err := s.settingsDoc(ctx, row)
	if err != nil {
		return err
	}

	name := fmt.Sprintf("sranchat-export-%s.zip", now.Format("20060102-150405"))
	final := filepath.Join(dir, name)
	tmp := filepath.Join(work, name)
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return fmt.Errorf("create zip: %w", err)
	}
	zw := zip.NewWriter(out)
	closeAll := func() error {
		if err := zw.Close(); err != nil {
			_ = out.Close()
			return err
		}
		return out.Close()
	}

	// Files first so the README can report the ones that could not be read.
	for _, p := range st.planned {
		if err := ctx.Err(); err != nil {
			_ = closeAll()
			return err
		}
		body, _, _, derr := s.store.Download(ctx, p.key)
		if derr != nil {
			st.skipped = append(st.skipped, p.zipPath+" — could not be read from storage")
			continue
		}
		w, werr := zw.CreateHeader(&zip.FileHeader{Name: p.zipPath, Method: zip.Deflate, Modified: now})
		if werr != nil {
			body.Close()
			_ = closeAll()
			return werr
		}
		if _, cerr := io.Copy(w, body); cerr != nil {
			body.Close()
			_ = closeAll()
			return fmt.Errorf("copy %s: %w", p.zipPath, cerr)
		}
		body.Close()
	}
	// Features that keep data outside the core schemas add their own folder.
	for _, p := range AccountDataProviders() {
		lines, perr := p.Export(ctx, row.TenantID, row.UserID, func(path string, r io.Reader) error {
			w, err := zw.CreateHeader(&zip.FileHeader{Name: safeZipName(p.Name()) + "/" + path, Method: zip.Deflate, Modified: now})
			if err == nil {
				_, err = io.Copy(w, r)
			}
			return err
		})
		if perr != nil {
			st.skipped = append(st.skipped, p.Name()+"/ — "+perr.Error())
		}
		st.provided = append(st.provided, lines...)
	}
	for _, e := range []struct {
		name string
		path string
		data []byte
	}{
		{"README.txt", "", s.readme(now, st)},
		{"chat.html", htmlPath, nil},
		{"conversations.json", convJSONPath, nil},
		{"memories.json", "", memoriesJSON},
		{"projects.json", "", projectsJSON},
		{"settings.json", "", settingsJSON},
	} {
		w, werr := zw.CreateHeader(&zip.FileHeader{Name: e.name, Method: zip.Deflate, Modified: now})
		if werr != nil {
			_ = closeAll()
			return werr
		}
		if e.path != "" {
			f, oerr := os.Open(e.path)
			if oerr != nil {
				_ = closeAll()
				return oerr
			}
			_, cerr := io.Copy(w, f)
			f.Close()
			if cerr != nil {
				_ = closeAll()
				return cerr
			}
		} else if _, werr := w.Write(e.data); werr != nil {
			_ = closeAll()
			return werr
		}
	}
	if err := closeAll(); err != nil {
		return fmt.Errorf("finish zip: %w", err)
	}
	if err := os.Rename(tmp, final); err != nil {
		return fmt.Errorf("place zip: %w", err)
	}
	info, err := os.Stat(final)
	if err != nil {
		return err
	}
	if err := s.exports.Complete(ctx, row.ID, final, info.Size(), s.now().UTC().Add(exportExpiryWindow)); err != nil {
		_ = os.Remove(final)
		return err
	}
	slog.Info("account export ready", "export", row.ID.String(),
		"conversations", st.conversations, "messages", st.messages,
		"files", len(st.planned), "skipped", len(st.skipped), "bytes", info.Size())
	return nil
}

// listConversations returns the caller's live chats (sidebar and archive),
// newest first, capped at maxConvs.
func (s *AccountExportService) listConversations(ctx context.Context, row *model.DataExportRequest, st *buildState) ([]model.Conversation, error) {
	var out []model.Conversation
	for _, archived := range []bool{false, true} {
		cursor := uuid.Nil
		for {
			page, next, err := s.convs.ListByUser(ctx, row.TenantID, row.UserID, accountExportPageSize, cursor, archived)
			if err != nil {
				return nil, fmt.Errorf("list conversations: %w", err)
			}
			out = append(out, page...)
			if len(out) >= s.maxConvs {
				st.truncated = true
				return out[:s.maxConvs], nil
			}
			if next == uuid.Nil {
				break
			}
			cursor = next
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].UpdatedAt.After(out[j].UpdatedAt) })
	return out, nil
}

// allMessages pages backwards through the active branch; ascending.
func (s *AccountExportService) allMessages(ctx context.Context, tenantID, convID uuid.UUID) ([]model.Message, error) {
	var all []model.Message
	cursor := uuid.Nil
	for {
		page, more, err := s.msgs.ListByConversation(ctx, tenantID, convID, accountExportPageSize, cursor)
		if err != nil {
			return nil, err
		}
		all = append(page, all...)
		if !more || len(page) == 0 {
			return all, nil
		}
		cursor = page[0].ID
	}
}

func (s *AccountExportService) writeConversations(
	ctx context.Context, row *model.DataExportRequest, projectNames map[uuid.UUID]string,
	jsonPath, htmlPath string, now time.Time, st *buildState,
) error {
	convs, err := s.listConversations(ctx, row, st)
	if err != nil {
		return err
	}
	jf, err := os.Create(jsonPath)
	if err != nil {
		return err
	}
	defer jf.Close()
	hf, err := os.Create(htmlPath)
	if err != nil {
		return err
	}
	defer hf.Close()
	jw := bufio.NewWriter(jf)
	hw := bufio.NewWriter(hf)

	writeHTMLHead(hw, now, len(convs))
	// Table of contents.
	hw.WriteString("<nav><h2>บทสนทนา / Conversations</h2><ol>")
	for i, c := range convs {
		fmt.Fprintf(hw, `<li><a href="#c%d">%s</a> <span class="meta">%s</span></li>`,
			i, html.EscapeString(titleOr(c.Title)), c.UpdatedAt.UTC().Format("2006-01-02"))
	}
	hw.WriteString("</ol></nav>\n")

	jw.WriteString("[\n")
	for i, c := range convs {
		if err := ctx.Err(); err != nil {
			return err
		}
		msgs, err := s.allMessages(ctx, row.TenantID, c.ID)
		if err != nil {
			return fmt.Errorf("messages of %s: %w", c.ID, err)
		}
		files, err := s.files.ListByConversation(ctx, row.TenantID, c.ID)
		if err != nil {
			return fmt.Errorf("files of %s: %w", c.ID, err)
		}
		folder := fmt.Sprintf("files/%s_%s", c.CreatedAt.UTC().Format("2006-01-02"), c.ID.String()[:8])
		byMsg := map[uuid.UUID][]exportFileJSON{}
		var unsent []exportFileJSON
		for _, f := range files {
			fj := exportFileJSON{
				ID: f.ID.String(), Filename: f.OriginalFilename, ContentType: f.ContentType,
				SizeBytes: f.FileSizeBytes, Generated: f.IsGenerated,
			}
			zipPath := folder + "/" + f.ID.String()[:8] + "_" + safeZipName(f.OriginalFilename)
			switch {
			case f.StoragePath == "":
				st.skipped = append(st.skipped, zipPath+" — no stored bytes")
			case st.fileBytes+f.FileSizeBytes > s.maxFileBytes:
				st.skipped = append(st.skipped, zipPath+" — over the size cap")
			default:
				st.fileBytes += f.FileSizeBytes
				st.planned = append(st.planned, plannedFile{key: f.StoragePath, zipPath: zipPath})
				fj.PathInZip = zipPath
			}
			if f.MessageID != nil {
				byMsg[*f.MessageID] = append(byMsg[*f.MessageID], fj)
			} else {
				unsent = append(unsent, fj)
			}
		}

		cj := exportConversationJSON{
			ID: c.ID.String(), Title: c.Title, ModelID: c.ModelID,
			CreatedAt: c.CreatedAt.UTC().Format(time.RFC3339),
			UpdatedAt: c.UpdatedAt.UTC().Format(time.RFC3339),
			Archived:  c.IsArchived, Pinned: c.IsPinned, Temporary: c.IsTemporary,
			Messages: make([]exportMessageJSON, 0, len(msgs)), UnsentFiles: unsent,
		}
		if c.ProjectID != nil {
			cj.ProjectID = c.ProjectID.String()
			cj.ProjectName = projectNames[*c.ProjectID]
		}
		fmt.Fprintf(hw, `<section id="c%d"><h2>%s</h2><p class="meta">%s · %s`,
			i, html.EscapeString(titleOr(c.Title)),
			c.CreatedAt.UTC().Format("2006-01-02 15:04 UTC"), html.EscapeString(c.ModelID))
		if cj.ProjectName != "" {
			fmt.Fprintf(hw, ` · โปรเจกต์ %s`, html.EscapeString(cj.ProjectName))
		}
		hw.WriteString("</p>\n")
		for _, m := range msgs {
			content := s.display(ctx, m, row.UserID, row.TenantID)
			mj := exportMessageJSON{
				ID: m.ID.String(), Role: m.Role, Content: content,
				CreatedAt: m.CreatedAt.UTC().Format(time.RFC3339),
				ModelID:   m.ModelID, Status: m.Status, Files: byMsg[m.ID],
			}
			cj.Messages = append(cj.Messages, mj)
			st.messages++
			who := "คุณ / You"
			cls := "user"
			if m.Role == "assistant" {
				who = "ผู้ช่วย / Assistant"
				if m.ModelID != nil && *m.ModelID != "" {
					who += " (" + *m.ModelID + ")"
				}
				cls = "assistant"
			}
			fmt.Fprintf(hw, `<div class="msg %s"><div class="who">%s <span class="meta">%s</span></div><div class="body">%s</div>`,
				cls, html.EscapeString(who), m.CreatedAt.UTC().Format("2006-01-02 15:04"), html.EscapeString(content))
			if len(mj.Files) > 0 {
				hw.WriteString(`<ul class="files">`)
				for _, f := range mj.Files {
					writeHTMLFile(hw, f)
				}
				hw.WriteString("</ul>")
			}
			hw.WriteString("</div>\n")
		}
		if len(unsent) > 0 {
			hw.WriteString(`<p class="meta">ไฟล์ที่อัปโหลดแต่ไม่ได้ส่ง / Uploaded, never sent:</p><ul class="files">`)
			for _, f := range unsent {
				writeHTMLFile(hw, f)
			}
			hw.WriteString("</ul>")
		}
		hw.WriteString("</section>\n")

		b, err := json.MarshalIndent(cj, "  ", "  ")
		if err != nil {
			return err
		}
		if i > 0 {
			jw.WriteString(",\n")
		}
		jw.WriteString("  ")
		jw.Write(b)
		st.conversations++
	}
	jw.WriteString("\n]\n")
	hw.WriteString("</body></html>\n")
	if err := jw.Flush(); err != nil {
		return err
	}
	return hw.Flush()
}

func writeHTMLFile(w *bufio.Writer, f exportFileJSON) {
	if f.PathInZip != "" {
		fmt.Fprintf(w, `<li><a href="%s">%s</a></li>`, html.EscapeString(f.PathInZip), html.EscapeString(f.Filename))
	} else {
		fmt.Fprintf(w, `<li>%s <span class="meta">(ไม่ได้รวมไว้ / not included — README.txt)</span></li>`, html.EscapeString(f.Filename))
	}
}

func writeHTMLHead(w *bufio.Writer, now time.Time, n int) {
	w.WriteString(`<!DOCTYPE html>
<html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SRAN Chat — ข้อมูลของคุณ</title>
<style>
body{font-family:"Noto Sans Thai","Leelawadee UI","Tahoma","Sarabun",system-ui,sans-serif;max-width:860px;margin:0 auto;padding:24px 16px;line-height:1.6;color:#1f2328;background:#fff}
h1{font-size:1.5rem}h2{font-size:1.15rem;margin-top:2rem;border-bottom:1px solid #d0d7de;padding-bottom:4px}
.meta{color:#656d76;font-size:.85rem}
.msg{margin:12px 0;padding:10px 12px;border-radius:8px;border:1px solid #d0d7de}
.msg.user{background:#f6f8fa}.msg.assistant{background:#fff}
.who{font-weight:600;margin-bottom:4px}
.body{white-space:pre-wrap;word-wrap:break-word}
.files{margin:6px 0 0 18px}
@media (prefers-color-scheme: dark){body{background:#0d1117;color:#e6edf3}.msg{border-color:#30363d}.msg.user{background:#161b22}.msg.assistant{background:#0d1117}h2{border-color:#30363d}.meta{color:#8b949e}a{color:#58a6ff}}
</style></head><body>
`)
	fmt.Fprintf(w, "<h1>SRAN Chat — ข้อมูลของคุณ / Your data</h1><p class=\"meta\">ส่งออกเมื่อ / Exported %s · %d บทสนทนา / conversations</p>\n",
		now.Format("2006-01-02 15:04 UTC"), n)
}

func (s *AccountExportService) projectsDoc(ctx context.Context, row *model.DataExportRequest) (map[uuid.UUID]string, []byte, error) {
	projects, err := s.projects.ListByUser(ctx, row.TenantID, row.UserID)
	if err != nil {
		return nil, nil, fmt.Errorf("list projects: %w", err)
	}
	names := map[uuid.UUID]string{}
	type pj struct {
		ID           string `json:"id"`
		Name         string `json:"name"`
		Instructions string `json:"instructions,omitempty"`
		CreatedAt    string `json:"created_at"`
		UpdatedAt    string `json:"updated_at"`
		Chats        int    `json:"chat_count"`
	}
	out := make([]pj, 0, len(projects))
	for _, p := range projects {
		names[p.ID] = p.Name
		out = append(out, pj{
			ID: p.ID.String(), Name: p.Name, Instructions: p.Instructions,
			CreatedAt: p.CreatedAt.UTC().Format(time.RFC3339),
			UpdatedAt: p.UpdatedAt.UTC().Format(time.RFC3339),
			Chats:     p.ConversationCount,
		})
	}
	b, err := json.MarshalIndent(map[string]any{
		"projects": out,
		"note":     "Chats belong to a project through project_id in conversations.json.",
	}, "", "  ")
	return names, b, err
}

func (s *AccountExportService) memoriesDoc(ctx context.Context, row *model.DataExportRequest) ([]byte, error) {
	all, err := s.memories.ListAllForExport(ctx, row.UserID, row.TenantID)
	if err != nil {
		return nil, fmt.Errorf("list memories: %w", err)
	}
	kept := make([]model.Memory, 0, len(all))
	for _, m := range all {
		// A deleted memory's text is already scrubbed; it is not the user's
		// data any more.
		if m.Status == model.MemoryStatusDeleted {
			continue
		}
		kept = append(kept, m)
	}
	return json.MarshalIndent(map[string]any{"memories": kept}, "", "  ")
}

func (s *AccountExportService) settingsDoc(ctx context.Context, row *model.DataExportRequest) ([]byte, error) {
	doc := map[string]any{}
	if p, err := s.prefs.Get(ctx, row.TenantID, row.UserID); err == nil {
		doc["custom_instructions"] = map[string]string{
			"about_you":          p.CustomInstructionsAbout,
			"how_to_respond":     p.CustomInstructionsRespond,
			"updated_at_rfc3339": p.UpdatedAt.UTC().Format(time.RFC3339),
		}
	} else if !errors.Is(err, repository.ErrNotFound) {
		return nil, fmt.Errorf("preferences: %w", err)
	}
	skills, err := s.skills.ListByUser(ctx, row.TenantID, row.UserID)
	if err != nil {
		return nil, fmt.Errorf("skills: %w", err)
	}
	doc["skills"] = skills
	return json.MarshalIndent(doc, "", "  ")
}

func (s *AccountExportService) readme(now time.Time, st *buildState) []byte {
	var b strings.Builder
	fmt.Fprintf(&b, "SRAN Chat — ข้อมูลของคุณ / Your data\nส่งออกเมื่อ / Exported: %s\n\n", now.Format("2006-01-02 15:04 UTC"))
	b.WriteString(`มีอะไรในไฟล์นี้
  chat.html           เปิดด้วยเบราว์เซอร์ อ่านบทสนทนาทั้งหมดได้เลย
  conversations.json  บทสนทนาเดียวกันในรูปข้อมูล (ข้อความตามที่แสดงในแชท)
  files/              ไฟล์ที่คุณอัปโหลดและไฟล์ที่ระบบสร้างให้
  memories.json       ความจำที่บันทึกไว้
  projects.json       โปรเจกต์ (ชื่อ คำสั่ง จำนวนแชท)
  settings.json       คำสั่งที่กำหนดเอง (custom instructions) และสกิล

ไม่ได้รวมไว้
  - แชทที่คุณลบแล้ว (จะถูกลบออกจากระบบถาวรภายใน 30 วันหลังจากลบ)
  - ข้อความเวอร์ชันเก่าที่ถูกแก้ไขทับ (มีเฉพาะเวอร์ชันที่แสดงในแชท)
  - คลังความรู้ (knowledge base) ของทีม — เป็นข้อมูลของทีม ไม่ใช่ของบัญชีคุณคนเดียว
  - คะแนน/ความเห็นต่อคำตอบ ลิงก์แชร์ ข้อมูลการใช้งานและเครดิต (ดูได้ที่หน้า Portal)
  - ข้อมูลที่ถูกปกปิดโดยระบบป้องกันข้อมูลรั่วไหล (DLP) จะอยู่ในรูปเดียวกับที่แสดงในแชท

What is inside
  chat.html           open in any browser to read every chat
  conversations.json  the same chats as data (text as the chat shows it)
  files/              files you uploaded and files the assistant made
  memories.json       saved memories
  projects.json       projects (name, instructions, number of chats)
  settings.json       custom instructions and skills

Not included
  - chats you deleted (they are permanently removed within 30 days of deletion)
  - older versions of edited messages (only the version the chat shows)
  - team knowledge bases (they belong to the team, not to your account alone)
  - feedback ratings, share links, usage and credit records (see the Portal)
  - text masked by data-loss prevention stays masked, exactly as the chat shows it
`)
	fmt.Fprintf(&b, "\nขนาดสูงสุด / Caps: ไฟล์รวม / files up to %d MB, บทสนทนา / conversations up to %d\n",
		s.maxFileBytes>>20, s.maxConvs)
	fmt.Fprintf(&b, "บทสนทนา / conversations: %d · ข้อความ / messages: %d · ไฟล์ / files: %d\n",
		st.conversations, st.messages, len(st.planned))
	if len(st.provided) > 0 {
		b.WriteString("\nรวมไว้ด้วย / Also included:\n")
		for _, l := range st.provided {
			b.WriteString("  " + l + "\n")
		}
	}
	if st.truncated {
		fmt.Fprintf(&b, "บทสนทนาเกิน %d รายการ — ไฟล์นี้มีเฉพาะ %d รายการล่าสุด / only the newest %d conversations are included\n",
			s.maxConvs, s.maxConvs, s.maxConvs)
	}
	if len(st.skipped) > 0 {
		b.WriteString("\nไฟล์ที่ไม่ได้รวมไว้ / Files not included:\n")
		for i, n := range st.skipped {
			if i >= readmeSkippedListCap {
				fmt.Fprintf(&b, "  ... และอีก %d ไฟล์ / and %d more\n", len(st.skipped)-i, len(st.skipped)-i)
				break
			}
			b.WriteString("  " + n + "\n")
		}
	}
	b.WriteString("\nลิงก์ดาวน์โหลดใช้ได้ 24 ชั่วโมง / The download link works for 24 hours.\n")
	// CRLF so Notepad on an older Windows shows the lines too.
	return []byte(strings.ReplaceAll(b.String(), "\n", "\r\n"))
}

func titleOr(t string) string {
	if strings.TrimSpace(t) == "" {
		return "(ไม่มีชื่อ / untitled)"
	}
	return t
}

// safeZipName keeps a file's own name (Thai included) but drops anything a
// ZIP tool or file system would read as a path or refuse.
func safeZipName(name string) string {
	var b strings.Builder
	for _, r := range name {
		switch {
		case r == '/' || r == '\\' || r == ':' || r == '*' || r == '?' || r == '"' || r == '<' || r == '>' || r == '|':
			b.WriteRune('_')
		case unicode.IsControl(r):
		default:
			b.WriteRune(r)
		}
	}
	out := strings.Trim(b.String(), " .")
	if out == "" {
		out = "file"
	}
	for utf8.RuneCountInString(out) > 120 {
		r := []rune(out)
		out = string(r[len(r)-120:])
	}
	return out
}
