// Tests for the COPY-based bulk insert path.
//
// Uses ``github.com/DATA-DOG/go-sqlmock`` to exercise the
// transaction lifecycle without a live Postgres. The COPY protocol's
// row-level Exec calls are mocked the same way any prepared-Stmt
// Exec is — sqlmock doesn't understand pq.CopyIn's STDIN streaming,
// but the call sequence (Prepare → Exec rows → Exec flush → Commit)
// is what we want to pin anyway.
//
// Each test asserts BOTH the function's return value AND that every
// expected interaction fired — ``mock.ExpectationsWereMet()`` after
// the call catches dropped rollbacks and orphaned transactions.
package db

import (
	"errors"
	"fmt"
	"regexp"
	"testing"

	"github.com/DATA-DOG/go-sqlmock"

	pb "github.com/beatchalongrat/ai-gateway/log-worker/pb"
)

func sampleRecord(id string) *pb.LogRecord {
	return &pb.LogRecord{
		RequestId:        id,
		TenantId:         "t",
		ProjectId:        "p",
		Provider:         "openai",
		Model:            "gpt-4o",
		Status:           "success",
		PolicyAction:     "allow",
		LatencyMs:        42,
		PromptTokens:     10,
		CompletionTokens: 20,
		TotalTokens:      30,
	}
}

// copyStmtPattern matches the SQL emitted by pq.CopyIn for the
// request_logs table. We don't pin the full column list because that
// would make the test brittle every time we add a column; matching
// the COPY-FROM-STDIN preamble is enough to verify we hit the
// right statement.
var copyStmtPattern = regexp.QuoteMeta(`COPY "request_logs"`)

// TestInsertBatch_Success — happy path: Begin → Prepare → Exec rows → Exec flush → Commit.
func TestInsertBatch_Success(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatalf("sqlmock new: %v", err)
	}
	defer db.Close()

	mock.ExpectBegin()
	prepare := mock.ExpectPrepare(copyStmtPattern)
	// Two row Execs + the empty-args flush Exec at the end.
	prepare.ExpectExec().WillReturnResult(sqlmock.NewResult(0, 0))
	prepare.ExpectExec().WillReturnResult(sqlmock.NewResult(0, 0))
	prepare.ExpectExec().WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectCommit()

	batch := []*pb.LogRecord{sampleRecord("a"), sampleRecord("b")}
	if err := InsertBatch(db, batch); err != nil {
		t.Fatalf("expected nil err, got %v", err)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Errorf("unmet expectations: %v", err)
	}
}

// TestInsertBatch_EmptyBatchNoop — empty batches must not touch the DB.
func TestInsertBatch_EmptyBatchNoop(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatalf("sqlmock new: %v", err)
	}
	defer db.Close()

	if err := InsertBatch(db, nil); err != nil {
		t.Errorf("empty batch should be a noop, got %v", err)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Errorf("empty batch should not call mock: %v", err)
	}
}

// TestInsertBatch_BeginError — Begin fails → no rollback (no txn to roll back), returns error.
func TestInsertBatch_BeginError(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatalf("sqlmock new: %v", err)
	}
	defer db.Close()

	mock.ExpectBegin().WillReturnError(errors.New("begin boom"))

	err = InsertBatch(db, []*pb.LogRecord{sampleRecord("a")})
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Errorf("unmet expectations: %v", err)
	}
}

// TestInsertBatch_PrepareError — Prepare fails → Rollback must fire, error returned.
func TestInsertBatch_PrepareError(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatalf("sqlmock new: %v", err)
	}
	defer db.Close()

	mock.ExpectBegin()
	mock.ExpectPrepare(copyStmtPattern).WillReturnError(errors.New("prepare boom"))
	mock.ExpectRollback()

	err = InsertBatch(db, []*pb.LogRecord{sampleRecord("a")})
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Errorf("unmet expectations: %v", err)
	}
}

// TestInsertBatch_ExecError — Exec fails mid-batch → Rollback fires.
func TestInsertBatch_ExecError(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatalf("sqlmock new: %v", err)
	}
	defer db.Close()

	mock.ExpectBegin()
	prepare := mock.ExpectPrepare(copyStmtPattern)
	prepare.ExpectExec().WillReturnError(errors.New("exec boom"))
	mock.ExpectRollback()

	err = InsertBatch(db, []*pb.LogRecord{sampleRecord("a")})
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Errorf("unmet expectations: %v", err)
	}
}

// TestInsertBatch_FlushExecError — final empty-args Exec (the COPY
// flush) fails → Rollback fires. Distinct from ExecError because
// the row Execs succeed first.
func TestInsertBatch_FlushExecError(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatalf("sqlmock new: %v", err)
	}
	defer db.Close()

	mock.ExpectBegin()
	prepare := mock.ExpectPrepare(copyStmtPattern)
	prepare.ExpectExec().WillReturnResult(sqlmock.NewResult(0, 0))
	prepare.ExpectExec().WillReturnError(errors.New("flush boom"))
	mock.ExpectRollback()

	err = InsertBatch(db, []*pb.LogRecord{sampleRecord("a")})
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	if !errorContains(err, "flush boom") {
		t.Errorf("expected wrapped flush error, got %v", err)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Errorf("unmet expectations: %v", err)
	}
}

// TestInsertBatch_CommitError — every step succeeds except Commit;
// the function returns the wrapped commit error. Rollback is not
// expected because the COPY path doesn't attempt one after Commit
// fails (Postgres has already torn the transaction down).
func TestInsertBatch_CommitError(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatalf("sqlmock new: %v", err)
	}
	defer db.Close()

	mock.ExpectBegin()
	prepare := mock.ExpectPrepare(copyStmtPattern)
	prepare.ExpectExec().WillReturnResult(sqlmock.NewResult(0, 0))
	prepare.ExpectExec().WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectCommit().WillReturnError(errors.New("commit boom"))

	err = InsertBatch(db, []*pb.LogRecord{sampleRecord("a")})
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	if !errorContains(err, "commit boom") {
		t.Errorf("expected wrapped commit error, got %v", err)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Errorf("unmet expectations: %v", err)
	}
}

// errorContains tiny helper — avoids pulling in testify just for assertions.
func errorContains(err error, want string) bool {
	if err == nil {
		return false
	}
	return contains(err.Error(), want)
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || indexOf(s, substr) >= 0)
}

func indexOf(s, substr string) int {
	return findSubstring(s, substr)
}

func findSubstring(s, substr string) int {
	if substr == "" {
		return 0
	}
	for i := 0; i+len(substr) <= len(s); i++ {
		if s[i:i+len(substr)] == substr {
			return i
		}
	}
	return -1
}

// Sanity check — verify the column list constant doesn't drift from
// the proto field set without breaking the rest of the suite.
func TestColumnListCovers042Migration(t *testing.T) {
	required := []string{
		"fallback_used",
		"fallback_attempts",
		"original_provider",
		"original_model",
	}
	seen := map[string]bool{}
	for _, c := range requestLogColumns {
		seen[c] = true
	}
	for _, c := range required {
		if !seen[c] {
			t.Errorf("requestLogColumns missing %q (migration 042 column)", c)
		}
	}
}

// Compile-time guard — ensures the package builds under ``go test``
// even when the testing binary is the only consumer of these helpers.
var _ = fmt.Sprintf
