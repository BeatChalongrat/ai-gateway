package config

import "testing"

// FIX126 O1 — background work has its own model by default; FIX126 F1 —
// MAX_FILES_PER_MESSAGE keeps its default of 5.
func TestFIX126_BackgroundTaskModelDefault(t *testing.T) {
	// Placeholder values for the required variables (none is used here).
	for _, k := range []string{"DATABASE_URL", "REDIS_URL", "GATEWAY_API_URL", "USER_JWT_SECRET",
		"SERVICE_JWT_SECRET", "CSRF_SECRET", "PORTAL_JWT_SECRET"} {
		t.Setenv(k, "test-placeholder")
	}
	cfg, err := Load()
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if cfg.BackgroundTaskModel != "deepseek-v4-flash" {
		t.Fatalf("BACKGROUND_TASK_MODEL default = %q, want deepseek-v4-flash", cfg.BackgroundTaskModel)
	}
	if cfg.MaxFilesPerMessage != 5 {
		t.Fatalf("MAX_FILES_PER_MESSAGE default = %d, want 5", cfg.MaxFilesPerMessage)
	}
	t.Setenv("BACKGROUND_TASK_MODEL", "other-model")
	if cfg, _ = Load(); cfg.BackgroundTaskModel != "other-model" {
		t.Fatalf("BACKGROUND_TASK_MODEL override = %q", cfg.BackgroundTaskModel)
	}
}
