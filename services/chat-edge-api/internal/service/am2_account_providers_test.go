package service

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

// AM2: a feature that keeps a user's data outside the core schemas
// registers an AccountDataProvider; export, the deletion request and the
// purge reach it without the core naming it.

type fakeProvider struct {
	stopped, purged []uuid.UUID
	fail            bool
}

func (p *fakeProvider) Name() string { return "fake-feature" }

func (p *fakeProvider) Export(_ context.Context, tenant, user uuid.UUID, add func(string, io.Reader) error) ([]string, error) {
	if err := add("data.json", strings.NewReader(`{"user":"`+user.String()+`"}`)); err != nil {
		return nil, err
	}
	return []string{"fake-feature/  the fake feature's data"}, nil
}

func (p *fakeProvider) Stop(_ context.Context, user uuid.UUID) (map[string]int, error) {
	p.stopped = append(p.stopped, user)
	return map[string]int{"runs": 1}, nil
}

func (p *fakeProvider) Purge(_ context.Context, user uuid.UUID) (map[string]int, error) {
	if p.fail {
		return nil, errors.New("storage down")
	}
	p.purged = append(p.purged, user)
	return map[string]int{"rows": 3}, nil
}

func TestAccountExport_RegisteredProvidersAddTheirFolder(t *testing.T) {
	pool := retentionTestPool(t)
	t.Cleanup(pool.Close)
	resetAccountDataProviders()
	t.Cleanup(resetAccountDataProviders)
	fake := &fakeProvider{}
	RegisterAccountDataProvider(fake)
	RegisterAccountDataProvider(fake) // registering twice keeps one
	if n := len(AccountDataProviders()); n != 1 {
		t.Fatalf("providers = %d", n)
	}
	ctx := context.Background()
	root := t.TempDir()
	store, _ := storage.NewLocalBackend(filepath.Join(root, "uploads"))
	f := seedExportUser(t, ctx, pool, store)
	_, files := runExport(t, ctx, newExportService(pool, store, root, 500<<20), pool, f)
	if got := string(files["fake-feature/data.json"]); !strings.Contains(got, f.user.String()) {
		t.Fatalf("the provider's folder: %q", got)
	}
	if !strings.Contains(string(files["README.txt"]), "fake-feature/  the fake feature's data") {
		t.Fatal("the README lists what the provider added")
	}
}

func TestAccountDeletion_RegisteredProvidersStopAndPurge(t *testing.T) {
	pool := retentionTestPool(t)
	t.Cleanup(pool.Close)
	resetAccountDataProviders()
	t.Cleanup(resetAccountDataProviders)
	fake := &fakeProvider{fail: true}
	RegisterAccountDataProvider(fake)
	ctx := context.Background()
	root := t.TempDir()
	store, _ := storage.NewLocalBackend(filepath.Join(root, "uploads"))
	f := seedAccount(t, ctx, pool, store, root)
	svc := newAccountDeletion(pool, store, root)
	if _, err := svc.RequestDeletion(ctx, f.user); err != nil {
		t.Fatal(err)
	}
	if len(fake.stopped) != 1 || fake.stopped[0] != f.user {
		t.Fatalf("the request stops the provider's work: %v", fake.stopped)
	}
	// a provider that cannot purge stops the purge before the identity row goes (it is retried)
	if _, err := svc.Purge(ctx, f.user); err == nil {
		t.Fatal("a failed provider purge must fail the purge")
	}
	if n := rowCount(t, ctx, pool, `SELECT count(*) FROM identity.users WHERE id = $1`, f.user); n != 1 {
		t.Fatalf("identity row gone although the provider's data is not: %d", n)
	}
	fake.fail = false
	counts, err := svc.Purge(ctx, f.user)
	if err != nil {
		t.Fatal(err)
	}
	if counts["fake-feature.rows"] != 3 || len(fake.purged) != 1 {
		t.Fatalf("counts %v purged %v", counts, fake.purged)
	}
	_ = os.Remove
}
