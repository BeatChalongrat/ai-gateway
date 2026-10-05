// Package runner is Agent Market's execution plane: it claims queued runs,
// executes their steps in the stock sandbox image, calls the model gateway
// with its own key and writes a row for every step. It runs only in the
// agent-runner container (cmd/agent-runner) — never inside chat-edge.
package runner

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// Engine is the Python side (backend/agent_market) as one zip. The same
// bytes go to every sandbox step as a read-only input, and the package
// checksum is computed from them — what was checked is what ran.
type Engine struct {
	ZipPath  string
	Checksum string // sha256 of engine files (everything except packages/)
	// Build is the sha256 of the whole zip, packages included (AM2): what a
	// released version is pinned to and where its engine is stored.
	Build    string
	zipBytes []byte
	Packages map[string]*Package // by slug
}

// Package is one installable agent inside the engine zip.
type Package struct {
	Slug         string
	PyPackage    string
	Version      string
	Manifest     agentmarket.Manifest
	RawManifest  json.RawMessage
	Instructions string
	SelfTest     json.RawMessage
	// IconSVG is the package's icon file, checked (AM10, look.go); empty when
	// the package ships none.
	IconSVG  string
	Checksum string
	prefix   string
}

// ReleaseManifest is what the registered version carries: the manifest with
// the icon written in.
func (p *Package) ReleaseManifest() (json.RawMessage, error) {
	return agentmarket.ReleaseManifest(p.RawManifest, p.IconSVG)
}

// LoadEngine opens a built zip, or — for development and tests — zips a
// source folder (…/backend/agent_market) deterministically.
func LoadEngine(zipPath, srcDir string) (*Engine, error) {
	var data []byte
	var err error
	switch {
	case zipPath != "":
		data, err = os.ReadFile(zipPath)
		if err != nil {
			return nil, fmt.Errorf("engine zip: %w", err)
		}
	case srcDir != "":
		data, err = BuildZip(srcDir)
		if err != nil {
			return nil, err
		}
		f, err := os.CreateTemp("", "am_engine-*.zip")
		if err != nil {
			return nil, err
		}
		if _, err := f.Write(data); err != nil {
			f.Close()
			return nil, err
		}
		f.Close()
		zipPath = f.Name()
	default:
		return nil, fmt.Errorf("engine: set AGENT_MARKET_ENGINE_ZIP or AGENT_MARKET_ENGINE_SRC")
	}
	build := sha256.Sum256(data)
	e := &Engine{ZipPath: zipPath, zipBytes: data, Build: hex.EncodeToString(build[:]), Packages: map[string]*Package{}}
	zr, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return nil, fmt.Errorf("engine zip: %w", err)
	}
	entries := map[string][]byte{}
	for _, f := range zr.File {
		if f.FileInfo().IsDir() {
			continue
		}
		rc, err := f.Open()
		if err != nil {
			return nil, err
		}
		b, err := io.ReadAll(rc)
		rc.Close()
		if err != nil {
			return nil, err
		}
		entries[f.Name] = b
	}
	e.Checksum = checksumOf(entries, func(n string) bool { return !strings.HasPrefix(n, "agent_market/packages/") })
	for name, b := range entries {
		if !strings.HasPrefix(name, "agent_market/packages/") || !strings.HasSuffix(name, "/agent.json") {
			continue
		}
		prefix := strings.TrimSuffix(name, "agent.json")
		m, err := agentmarket.ParseManifest(b)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", name, err)
		}
		p := &Package{Slug: m.ID, PyPackage: m.PythonPackage, Version: m.Version, Manifest: m, RawManifest: b, prefix: prefix}
		p.Instructions = string(entries[prefix+"AGENT.md"])
		p.SelfTest = entries[prefix+"self-test.json"]
		if m.IconFile != "" {
			svg, ok := entries[prefix+m.IconFile]
			if !ok {
				return nil, fmt.Errorf("%s: icon_file %q is not in the package", name, m.IconFile)
			}
			if err := agentmarket.CheckIcon(svg); err != nil {
				return nil, fmt.Errorf("%s: %s: %w", name, m.IconFile, err)
			}
			p.IconSVG = string(svg)
		}
		if _, err := p.ReleaseManifest(); err != nil {
			return nil, fmt.Errorf("%s: %w", name, err)
		}
		p.Checksum = checksumOf(entries, func(n string) bool { return strings.HasPrefix(n, prefix) })
		e.Packages[m.ID] = p
	}
	if len(e.Packages) == 0 {
		return nil, fmt.Errorf("engine zip has no packages")
	}
	return e, nil
}

// PackageFile returns a file shipped inside a package (sample data).
func (e *Engine) PackageFile(p *Package, rel string) ([]byte, error) {
	zr, err := zip.NewReader(bytes.NewReader(e.zipBytes), int64(len(e.zipBytes)))
	if err != nil {
		return nil, err
	}
	want := p.prefix + strings.TrimPrefix(path.Clean("/"+rel), "/")
	for _, f := range zr.File {
		if f.Name == want {
			rc, err := f.Open()
			if err != nil {
				return nil, err
			}
			defer rc.Close()
			return io.ReadAll(rc)
		}
	}
	return nil, fmt.Errorf("package file %s not found", rel)
}

func checksumOf(entries map[string][]byte, include func(string) bool) string {
	var names []string
	for n := range entries {
		if include(n) {
			names = append(names, n)
		}
	}
	sort.Strings(names)
	h := sha256.New()
	for _, n := range names {
		sum := sha256.Sum256(entries[n])
		h.Write([]byte(n))
		h.Write([]byte{0})
		h.Write(sum[:])
	}
	return hex.EncodeToString(h.Sum(nil))
}

// BuildZip zips backend/agent_market with fixed timestamps and sorted names,
// leaving out tests, eval fixtures and caches: the runtime never needs them.
func BuildZip(src string) ([]byte, error) {
	var files []string
	err := filepath.WalkDir(src, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, p)
		rel = filepath.ToSlash(rel)
		if d.IsDir() {
			switch d.Name() {
			case "__pycache__", "tests", "fixtures", "nasty", "tools", ".pytest_cache":
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasSuffix(rel, ".pyc") || strings.HasPrefix(d.Name(), ".") {
			return nil
		}
		files = append(files, rel)
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Strings(files)
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	stamp := time.Date(2026, 9, 25, 0, 0, 0, 0, time.UTC)
	for _, rel := range files {
		b, err := os.ReadFile(filepath.Join(src, filepath.FromSlash(rel)))
		if err != nil {
			return nil, err
		}
		hdr := &zip.FileHeader{Name: "agent_market/" + rel, Method: zip.Deflate, Modified: stamp}
		hdr.SetMode(0o644)
		w, err := zw.CreateHeader(hdr)
		if err != nil {
			return nil, err
		}
		if _, err := w.Write(b); err != nil {
			return nil, err
		}
	}
	if err := zw.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}
