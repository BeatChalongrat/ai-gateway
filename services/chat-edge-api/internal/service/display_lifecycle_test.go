package service

// Phase 4A.2.4 — Display Lifecycle unit tests (no DB).
// Covers: write-representation choice, never-restore token selection, the
// consumer representation matrix, owner restore-on-read (with a fake Vault V2
// restorer), wrong-actor denial, TTL-expired fallback, vault-disabled fallback,
// and the pure non-restoring helper used by export/search/admin.

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// fakeRestorer is a stand-in Vault V2 restore-on-read bridge.
type fakeRestorer struct {
	allowActor uuid.UUID
	raw        string
	available  bool
	gotTokens  []string
	called     bool
}

func (f *fakeRestorer) RestoreDisplay(
	ctx context.Context, actor DisplayActor, m model.Message, restorable []string,
) (string, bool) {
	f.called = true
	f.gotTokens = restorable
	if !f.available {
		return "", false
	}
	if actor.UserID != f.allowActor {
		return "", false
	}
	return f.raw, true
}

func canonSafeMsg(canon string) model.Message {
	c := canon
	return model.Message{
		Role:             "assistant",
		ContentMasked:    canon, // under the flag, stored display == canonical
		ContentCanonical: &c,
		CanonicalStatus:  CanonicalStatusComplete,
		DisplayState:     DisplayStateCanonicalSafe,
	}
}

// ---- write-representation -------------------------------------------------

func TestPersistedDisplay(t *testing.T) {
	canon := "salary <<EMAIL_1>>"

	// Flag OFF → legacy passthrough (restored display persisted, as before).
	off := &MessageService{}
	if got, st := off.persistedDisplay("RAW 50000 alice@x.com", &canon); got != "RAW 50000 alice@x.com" || st != DisplayStateLegacy {
		t.Fatalf("flag off must passthrough+legacy, got %q/%q", got, st)
	}

	on := &MessageService{}
	on.SetDisplayLifecycle(true, false, false)
	// Complete canonical → store tokenized canonical, canonical_safe.
	if got, st := on.persistedDisplay("RAW 50000 alice@x.com", &canon); got != canon || st != DisplayStateCanonicalSafe {
		t.Fatalf("flag on+canonical must store canonical/canonical_safe, got %q/%q", got, st)
	}
	// No canonical → redacted placeholder, never raw.
	got, st := on.persistedDisplay("RAW 50000 alice@x.com", nil)
	if got != RedactedDisplayPlaceholder || st != DisplayStateRedacted {
		t.Fatalf("flag on+no-canonical must redact, got %q/%q", got, st)
	}
	if strings.Contains(got, "50000") || strings.Contains(got, "alice@x.com") {
		t.Fatalf("redacted placeholder leaked raw: %q", got)
	}
}

// ---- never-restore token selection ---------------------------------------

func TestSelectRestorableTokens(t *testing.T) {
	in := "a <<EMAIL_1>> b <<PERSON_2>> c <<SECRET_1>> d <<NATIONAL_ID_1>> e <<CREDIT_CARD_3>> f <r0> g <<EMAIL_1>>"
	got := selectRestorableTokens(in)
	want := map[string]bool{"<<EMAIL_1>>": true, "<<PERSON_2>>": true}
	if len(got) != len(want) {
		t.Fatalf("expected %v restorable, got %v", want, got)
	}
	for _, tok := range got {
		if !want[tok] {
			t.Fatalf("token %q must NOT be restorable", tok)
		}
	}
	// Explicit never-restore checks.
	for _, secret := range []string{"<<SECRET_1>>", "<<NATIONAL_ID_1>>", "<<CREDIT_CARD_3>>", "<r0>", "<<APIKEY_1>>", "<<PASSWORD_1>>"} {
		if !neverRestoreToken(secret) {
			t.Fatalf("%q must be never-restore", secret)
		}
	}
	if neverRestoreToken("<<EMAIL_1>>") {
		t.Fatalf("<<EMAIL_1>> must be restorable")
	}
}

// ---- representation matrix ------------------------------------------------

func TestRepresentationMatrix_FlagOff(t *testing.T) {
	off := &MessageService{}
	m := model.Message{ContentMasked: "RAW 50000", DisplayState: DisplayStateLegacy}
	for _, c := range []DisplayConsumer{
		ConsumerBrowserReloadOwner, ConsumerAdminDefault, ConsumerExportShare,
		ConsumerSearchIndex, ConsumerProviderHistory, ConsumerAnalytics,
	} {
		if got := off.ResolveRepresentation(context.Background(), c, m, DisplayActor{}); got.Content != "RAW 50000" {
			t.Fatalf("flag off must passthrough for consumer %d, got %q", c, got.Content)
		}
	}
}

func TestRepresentationMatrix_NonRestoringConsumers(t *testing.T) {
	on := &MessageService{}
	on.SetDisplayLifecycle(true, false, false)
	canon := "salary <<EMAIL_1>>"
	safe := canonSafeMsg(canon)
	legacy := model.Message{ContentMasked: "RAW 50000 alice@x.com", DisplayState: DisplayStateLegacy}

	for _, c := range []DisplayConsumer{ConsumerAdminDefault, ConsumerExportShare, ConsumerSearchIndex} {
		// canonical_safe → tokenized canonical (no raw).
		if got := on.ResolveRepresentation(context.Background(), c, safe, DisplayActor{}); got.Content != canon || !got.Redacted {
			t.Fatalf("consumer %d canonical_safe should be tokenized, got %q", c, got.Content)
		}
		// legacy → redacted (never raw to these consumers).
		got := on.ResolveRepresentation(context.Background(), c, legacy, DisplayActor{})
		if got.Content != RedactedDisplayPlaceholder || strings.Contains(got.Content, "50000") || strings.Contains(got.Content, "alice@x.com") {
			t.Fatalf("consumer %d legacy must be redacted, got %q", c, got.Content)
		}
	}

	// Provider history → canonical only, never restore.
	if got := on.ResolveRepresentation(context.Background(), ConsumerProviderHistory, safe, DisplayActor{}); got.Content != canon || got.Restored {
		t.Fatalf("provider history must be canonical-only, got %q restored=%v", got.Content, got.Restored)
	}
	// Analytics → no content.
	if got := on.ResolveRepresentation(context.Background(), ConsumerAnalytics, safe, DisplayActor{}); got.Content != "" {
		t.Fatalf("analytics must expose no content, got %q", got.Content)
	}
}

// ---- owner restore-on-read ------------------------------------------------

func TestOwnerReload_VaultDisabled_Fallback(t *testing.T) {
	on := &MessageService{}
	on.SetDisplayLifecycle(true, false, false)
	canon := "salary <<EMAIL_1>>"
	got := on.ResolveRepresentation(context.Background(), ConsumerBrowserReloadOwner, canonSafeMsg(canon), DisplayActor{UserID: uuid.New()})
	if got.Content != canon || got.Restored || !got.Redacted {
		t.Fatalf("vault-disabled reload must fall back to tokenized, got %q restored=%v", got.Content, got.Restored)
	}
}

func TestOwnerReload_RestoresForOwner(t *testing.T) {
	owner := uuid.New()
	fr := &fakeRestorer{allowActor: owner, raw: "salary alice@x.com", available: true}
	on := &MessageService{}
	on.SetDisplayLifecycle(true, false, false)
	on.SetDisplayRestorer(fr)
	canon := "salary <<EMAIL_1>>"
	got := on.ResolveRepresentation(context.Background(), ConsumerBrowserReloadOwner, canonSafeMsg(canon), DisplayActor{UserID: owner})
	if !got.Restored || got.Content != "salary alice@x.com" {
		t.Fatalf("owner restore failed: %q restored=%v", got.Content, got.Restored)
	}
	// The restorer must only ever be handed restorable (non-secret) tokens.
	if len(fr.gotTokens) != 1 || fr.gotTokens[0] != "<<EMAIL_1>>" {
		t.Fatalf("restorer got wrong tokens: %v", fr.gotTokens)
	}
}

func TestOwnerReload_WrongActorDenied(t *testing.T) {
	fr := &fakeRestorer{allowActor: uuid.New(), raw: "salary alice@x.com", available: true}
	on := &MessageService{}
	on.SetDisplayLifecycle(true, false, false)
	on.SetDisplayRestorer(fr)
	canon := "salary <<EMAIL_1>>"
	got := on.ResolveRepresentation(context.Background(), ConsumerBrowserReloadOwner, canonSafeMsg(canon), DisplayActor{UserID: uuid.New()})
	if got.Restored || got.Content != canon {
		t.Fatalf("wrong actor must not restore: %q restored=%v", got.Content, got.Restored)
	}
}

func TestOwnerReload_TTLExpiredFallback(t *testing.T) {
	owner := uuid.New()
	fr := &fakeRestorer{allowActor: owner, available: false} // TTL expired / no entry
	on := &MessageService{}
	on.SetDisplayLifecycle(true, false, false)
	on.SetDisplayRestorer(fr)
	canon := "salary <<EMAIL_1>>"
	got := on.ResolveRepresentation(context.Background(), ConsumerBrowserReloadOwner, canonSafeMsg(canon), DisplayActor{UserID: owner})
	if got.Restored || got.Content != canon {
		t.Fatalf("TTL-expired must fall back to tokenized, got %q restored=%v", got.Content, got.Restored)
	}
}

func TestOwnerReload_SecretTokenNeverRestored(t *testing.T) {
	owner := uuid.New()
	fr := &fakeRestorer{allowActor: owner, raw: "SHOULD-NOT-APPEAR", available: true}
	on := &MessageService{}
	on.SetDisplayLifecycle(true, false, false)
	on.SetDisplayRestorer(fr)
	// Only secret/identifier tokens → no restorable tokens → restorer not called.
	canon := "key <<SECRET_1>> id <<NATIONAL_ID_1>>"
	got := on.ResolveRepresentation(context.Background(), ConsumerBrowserReloadOwner, canonSafeMsg(canon), DisplayActor{UserID: owner})
	if fr.called {
		t.Fatalf("restorer must NOT be called when only secret/identifier tokens present")
	}
	if got.Restored || got.Content != canon {
		t.Fatalf("secret-only row must fall back tokenized, got %q", got.Content)
	}
}

func TestProviderHistory_NeverCallsRestore(t *testing.T) {
	owner := uuid.New()
	fr := &fakeRestorer{allowActor: owner, raw: "raw", available: true}
	on := &MessageService{}
	on.SetDisplayLifecycle(true, false, false)
	on.SetDisplayRestorer(fr)
	on.ResolveRepresentation(context.Background(), ConsumerProviderHistory, canonSafeMsg("x <<EMAIL_1>>"), DisplayActor{UserID: owner})
	if fr.called {
		t.Fatalf("provider history must NEVER call display restore")
	}
}

// ---- legacy rows ----------------------------------------------------------

func TestLegacyReload_NonStrictReadable_StrictRedacted(t *testing.T) {
	legacy := model.Message{ContentMasked: "RAW 50000", DisplayState: DisplayStateLegacy}

	nonStrict := &MessageService{}
	nonStrict.SetDisplayLifecycle(true, false, false)
	if got := nonStrict.ResolveRepresentation(context.Background(), ConsumerBrowserReloadOwner, legacy, DisplayActor{}); got.Content != "RAW 50000" {
		t.Fatalf("non-strict legacy must stay readable to owner, got %q", got.Content)
	}

	strict := &MessageService{}
	strict.SetDisplayLifecycle(true, false, true) // strictLegacy
	if got := strict.ResolveRepresentation(context.Background(), ConsumerBrowserReloadOwner, legacy, DisplayActor{}); got.Content != RedactedDisplayPlaceholder {
		t.Fatalf("strict legacy must redact, got %q", got.Content)
	}
}

// ---- pure non-restoring helper (export/search/admin) ----------------------

func TestNonRestoringRepresentation(t *testing.T) {
	if got := NonRestoringRepresentation(false, model.Message{ContentMasked: "RAW"}); got != "RAW" {
		t.Fatalf("flag off must passthrough, got %q", got)
	}
	if got := NonRestoringRepresentation(true, canonSafeMsg("x <<EMAIL_1>>")); got != "x <<EMAIL_1>>" {
		t.Fatalf("canonical_safe should expose tokenized, got %q", got)
	}
	if got := NonRestoringRepresentation(true, model.Message{ContentMasked: "RAW 50000", DisplayState: DisplayStateLegacy}); got != RedactedDisplayPlaceholder {
		t.Fatalf("legacy must redact, got %q", got)
	}
	if got := NonRestoringRepresentation(true, model.Message{ContentMasked: "RAW", DisplayState: ""}); got != RedactedDisplayPlaceholder {
		t.Fatalf("empty/pre-migration state must redact, got %q", got)
	}
}
