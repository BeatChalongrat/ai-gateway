// Package storage abstracts blob persistence behind a Backend
// interface so the service can swap between a local filesystem (dev)
// and S3-compatible storage (MinIO + production AWS) without
// touching any caller code.
//
// Keys are forward-slash separated and follow the same hierarchy the
// pre-refactor code wrote to disk:
//
//	conversation-files/{tenant_id}/{conversation_id}/{file_id}_{name}
//	knowledge/{tenant_id}/{kb_id}/{doc_id}_{name}
//
// The Backend never sees the absolute path / S3 URL — that's a
// concern of the implementation. Callers always speak in keys; the
// backend resolves the key to wherever it lives.
package storage

import (
	"context"
	"errors"
	"io"
	"time"
)

// ErrNotFound is returned by every Backend when the key doesn't
// exist. Callers should compare with errors.Is so future backends
// that wrap the error stay compatible.
var ErrNotFound = errors.New("storage: not found")

// Backend is the contract every storage implementation honours.
// All methods are tenant-agnostic — the tenant slice of the key
// hierarchy is built by the caller (file_service / knowledge_service)
// so the backend stays a thin I/O layer.
type Backend interface {
	// Upload writes the body under the given key. ``contentType`` is
	// surfaced as the object's Content-Type metadata (S3) or used to
	// pick the disk layout (local; ignored). ``size`` is advisory —
	// some backends use it for upload optimisations.
	Upload(ctx context.Context, key string, body io.Reader, contentType string, size int64) error

	// Download returns a reader for the object at ``key``. The caller
	// MUST close the reader. ``size`` and ``contentType`` are
	// best-effort metadata — empty / 0 when the backend doesn't
	// track them.
	Download(ctx context.Context, key string) (body io.ReadCloser, size int64, contentType string, err error)

	// Delete removes the object. Returns nil even if the object was
	// already absent (idempotent), unless the backend can't tell —
	// then ErrNotFound is acceptable.
	Delete(ctx context.Context, key string) error

	// Exists checks whether an object lives at ``key`` without
	// fetching its body. Used by health-checks and post-write
	// validation.
	Exists(ctx context.Context, key string) (bool, error)

	// GeneratePresignedURL returns a time-limited URL the caller can
	// hand to a browser for a direct GET. Empty string + nil error
	// means "this backend doesn't issue presigned URLs" (typically
	// the local-filesystem backend); the caller falls back to
	// streaming the body through.
	GeneratePresignedURL(ctx context.Context, key string, expiry time.Duration) (string, error)

	// OpenLocalCopy returns a filesystem path the caller can hand to
	// out-of-process tools (the Python text extractor, for example).
	// The cleanup func must always be invoked.
	//
	// For the local backend this is a no-op (return the underlying
	// path + a noop cleanup). For S3 it materialises a temp file,
	// downloads the object into it, and cleans it up on close.
	OpenLocalCopy(ctx context.Context, key string) (path string, cleanup func(), err error)
}

// JoinKey concatenates path segments into a forward-slash key. Empty
// segments are skipped so callers can pass a "" for "no prefix"
// without producing leading "/". Centralised so the key shape stays
// consistent between file_service and knowledge_service.
func JoinKey(parts ...string) string {
	out := ""
	for _, p := range parts {
		if p == "" {
			continue
		}
		if out == "" {
			out = p
			continue
		}
		out += "/" + p
	}
	return out
}
