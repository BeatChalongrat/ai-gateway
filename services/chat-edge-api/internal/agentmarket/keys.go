package agentmarket

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"path"
	"regexp"
	"sort"
	"strings"

	"github.com/google/uuid"
)

// Storage keys. Everything Agent Market stores lives under this prefix, so
// removal deletes one folder (local volume) or one prefix (S3).
const StoragePrefix = "agent-market"

var unsafeName = regexp.MustCompile(`[^\p{L}\p{M}\p{N}._ -]+`)

// SafeName keeps a display name but drops anything path-like.
func SafeName(name string) string {
	name = path.Base(strings.ReplaceAll(name, "\\", "/"))
	name = unsafeName.ReplaceAllString(name, "_")
	name = strings.Trim(name, ". ")
	if name == "" {
		name = "file"
	}
	if r := []rune(name); len(r) > 120 {
		name = string(r[len(r)-120:])
	}
	return name
}

// UploadKey is where an uploaded file's bytes live.
func UploadKey(tenant, install, file uuid.UUID, name string) string {
	return path.Join(StoragePrefix, tenant.String(), "installs", install.String(), "files", file.String(), SafeName(name))
}

// BlobKey is where a step's large output lives (parsed.json, computed.json…).
func BlobKey(tenant, run uuid.UUID, stepKey, name string) string {
	return path.Join(StoragePrefix, tenant.String(), "runs", run.String(), "steps", stepKey[:16], SafeName(name))
}

// ArtifactKey is where a delivered file lives.
func ArtifactKey(tenant, run uuid.UUID, name string) string {
	return path.Join(StoragePrefix, tenant.String(), "runs", run.String(), "artifacts", SafeName(name))
}

// LogoKey is where a theme logo lives.
func LogoKey(tenant uuid.UUID, version int, name string) string {
	return path.Join(StoragePrefix, tenant.String(), "themes", itoa(version), SafeName(name))
}

func itoa(n int) string {
	b, _ := json.Marshal(n)
	return string(b)
}

// StepKey derives a step's idempotency key from the run, the step name and
// everything that decides its output. Same inputs → same key → the step is
// found done and never replayed; changed inputs → a new key → a new step.
func StepKey(run uuid.UUID, name string, parts ...any) string {
	h := sha256.New()
	h.Write([]byte(run.String()))
	h.Write([]byte{0})
	h.Write([]byte(name))
	for _, p := range parts {
		h.Write([]byte{0})
		h.Write(canonical(p))
	}
	return hex.EncodeToString(h.Sum(nil))
}

// canonical JSON-encodes with sorted map keys (encoding/json already sorts
// map keys; this normalises RawMessage values through a decode/encode).
func canonical(v any) []byte {
	switch x := v.(type) {
	case string:
		return []byte(x)
	case json.RawMessage:
		var any_ any
		if json.Unmarshal(x, &any_) == nil {
			b, _ := json.Marshal(any_)
			return b
		}
		return x
	case []string:
		c := append([]string(nil), x...)
		sort.Strings(c)
		b, _ := json.Marshal(c)
		return b
	default:
		b, _ := json.Marshal(x)
		return b
	}
}

// ArtifactName is {agent}_{period}_{kind}_v{n}.{ext}.
func ArtifactName(slug, period, format string, version int) string {
	agent := slug
	if i := strings.LastIndex(slug, "."); i >= 0 {
		agent = slug[i+1:]
	}
	kind := map[string]string{"docx": "report", "pptx": "deck", "xlsx": "tables"}[format]
	if kind == "" {
		kind = "output"
	}
	if period == "" {
		period = "undated"
	}
	return agent + "_" + period + "_" + kind + "_v" + itoa(version) + "." + format
}
