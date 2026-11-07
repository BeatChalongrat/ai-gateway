// Guard: a test may not close its pool with `defer` while also
// registering `t.Cleanup`.
//
// Go runs every deferred call when the test function returns, and only
// THEN runs the t.Cleanup callbacks. So this shape:
//
//	pool := testPool(t)
//	defer pool.Close()                    // runs first
//	t.Cleanup(func() { ...DELETE... })    // runs second, on a dead pool
//
// closes the pool before the cleanup can use it. The DELETEs fail, the
// errors are discarded (`_, _ =`), and the test goes green having
// cleaned nothing — the shared test database keeps every row the test
// created. That is not hypothetical: it was measured leaking rows on a
// green run, and it accumulated thousands of orphaned rows over time,
// including rows in a settings table whose flags drive a deletion job.
//
// The fix is ordering, not effort: register the close through
// t.Cleanup FIRST. Cleanups run last-in-first-out, so the close ends
// up last, after the row deletes:
//
//	t.Cleanup(pool.Close)
//
// This test fails on any *_test.go that pairs `defer pool.Close()`
// with a `t.Cleanup(`. A file that uses `defer pool.Close()` and
// registers no cleanup at all is NOT flagged — it never cleaned up in
// the first place, which is a separate (and deliberate-or-not)
// question for a human, not something to silently "fix" here.
package testhygiene

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// moduleRoot walks up from the package directory to the directory
// holding go.mod, so the scan covers the whole service regardless of
// where `go test` was invoked from.
func moduleRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	for i := 0; i < 10; i++ {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	t.Fatal("could not locate go.mod above the test package")
	return ""
}

func TestNoDeferPoolCloseAlongsideCleanup(t *testing.T) {
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
		// Don't flag this guard's own explanatory text.
		if strings.HasSuffix(path, filepath.Join("testhygiene", "pool_close_test.go")) {
			return nil
		}
		b, readErr := os.ReadFile(path)
		if readErr != nil {
			return readErr
		}
		src := string(b)
		if strings.Contains(src, "defer pool.Close()") &&
			strings.Contains(src, "t.Cleanup(") {
			rel, relErr := filepath.Rel(root, path)
			if relErr != nil {
				rel = path
			}
			offenders = append(offenders, filepath.ToSlash(rel))
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk: %v", err)
	}

	if len(offenders) > 0 {
		t.Errorf(
			"these test files close the pool with `defer` while also using "+
				"t.Cleanup — every deferred call runs BEFORE any cleanup, so "+
				"the cleanup's queries hit a closed pool and silently do "+
				"nothing. Replace `defer pool.Close()` with "+
				"`t.Cleanup(pool.Close)` (cleanups are LIFO, so the close "+
				"runs last):\n  %s",
			strings.Join(offenders, "\n  "),
		)
	}
}
