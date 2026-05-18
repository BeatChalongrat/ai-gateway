// Command log-worker is the async sink for request_logs.
//
// It accepts LogRecord RPCs from the Python gateway and flushes
// batches into Postgres via the COPY protocol. See proto/logworker.proto
// for the on-wire contract and worker/worker.go for the buffering /
// flushing logic.
package main

import (
	"context"
	"database/sql"
	"errors"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync/atomic"
	"syscall"
	"time"

	_ "github.com/lib/pq"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"google.golang.org/grpc"

	pb "github.com/beatchalongrat/ai-gateway/log-worker/pb"
	"github.com/beatchalongrat/ai-gateway/log-worker/worker"
)

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

func main() {
	dsn := envOr("POSTGRES_DSN",
		"host=postgres port=5432 dbname=ai_gateway user=postgres password=postgres sslmode=disable")
	grpcPort := envOr("GRPC_PORT", "50052")
	metricsPort := envOr("METRICS_PORT", "9100")
	batchSize := envInt("BATCH_SIZE", 100)
	flushMS := envInt("FLUSH_INTERVAL_MS", 100)
	bufferSize := envInt("QUEUE_BUFFER", 50000)

	log.Printf("log-worker booting dsn_host=%q grpc_port=%s metrics_port=%s batch_size=%d flush_ms=%d buffer=%d",
		maskDSN(dsn), grpcPort, metricsPort, batchSize, flushMS, bufferSize)

	// Open the pool eagerly so a misconfigured DSN fails the container
	// healthcheck instead of silently surviving until the first Enqueue.
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		log.Fatalf("sql.Open: %v", err)
	}
	// Modest pool — one in-flight COPY per concurrent batch worker is
	// usually enough; multi-row COPYs are CPU-bound on the server side
	// and a wide pool just spreads contention.
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(2)
	db.SetConnMaxLifetime(5 * time.Minute)

	pingCtx, pingCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer pingCancel()
	if err := db.PingContext(pingCtx); err != nil {
		log.Fatalf("postgres ping failed: %v", err)
	}

	w := worker.New(worker.Config{
		DB:            db,
		BatchSize:     batchSize,
		FlushInterval: time.Duration(flushMS) * time.Millisecond,
		BufferSize:    bufferSize,
	})

	// Metrics endpoint — separate port so the gRPC port is dedicated.
	go func() {
		mux := http.NewServeMux()
		mux.Handle("/metrics", promhttp.Handler())
		mux.HandleFunc("/healthz", func(rw http.ResponseWriter, _ *http.Request) {
			rw.WriteHeader(http.StatusOK)
			_, _ = rw.Write([]byte("ok\n"))
		})
		srv := &http.Server{
			Addr:              ":" + metricsPort,
			Handler:           mux,
			ReadHeaderTimeout: 5 * time.Second,
		}
		log.Printf("metrics listening :%s", metricsPort)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf("metrics server error: %v", err)
		}
	}()

	lis, err := net.Listen("tcp", ":"+grpcPort)
	if err != nil {
		log.Fatalf("net.Listen: %v", err)
	}

	grpcServer := grpc.NewServer(
		// Idle pings keep half-open TCP detected fast on a network
		// partition; the gateway expects a fast Enqueue or fallback.
		grpc.MaxConcurrentStreams(1024),
	)
	pb.RegisterLogWorkerServiceServer(grpcServer, &server{w: w, draining: 0})

	// Start the worker AFTER the gRPC server is registered but BEFORE
	// Serve() takes traffic — so the first record arrives at a running
	// channel + active flusher.
	w.Start()

	// Trap SIGINT/SIGTERM for a clean drain. The order matters:
	// stop accepting new RPCs first (GracefulStop closes the
	// listener + waits for in-flight RPCs), then drain the channel,
	// then close the DB.
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		log.Printf("gRPC listening :%s", grpcPort)
		if err := grpcServer.Serve(lis); err != nil {
			log.Printf("grpc serve error: %v", err)
		}
	}()

	<-stop
	log.Printf("shutdown signal received, draining…")

	grpcServer.GracefulStop()
	w.Stop(30 * time.Second)
	_ = db.Close()
	log.Printf("shutdown complete")
}

// server adapts the worker to the generated gRPC interface.
type server struct {
	pb.UnimplementedLogWorkerServiceServer
	w        *worker.Worker
	draining int32
}

func (s *server) Enqueue(_ context.Context, rec *pb.LogRecord) (*pb.EnqueueResponse, error) {
	if atomic.LoadInt32(&s.draining) == 1 {
		return &pb.EnqueueResponse{Accepted: false, Reason: "draining"}, nil
	}
	if rec == nil {
		return &pb.EnqueueResponse{Accepted: false, Reason: "nil record"}, nil
	}
	accepted := s.w.Enqueue(rec)
	resp := &pb.EnqueueResponse{Accepted: accepted}
	if !accepted {
		resp.Reason = "queue_full"
	}
	return resp, nil
}

func (s *server) Health(_ context.Context, _ *pb.HealthRequest) (*pb.HealthResponse, error) {
	depth, dropped, flushed := s.w.Stats()
	status := "ready"
	if atomic.LoadInt32(&s.draining) == 1 {
		status = "draining"
	}
	return &pb.HealthResponse{
		Status:        status,
		QueueDepth:    depth,
		DroppedTotal:  dropped,
		FlushedTotal:  flushed,
	}, nil
}

// maskDSN hides the password before logging the DSN. Cheap operator
// safety net — the env value is still visible to anyone with shell
// access on the container, but boot logs don't accidentally leak it
// into log aggregation systems.
func maskDSN(dsn string) string {
	// libpq DSN is space-separated key=value; we redact ``password``.
	out := []byte(dsn)
	i := 0
	for i < len(out) {
		// scan for "password="
		if i+9 <= len(out) && string(out[i:i+9]) == "password=" {
			// skip past 'password=' then redact until next whitespace
			j := i + 9
			for j < len(out) && out[j] != ' ' {
				out[j] = '*'
				j++
			}
			i = j
			continue
		}
		i++
	}
	return string(out)
}
