package service

import (
	"context"
	"encoding/base64"
	"fmt"
	"github.com/google/uuid"
	"io"
	"net/http"
	"os"
)

// Resolve references through the same ownership boundary as sandbox input files.
// No URLs or arbitrary filesystem paths are accepted from the model.
func resolveImageSources(ctx context.Context, files codeFileResolver, ec ToolExecContext, ids []string) ([]string, error) {
	if len(ids) == 0 {
		return nil, nil
	}
	if files == nil || len(ids) > 4 {
		return nil, fmt.Errorf("Image editing needs 1–4 accessible conversation images.")
	}
	sources := make([]string, 0, len(ids))
	seen := map[uuid.UUID]bool{}
	total := 0
	for _, id := range ids {
		fid, err := uuid.Parse(id)
		if err != nil {
			return nil, fmt.Errorf("Invalid reference image file ID.")
		}
		if seen[fid] {
			continue
		}
		seen[fid] = true
		path, _, cleanup, err := files.OpenConversationFileCopy(ctx, ec.TenantID, ec.UserID, ec.ConversationID, fid)
		if err != nil {
			return nil, fmt.Errorf("Reference image must belong to this conversation.")
		}
		if cleanup != nil {
			defer cleanup()
		}
		f, err := os.Open(path)
		if err != nil {
			return nil, fmt.Errorf("Cannot read reference image.")
		}
		raw, err := io.ReadAll(io.LimitReader(f, 4*1024*1024+1))
		f.Close()
		total += len(raw)
		if err != nil || len(raw) > 4*1024*1024 || total > 12*1024*1024 {
			return nil, fmt.Errorf("Reference images exceed 4 MiB each / 12 MiB total.")
		}
		mime := http.DetectContentType(raw)
		if mime != "image/png" && mime != "image/jpeg" && mime != "image/webp" {
			return nil, fmt.Errorf("Reference images must be PNG, JPEG or WebP.")
		}
		sources = append(sources, "data:"+mime+";base64,"+base64.StdEncoding.EncodeToString(raw))
	}
	return sources, nil
}
