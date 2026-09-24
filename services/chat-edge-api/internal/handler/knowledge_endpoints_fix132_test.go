package handler

// FIX132 B — the contract's shapes: KB list with indexing counts, documents
// with status / progress / error_code / indexed_at, upload and re-index 202,
// and GET …/documents/{docId}/file serving the original bytes inline.

import (
	"context"
	"net/http"
	"net/url"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestFIX132_ListShapesCarryIndexingAndProgress(t *testing.T) {
	k := newKBHTTP(t)
	user := uuid.New()
	kbID := k.createKB(t, user, "HR")
	up := k.upload(t, user, kbID, "leave.txt", "text/plain", "Annual leave policy. Employees get ten days of annual leave each year.")
	if up.Code != http.StatusAccepted {
		t.Fatalf("upload answers 202, got %d", up.Code)
	}
	var created map[string]any
	decode(t, up, &created)
	for _, key := range []string{"id", "knowledge_base_id", "original_filename", "filename", "content_type", "file_size_bytes",
		"status", "progress_done", "progress_total", "chunk_count", "char_count", "error_code", "error_message", "created_at", "updated_at", "indexed_at"} {
		if _, ok := created[key]; !ok {
			t.Errorf("document field %q missing: %s", key, up.Body.String())
		}
	}
	if created["status"] != "pending" || created["original_filename"] != "leave.txt" {
		t.Fatalf("the upload answers the pending row: %s", up.Body.String())
	}
	k.drain(t) // leave.txt indexed
	k.upload(t, user, kbID, "empty.txt", "text/plain", "   \n  ")
	k.drain(t) // empty.txt failed / empty_text
	k.upload(t, user, kbID, "later.txt", "text/plain", "Travel rules.") // stays pending

	var list struct {
		KBs []struct {
			ID       string         `json:"id"`
			Indexing map[string]int `json:"indexing"`
			Docs     int            `json:"document_count"`
			Chunks   int            `json:"total_chunks"`
		} `json:"knowledge_bases"`
	}
	decode(t, k.do(t, user, http.MethodGet, "/knowledge-bases", nil), &list)
	if len(list.KBs) != 1 || list.KBs[0].Indexing["pending"] != 1 || list.KBs[0].Indexing["processing"] != 0 ||
		list.KBs[0].Indexing["failed"] != 1 || list.KBs[0].Docs != 3 || list.KBs[0].Chunks != 1 {
		t.Fatalf("list: %+v", list.KBs)
	}

	var docs struct {
		Documents []map[string]any `json:"documents"`
	}
	decode(t, k.do(t, user, http.MethodGet, "/knowledge-bases/"+kbID+"/documents", nil), &docs)
	byName := map[string]map[string]any{}
	for _, d := range docs.Documents {
		byName[d["original_filename"].(string)] = d
	}
	if d := byName["leave.txt"]; d["status"] != "indexed" || d["progress_done"] != float64(1) || d["progress_total"] != float64(1) || d["indexed_at"] == nil {
		t.Fatalf("indexed document: %v", d)
	}
	if d := byName["empty.txt"]; d["status"] != "failed" || d["error_code"] != "empty_text" || d["error_message"] == nil {
		t.Fatalf("failed document: %v", d)
	}
	if d := byName["later.txt"]; d["status"] != "pending" || d["error_code"] != nil {
		t.Fatalf("pending document: %v", d)
	}
	var detail struct {
		KB map[string]any `json:"knowledge_base"`
	}
	decode(t, k.do(t, user, http.MethodGet, "/knowledge-bases/"+kbID, nil), &detail)
	if ix, _ := detail.KB["indexing"].(map[string]any); ix["pending"] != float64(1) || ix["failed"] != float64(1) {
		t.Fatalf("detail indexing: %v", detail.KB)
	}
	id := byName["leave.txt"]["id"].(string)
	if rec := k.do(t, user, http.MethodPost, "/knowledge-bases/"+kbID+"/documents/"+id+"/reindex", nil); rec.Code != http.StatusAccepted || !strings.Contains(rec.Body.String(), `"status":"pending"`) {
		t.Fatalf("reindex answers 202 pending: %d %s", rec.Code, rec.Body.String())
	}
}

func TestFIX132_DocumentFileServesTheOriginalBytes(t *testing.T) {
	k := newKBHTTP(t)
	owner := uuid.New()
	kbID := k.createKB(t, owner, "HR")
	content := "ราคาห้องพัก ห้องละ 1200 บาท\nline two"
	up := k.upload(t, owner, kbID, "ราคา ห้อง (2027).txt", "text/plain", content)
	var doc struct {
		ID string `json:"id"`
	}
	decode(t, up, &doc)
	path := "/knowledge-bases/" + kbID + "/documents/" + doc.ID + "/file"

	rec := k.do(t, owner, http.MethodGet, path, nil)
	if rec.Code != http.StatusOK || rec.Body.String() != content {
		t.Fatalf("file: %d %q", rec.Code, rec.Body.String())
	}
	if ct := rec.Header().Get("Content-Type"); !strings.HasPrefix(ct, "text/plain") {
		t.Fatalf("content type %q", ct)
	}
	cd := rec.Header().Get("Content-Disposition")
	if !strings.HasPrefix(cd, "inline; ") {
		t.Fatalf("disposition %q", cd)
	}
	i := strings.Index(cd, "filename*=UTF-8''")
	if i < 0 {
		t.Fatalf("no RFC 5987 filename: %q", cd)
	}
	name, err := url.PathUnescape(cd[i+len("filename*=UTF-8''"):])
	if err != nil || name != "ราคา ห้อง (2027).txt" {
		t.Fatalf("the exact filename survives: %q %v", name, err)
	}
	if rec.Header().Get("X-Content-Type-Options") != "nosniff" || rec.Header().Get("Content-Security-Policy") != "sandbox" {
		t.Fatalf("security headers: %v", rec.Header())
	}

	if rec := k.do(t, uuid.New(), http.MethodGet, path, nil); rec.Code != http.StatusNotFound {
		t.Fatalf("a colleague gets 404, got %d", rec.Code)
	}
	if rec := k.do(t, owner, http.MethodGet, "/knowledge-bases/"+kbID+"/documents/"+uuid.NewString()+"/file", nil); rec.Code != http.StatusNotFound {
		t.Fatalf("an unknown document is 404, got %d", rec.Code)
	}
	// A document of another of the owner's knowledge bases is not reachable
	// through this one.
	other := k.createKB(t, owner, "Other")
	if rec := k.do(t, owner, http.MethodGet, "/knowledge-bases/"+other+"/documents/"+doc.ID+"/file", nil); rec.Code != http.StatusNotFound {
		t.Fatalf("wrong knowledge base → 404, got %d", rec.Code)
	}
	_ = context.Background()
}
