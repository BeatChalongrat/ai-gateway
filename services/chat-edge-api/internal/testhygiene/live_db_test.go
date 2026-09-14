package testhygiene

// FIX119 Part C1 — no test may open its own database connection.
//
// Seven test files used to build a pgxpool from a default DSN that named
// the LIVE database (sran_ai on :5433) and fell back to it whenever
// CHAT_EDGE_TEST_DSN was unset. FIX118 counted the result in the
// production table: 176 fixture rows. internal/testdb.Fresh is the only
// door now — it refuses a live database name outright and fails (not
// skips) when no test database can be had. This guard fails the build
// on any _test.go that reaches for pgxpool directly or spells the live
// DSN, so the door cannot be walked around again.

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var liveDSNMarkers = []string{
	"pgxpool.New(",
	"/sran_ai",
	"localhost:5433",
	"CHAT_EDGE_TEST_DSN",
}

func TestNoTestOpensItsOwnDatabase(t *testing.T) {
	root := moduleRoot(t)
	var offenders []string
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			if name := info.Name(); name == "vendor" || name == ".git" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, "_test.go") {
			return nil
		}
		rel, relErr := filepath.Rel(root, path)
		if relErr != nil {
			rel = path
		}
		rel = filepath.ToSlash(rel)
		// The helper itself, and this guard's own text.
		if strings.HasPrefix(rel, "internal/testdb/") || strings.HasSuffix(rel, "testhygiene/live_db_test.go") {
			return nil
		}
		b, readErr := os.ReadFile(path)
		if readErr != nil {
			return readErr
		}
		src := string(b)
		for _, marker := range liveDSNMarkers {
			if strings.Contains(src, marker) {
				offenders = append(offenders, rel+" ("+marker+")")
				break
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk: %v", err)
	}
	if len(offenders) > 0 {
		t.Fatalf("these tests open (or could open) a database on their own; use internal/testdb.Fresh(t):\n  %s",
			strings.Join(offenders, "\n  "))
	}
}
