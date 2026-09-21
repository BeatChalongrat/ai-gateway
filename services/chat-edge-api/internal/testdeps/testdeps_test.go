package testdeps

import "testing"

// recordingTB records Fatalf/Skipf instead of ending the test.
type recordingTB struct {
	testing.TB
	failed, skipped bool
	msg             string
}

func (r *recordingTB) Helper() {}
func (r *recordingTB) Fatalf(format string, _ ...any) {
	r.failed, r.msg = true, format
}
func (r *recordingTB) Skipf(format string, _ ...any) {
	r.skipped, r.msg = true, format
}

// FIX126 O7 — a missing dependency skips locally and fails when required.
func TestMissing_SkipsLocallyFailsWhenRequired(t *testing.T) {
	cases := []struct {
		ci, require string
		wantFail    bool
	}{
		{"", "", false},
		{"false", "0", false},
		{"true", "", true},
		{"", "1", true},
		{"TRUE", "", true},
	}
	for _, c := range cases {
		t.Setenv("CI", c.ci)
		t.Setenv("CHAT_EDGE_REQUIRE_DEPS", c.require)
		if Required() != c.wantFail {
			t.Fatalf("CI=%q CHAT_EDGE_REQUIRE_DEPS=%q: Required()=%v", c.ci, c.require, Required())
		}
		rec := &recordingTB{TB: t}
		Missing(rec, "redis unreachable: %v", "dial tcp")
		if rec.failed != c.wantFail || rec.skipped == c.wantFail {
			t.Fatalf("CI=%q CHAT_EDGE_REQUIRE_DEPS=%q: failed=%v skipped=%v, want fail=%v",
				c.ci, c.require, rec.failed, rec.skipped, c.wantFail)
		}
	}
}
