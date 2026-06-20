package service

import "testing"

func TestFeatureScope(t *testing.T) {
	// off → nothing
	off := NewFeatureScope("off", "acme-corp-001/acme-customer-support")
	if off.TenantInScope("acme-corp-001") || off.Effective(true, "acme-corp-001") {
		t.Fatalf("off must disable all")
	}
	// allowlist match / mismatch
	al := NewFeatureScope("allowlist", "acme-corp-001/acme-customer-support, x/y")
	if !al.TenantInScope("acme-corp-001") {
		t.Fatalf("allowlisted tenant must be in scope")
	}
	if al.TenantInScope("demo-tenant-000") {
		t.Fatalf("non-allowlisted tenant must be out of scope")
	}
	if !al.Effective(true, "acme-corp-001") || al.Effective(false, "acme-corp-001") {
		t.Fatalf("Effective must require global flag AND scope")
	}
	// blank tenant disabled
	if al.TenantInScope("") || al.TenantInScope("  ") {
		t.Fatalf("blank tenant must be out of scope")
	}
	// global → any non-empty tenant
	gl := NewFeatureScope("global", "")
	if !gl.TenantInScope("any") || gl.TenantInScope("") {
		t.Fatalf("global must allow any non-empty tenant only")
	}
	// unknown mode → off; malformed entries skipped
	bad := NewFeatureScope("weird", "no-slash, /noproj, notenant/, good/proj, a/b/c")
	if bad.TenantInScope("good") {
		t.Fatalf("unknown mode must fail closed")
	}
	al2 := NewFeatureScope("allowlist", "no-slash, /noproj, notenant/, good/proj, a/b/c")
	if !al2.TenantInScope("good") {
		t.Fatalf("valid entry 'good/proj' must parse")
	}
	for _, badT := range []string{"no-slash", "notenant", "a"} {
		if al2.TenantInScope(badT) {
			t.Fatalf("malformed entry %q must be skipped", badT)
		}
	}
}
