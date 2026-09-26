package service

import (
	"context"
	"io"
	"sync"

	"github.com/google/uuid"
)

// AccountDataProvider — AM2: a feature that keeps a user's data outside
// the core schemas (Agent Market keeps its own schema and object prefix)
// registers itself here, so "export all my data" and account deletion
// reach that data without the core naming the feature. Removing the
// feature removes its registration; the core keeps this registry and one
// call in each of export, deletion request and purge.
type AccountDataProvider interface {
	// Name is the provider's folder in the export ZIP (e.g. "agent-market").
	Name() string
	// Export writes the user's data in this tenant through add (a path in
	// the provider's folder, the bytes) and returns lines for the README.
	Export(ctx context.Context, tenantID, userID uuid.UUID, add func(path string, r io.Reader) error) ([]string, error)
	// Stop runs when the user asks to delete the account: nothing more is
	// started or kept running for them (queued and running work cancelled).
	Stop(ctx context.Context, userID uuid.UUID) (map[string]int, error)
	// Purge erases everything the provider keeps for the user, in every
	// tenant, objects first. Idempotent.
	Purge(ctx context.Context, userID uuid.UUID) (map[string]int, error)
}

var (
	accountProvidersMu sync.RWMutex
	accountProviders   []AccountDataProvider
)

// RegisterAccountDataProvider adds a provider (at start, by the feature).
func RegisterAccountDataProvider(p AccountDataProvider) {
	accountProvidersMu.Lock()
	defer accountProvidersMu.Unlock()
	for _, q := range accountProviders {
		if q.Name() == p.Name() {
			return
		}
	}
	accountProviders = append(accountProviders, p)
}

// AccountDataProviders lists the registered providers.
func AccountDataProviders() []AccountDataProvider {
	accountProvidersMu.RLock()
	defer accountProvidersMu.RUnlock()
	return append([]AccountDataProvider(nil), accountProviders...)
}

// resetAccountDataProviders is for tests.
func resetAccountDataProviders() {
	accountProvidersMu.Lock()
	defer accountProvidersMu.Unlock()
	accountProviders = nil
}
