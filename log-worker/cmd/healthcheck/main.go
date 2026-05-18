// Tiny healthcheck binary for the distroless runtime image.
//
// Distroless static has no shell or wget, so the standard
// ``CMD-SHELL: wget …`` healthcheck pattern fails with
// ``stat /bin/sh: no such file``. We ship this binary alongside the
// log-worker and reference it directly from HEALTHCHECK / docker-
// compose healthcheck:
//
//	test: ["/healthcheck"]
//
// Exits 0 iff GET http://localhost:${METRICS_PORT:-9100}/healthz
// returns HTTP 200 within 3 seconds. Any other outcome → exit 1.
package main

import (
	"net/http"
	"os"
	"time"
)

func main() {
	port := os.Getenv("METRICS_PORT")
	if port == "" {
		port = "9100"
	}

	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://localhost:" + port + "/healthz")
	if err != nil {
		os.Exit(1)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		os.Exit(1)
	}
	os.Exit(0)
}
