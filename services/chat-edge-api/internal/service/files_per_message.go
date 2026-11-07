package service

// FIX126 F1 — the files-per-message limit is enforced by the server, fail
// closed. The page stops at MAX_FILES_PER_MESSAGE (5), but the send path
// accepted any number: 20 dropped files were uploaded and sent. The attach
// linker did refuse more than the cap — after the user row was written, and
// its error was discarded — so the turn ran anyway. A send with more file
// ids than the cap is now refused BEFORE anything is written (no user row,
// no turn claim, no gateway call); the handler answers 422 TOO_MANY_FILES.

import (
	"errors"
	"fmt"

	"github.com/google/uuid"
)

// ErrTooManyFiles — a send named more file ids than one message may carry.
// errors.Is also matches ErrFileTooManyAttached, the attach linker's error,
// so a caller that already maps that one keeps working.
var ErrTooManyFiles = errors.New("message: too many files attached")

// TooManyFilesError carries the numbers for the wire message.
type TooManyFilesError struct {
	Max int
	Got int
}

func (e *TooManyFilesError) Error() string {
	return fmt.Sprintf("message: %d files attached; at most %d files per message", e.Got, e.Max)
}

func (e *TooManyFilesError) Is(target error) bool {
	return target == ErrTooManyFiles || target == ErrFileTooManyAttached
}

// maxFilesReporter is the optional half of FileLinker that knows the
// configured cap (*FileService does). Test fakes need not implement it.
type maxFilesReporter interface {
	MaxFilesPerMessage() int
}

// MaxFilesPerMessage is the cap this service enforces on a send: the file
// service's configured MAX_FILES_PER_MESSAGE when wired, else the default.
func (s *MessageService) MaxFilesPerMessage() int {
	if r, ok := s.fileSvc.(maxFilesReporter); ok && r != nil {
		if n := r.MaxFilesPerMessage(); n > 0 {
			return n
		}
	}
	return DefaultMaxFilesPerMessage
}

// checkFilesPerMessage refuses a send whose DISTINCT file ids exceed the cap.
// (A repeated id links one row, so it counts once.)
func (s *MessageService) checkFilesPerMessage(in SendInput) error {
	if len(in.FileIDs) == 0 {
		return nil
	}
	seen := make(map[uuid.UUID]struct{}, len(in.FileIDs))
	for _, id := range in.FileIDs {
		seen[id] = struct{}{}
	}
	if max := s.MaxFilesPerMessage(); len(seen) > max {
		return &TooManyFilesError{Max: max, Got: len(seen)}
	}
	return nil
}
