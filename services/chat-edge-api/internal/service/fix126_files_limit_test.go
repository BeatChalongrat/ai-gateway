package service

// FIX126 F1 — service-level pins of the files-per-message cap.

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"
)

func nIDs(n int) []uuid.UUID {
	out := make([]uuid.UUID, n)
	for i := range out {
		out[i] = uuid.New()
	}
	return out
}

func TestFIX126_SixFilesRefusedBeforeAnythingIsWritten(t *testing.T) {
	f := newFix125Fixture(t)
	in := f.in("หกไฟล์")
	in.FileIDs = nIDs(6)
	_, err := f.s.SendStream(context.Background(), in)
	if !errors.Is(err, ErrTooManyFiles) {
		t.Fatalf("six files: err = %v, want ErrTooManyFiles", err)
	}
	var tm *TooManyFilesError
	if !errors.As(err, &tm) || tm.Max != DefaultMaxFilesPerMessage || tm.Got != 6 {
		t.Fatalf("error must carry max=%d got=6: %#v", DefaultMaxFilesPerMessage, tm)
	}
	if n := f.countRows(t, "user"); n != 0 {
		t.Fatalf("a refused send wrote %d user rows", n)
	}
	if f.gw.calls() != 0 || f.s.TurnInFlight(f.conv.ID) {
		t.Fatalf("a refused send reached the gateway (%d) or kept the claim (%v)", f.gw.calls(), f.s.TurnInFlight(f.conv.ID))
	}

	// A repeated id counts once: six ids naming five files is five.
	in = f.in("ห้าไฟล์")
	five := nIDs(5)
	in.FileIDs = append(five, five[0])
	h, err := f.s.SendStream(context.Background(), in)
	if err != nil {
		t.Fatalf("five distinct files: %v", err)
	}
	drainHandle(h)
	if n := f.countRows(t, "user"); n != 1 {
		t.Fatalf("five files: %d user rows, want 1", n)
	}
}

func TestFIX126_LimitFollowsTheConfiguredKnob(t *testing.T) {
	s := &MessageService{}
	if got := s.MaxFilesPerMessage(); got != DefaultMaxFilesPerMessage {
		t.Fatalf("no file service: limit %d, want %d", got, DefaultMaxFilesPerMessage)
	}
	s.fileSvc = NewFileServiceForTest(Knobs{MaxFilesPerMessage: 2})
	if got := s.MaxFilesPerMessage(); got != 2 {
		t.Fatalf("MAX_FILES_PER_MESSAGE=2: limit %d", got)
	}
	if err := s.checkFilesPerMessage(SendInput{FileIDs: nIDs(3)}); !errors.Is(err, ErrTooManyFiles) {
		t.Fatalf("3 files over a limit of 2: %v", err)
	}
	if err := s.checkFilesPerMessage(SendInput{FileIDs: nIDs(2)}); err != nil {
		t.Fatalf("2 files at a limit of 2: %v", err)
	}
}
