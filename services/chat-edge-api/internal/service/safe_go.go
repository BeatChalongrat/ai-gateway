package service

import (
	"fmt"
	"log/slog"
	"runtime/debug"
)

// safeGo runs fn in a new goroutine with panic recovery.
//
// The chat-edge send path fires several detached background goroutines
// (memory use-count bumps, S3 conversation-state extraction, S8
// preference extraction) on a fresh context.Background() so they outlive
// the request. The hazard: an unrecovered panic in ANY goroutine
// propagates straight to the Go runtime and crashes the whole process —
// taking down every other in-flight request with it. These background
// tasks are best-effort and must never have that blast radius.
//
// safeGo wraps the goroutine body in a recover() that logs the panic with
// its stack and swallows it. name identifies the task in the log line so
// a recurring panic is traceable. Use it for any fire-and-forget
// goroutine whose failure should not be fatal.
func safeGo(name string, fn func()) {
	go func() {
		defer func() {
			if r := recover(); r != nil {
				slog.Error("recovered panic in background goroutine",
					"goroutine", name,
					"panic", fmt.Sprintf("%v", r),
					"stack", string(debug.Stack()),
				)
			}
		}()
		fn()
	}()
}
