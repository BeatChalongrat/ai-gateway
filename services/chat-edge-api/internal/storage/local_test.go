// Smoke-level coverage for the local backend. The S3 backend is
// exercised end-to-end against MinIO in the docker-compose dev
// loop; mocking the AWS SDK here would add maintenance without
// catching real failures.

package storage

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestLocalBackend_RoundTrip(t *testing.T) {
	dir := t.TempDir()
	b, err := NewLocalBackend(dir)
	if err != nil {
		t.Fatalf("NewLocalBackend: %v", err)
	}
	ctx := context.Background()
	key := "tenant/conv/blob_hello.txt"
	body := []byte("hello, world\n")

	if err := b.Upload(ctx, key, bytes.NewReader(body), "text/plain", int64(len(body))); err != nil {
		t.Fatalf("Upload: %v", err)
	}

	// The actual file lives under {dir}/{key} with native separators.
	want := filepath.Join(dir, filepath.FromSlash(key))
	if _, err := os.Stat(want); err != nil {
		t.Fatalf("expected file at %s: %v", want, err)
	}

	ok, err := b.Exists(ctx, key)
	if err != nil || !ok {
		t.Fatalf("Exists: ok=%v err=%v", ok, err)
	}

	rc, size, _, err := b.Download(ctx, key)
	if err != nil {
		t.Fatalf("Download: %v", err)
	}
	if size != int64(len(body)) {
		t.Errorf("size: got %d, want %d", size, len(body))
	}
	got, err := io.ReadAll(rc)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	// Close BEFORE Delete — Windows refuses to remove a file while a
	// handle is still open. POSIX would tolerate the deferred close.
	if err := rc.Close(); err != nil {
		t.Errorf("close: %v", err)
	}
	if !bytes.Equal(got, body) {
		t.Errorf("body: got %q, want %q", got, body)
	}

	if err := b.Delete(ctx, key); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if _, err := os.Stat(want); !os.IsNotExist(err) {
		t.Fatalf("expected file gone, got %v", err)
	}
	// Delete is idempotent.
	if err := b.Delete(ctx, key); err != nil {
		t.Errorf("Delete (idempotent): %v", err)
	}
}

func TestLocalBackend_DownloadNotFound(t *testing.T) {
	b, err := NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatalf("NewLocalBackend: %v", err)
	}
	_, _, _, err = b.Download(context.Background(), "missing/key")
	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("expected ErrNotFound, got %v", err)
	}
}

func TestLocalBackend_PathTraversalBlocked(t *testing.T) {
	b, err := NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatalf("NewLocalBackend: %v", err)
	}
	// "..": should be rejected regardless of how many slashes lead it.
	cases := []string{
		"../etc/passwd",
		"a/../../etc/passwd",
		"../../outside.txt",
	}
	for _, c := range cases {
		err := b.Upload(
			context.Background(), c,
			bytes.NewReader([]byte("x")), "text/plain", 1,
		)
		if err == nil {
			t.Errorf("expected error for %q, got nil", c)
		}
	}
}

func TestLocalBackend_PresignedURLEmpty(t *testing.T) {
	b, err := NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatalf("NewLocalBackend: %v", err)
	}
	url, err := b.GeneratePresignedURL(context.Background(), "k", time.Minute)
	if err != nil {
		t.Errorf("GeneratePresignedURL: %v", err)
	}
	if url != "" {
		t.Errorf("expected empty url, got %q", url)
	}
}

func TestJoinKey(t *testing.T) {
	cases := []struct {
		in   []string
		want string
	}{
		{[]string{"a", "b", "c"}, "a/b/c"},
		{[]string{"", "b", "c"}, "b/c"},
		{[]string{"a", "", "c"}, "a/c"},
		{[]string{"a", "b", ""}, "a/b"},
		{[]string{}, ""},
	}
	for _, tc := range cases {
		got := JoinKey(tc.in...)
		if got != tc.want {
			t.Errorf("JoinKey(%v) = %q, want %q", tc.in, got, tc.want)
		}
	}
}
