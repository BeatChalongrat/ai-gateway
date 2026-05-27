// Local-filesystem implementation of Backend. Tested every dev day
// and the default when STORAGE_BACKEND is unset.
//
// On-disk layout mirrors the key 1:1:
//
//	{basePath}/{key}
//
// Keys use forward slashes; on Windows they're translated to the
// native separator at the boundary. Parent directories are created
// on demand (0o750). Path-traversal is blocked by re-resolving each
// key against basePath and rejecting anything that escapes.

package storage

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// LocalBackend persists blobs under a single base directory. Safe to
// share between goroutines — each Upload/Download/Delete call resolves
// the path independently, so there's no mutable state to guard.
type LocalBackend struct {
	basePath string
}

// NewLocalBackend resolves the supplied base path to an absolute one
// up front so subsequent path-traversal checks have a stable anchor.
// The directory is created lazily (Upload mkdirs the parent on first
// write) so a missing/empty mount doesn't block startup.
func NewLocalBackend(basePath string) (*LocalBackend, error) {
	if basePath == "" {
		basePath = "./data/uploads"
	}
	abs, err := filepath.Abs(basePath)
	if err != nil {
		return nil, fmt.Errorf("resolve local storage root: %w", err)
	}
	return &LocalBackend{basePath: abs}, nil
}

func (l *LocalBackend) Upload(
	ctx context.Context, key string, body io.Reader, _ string, _ int64,
) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	dst, err := l.resolve(key)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(dst), 0o750); err != nil {
		return fmt.Errorf("local storage mkdir: %w", err)
	}
	// Write to a sibling temp file then rename so a partial write
	// can't leave a half-blob behind. atomic on POSIX, best-effort
	// on Windows (still better than direct write).
	tmp, err := os.CreateTemp(filepath.Dir(dst), ".upload-*")
	if err != nil {
		return fmt.Errorf("local storage tempfile: %w", err)
	}
	tmpName := tmp.Name()
	_, err = io.Copy(tmp, body)
	closeErr := tmp.Close()
	if err != nil {
		_ = os.Remove(tmpName)
		return fmt.Errorf("local storage write: %w", err)
	}
	if closeErr != nil {
		_ = os.Remove(tmpName)
		return fmt.Errorf("local storage close: %w", closeErr)
	}
	if err := os.Rename(tmpName, dst); err != nil {
		_ = os.Remove(tmpName)
		return fmt.Errorf("local storage rename: %w", err)
	}
	return nil
}

func (l *LocalBackend) Download(
	ctx context.Context, key string,
) (io.ReadCloser, int64, string, error) {
	if err := ctx.Err(); err != nil {
		return nil, 0, "", err
	}
	src, err := l.resolve(key)
	if err != nil {
		return nil, 0, "", err
	}
	fp, err := os.Open(src)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, 0, "", ErrNotFound
		}
		return nil, 0, "", fmt.Errorf("local storage open: %w", err)
	}
	st, err := fp.Stat()
	if err != nil {
		_ = fp.Close()
		return nil, 0, "", fmt.Errorf("local storage stat: %w", err)
	}
	// Local backend doesn't track content-type; the caller has it
	// in the DB row anyway and treats "" as "use what's on file".
	return fp, st.Size(), "", nil
}

func (l *LocalBackend) Delete(ctx context.Context, key string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	dst, err := l.resolve(key)
	if err != nil {
		return err
	}
	if err := os.Remove(dst); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil // idempotent
		}
		return fmt.Errorf("local storage remove: %w", err)
	}
	return nil
}

func (l *LocalBackend) Exists(ctx context.Context, key string) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	dst, err := l.resolve(key)
	if err != nil {
		return false, err
	}
	if _, err := os.Stat(dst); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return false, nil
		}
		return false, err
	}
	return true, nil
}

// GeneratePresignedURL is not supported by the local backend.
// Returning empty + nil tells the caller to fall back to streaming.
func (l *LocalBackend) GeneratePresignedURL(
	_ context.Context, _ string, _ time.Duration,
) (string, error) {
	return "", nil
}

// OpenLocalCopy hands the underlying disk path back to the caller —
// no copying necessary since this IS the disk. Cleanup is a no-op.
func (l *LocalBackend) OpenLocalCopy(
	ctx context.Context, key string,
) (string, func(), error) {
	if err := ctx.Err(); err != nil {
		return "", func() {}, err
	}
	dst, err := l.resolve(key)
	if err != nil {
		return "", func() {}, err
	}
	if _, err := os.Stat(dst); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return "", func() {}, ErrNotFound
		}
		return "", func() {}, err
	}
	return dst, func() {}, nil
}

// resolve normalises the key into an absolute filesystem path under
// basePath. Rejects anything that resolves outside basePath — the
// classic ``../../etc/passwd`` defence.
func (l *LocalBackend) resolve(key string) (string, error) {
	// Convert forward-slash keys to the platform separator.
	clean := filepath.FromSlash(strings.TrimLeft(key, "/"))
	if clean == "" {
		return "", fmt.Errorf("local storage: empty key")
	}
	full := filepath.Join(l.basePath, clean)
	rel, err := filepath.Rel(l.basePath, full)
	if err != nil || strings.HasPrefix(rel, "..") || rel == ".." {
		return "", fmt.Errorf("local storage: path traversal blocked: %s", key)
	}
	return full, nil
}
