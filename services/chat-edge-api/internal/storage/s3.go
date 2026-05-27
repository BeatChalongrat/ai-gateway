// S3 implementation of Backend. Works against both AWS S3 and any
// S3-compatible service (MinIO is the dev target). The endpoint
// override + ``UsePathStyle`` switch are what makes MinIO work — AWS
// itself uses virtual-host buckets; MinIO doesn't.
//
// The S3 client is constructed once and reused — the SDK is
// goroutine-safe so we share it across requests without locking.

package storage

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	s3types "github.com/aws/aws-sdk-go-v2/service/s3/types"
)

// S3Config is the wire-friendly bag of knobs the factory passes in.
// Endpoint is empty for AWS, "http://minio:9000" (or similar) for
// MinIO / Wasabi / R2 / etc.
type S3Config struct {
	Bucket         string
	Region         string
	Endpoint       string
	AccessKey      string
	SecretKey      string
	ForcePathStyle bool
}

type S3Backend struct {
	bucket  string
	client  *s3.Client
	presign *s3.PresignClient
}

func NewS3Backend(cfg S3Config) (*S3Backend, error) {
	if cfg.Bucket == "" {
		return nil, errors.New("s3 storage: bucket is required")
	}
	if cfg.Region == "" {
		// MinIO ignores the region but the SDK requires a non-empty
		// value to construct a client.
		cfg.Region = "us-east-1"
	}

	loadOpts := []func(*awsconfig.LoadOptions) error{
		awsconfig.WithRegion(cfg.Region),
	}
	// Use static creds when the user provided them; otherwise let
	// the SDK walk the default credential chain (env, IMDS, role).
	if cfg.AccessKey != "" && cfg.SecretKey != "" {
		loadOpts = append(loadOpts,
			awsconfig.WithCredentialsProvider(
				credentials.NewStaticCredentialsProvider(
					cfg.AccessKey, cfg.SecretKey, "",
				),
			),
		)
	}

	awsCfg, err := awsconfig.LoadDefaultConfig(context.Background(), loadOpts...)
	if err != nil {
		return nil, fmt.Errorf("s3 storage: load aws config: %w", err)
	}

	clientOpts := func(o *s3.Options) {
		if cfg.Endpoint != "" {
			// Custom endpoint (MinIO / R2 / Wasabi). Quote the value
			// so the SDK doesn't try to interpret it as a region.
			o.BaseEndpoint = aws.String(cfg.Endpoint)
		}
		if cfg.ForcePathStyle {
			o.UsePathStyle = true
		}
	}
	client := s3.NewFromConfig(awsCfg, clientOpts)
	return &S3Backend{
		bucket:  cfg.Bucket,
		client:  client,
		presign: s3.NewPresignClient(client),
	}, nil
}

func (b *S3Backend) Upload(
	ctx context.Context, key string, body io.Reader, contentType string, size int64,
) error {
	input := &s3.PutObjectInput{
		Bucket: aws.String(b.bucket),
		Key:    aws.String(key),
		Body:   body,
	}
	if contentType != "" {
		input.ContentType = aws.String(contentType)
	}
	if size > 0 {
		input.ContentLength = aws.Int64(size)
	}
	if _, err := b.client.PutObject(ctx, input); err != nil {
		return fmt.Errorf("s3 put: %w", err)
	}
	return nil
}

func (b *S3Backend) Download(
	ctx context.Context, key string,
) (io.ReadCloser, int64, string, error) {
	out, err := b.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(b.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		if isS3NotFound(err) {
			return nil, 0, "", ErrNotFound
		}
		return nil, 0, "", fmt.Errorf("s3 get: %w", err)
	}
	size := int64(0)
	if out.ContentLength != nil {
		size = *out.ContentLength
	}
	ct := ""
	if out.ContentType != nil {
		ct = *out.ContentType
	}
	return out.Body, size, ct, nil
}

func (b *S3Backend) Delete(ctx context.Context, key string) error {
	if _, err := b.client.DeleteObject(ctx, &s3.DeleteObjectInput{
		Bucket: aws.String(b.bucket),
		Key:    aws.String(key),
	}); err != nil {
		return fmt.Errorf("s3 delete: %w", err)
	}
	return nil
}

func (b *S3Backend) Exists(ctx context.Context, key string) (bool, error) {
	_, err := b.client.HeadObject(ctx, &s3.HeadObjectInput{
		Bucket: aws.String(b.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		if isS3NotFound(err) {
			return false, nil
		}
		return false, fmt.Errorf("s3 head: %w", err)
	}
	return true, nil
}

// GeneratePresignedURL produces a short-lived GET URL the browser
// can hit directly. We default the response Content-Disposition to
// the object's stored content-type so a presigned download starts
// playing / displaying inline when applicable.
func (b *S3Backend) GeneratePresignedURL(
	ctx context.Context, key string, expiry time.Duration,
) (string, error) {
	if expiry <= 0 {
		expiry = 15 * time.Minute
	}
	req, err := b.presign.PresignGetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(b.bucket),
		Key:    aws.String(key),
	}, func(o *s3.PresignOptions) {
		o.Expires = expiry
	})
	if err != nil {
		return "", fmt.Errorf("s3 presign: %w", err)
	}
	return req.URL, nil
}

// OpenLocalCopy downloads the object into a temp file and returns
// its path. The Python text extractor needs a real path on disk;
// streaming a reader isn't enough. Cleanup must always be called.
func (b *S3Backend) OpenLocalCopy(
	ctx context.Context, key string,
) (string, func(), error) {
	body, _, _, err := b.Download(ctx, key)
	if err != nil {
		return "", func() {}, err
	}
	defer body.Close()

	// Preserve the original extension so the Python extractor can
	// dispatch on it.
	ext := keyExtension(key)
	tmp, err := os.CreateTemp("", "kbcopy-*"+ext)
	if err != nil {
		return "", func() {}, fmt.Errorf("s3 tempfile: %w", err)
	}
	if _, err := io.Copy(tmp, body); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmp.Name())
		return "", func() {}, fmt.Errorf("s3 tempfile write: %w", err)
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmp.Name())
		return "", func() {}, fmt.Errorf("s3 tempfile close: %w", err)
	}
	cleanup := func() { _ = os.Remove(tmp.Name()) }
	return tmp.Name(), cleanup, nil
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// isS3NotFound checks the typed S3 errors AND the generic smithy
// API error code. HeadObject returns the typed ``NotFound``;
// GetObject returns ``NoSuchKey``.
func isS3NotFound(err error) bool {
	var notFound *s3types.NotFound
	if errors.As(err, &notFound) {
		return true
	}
	var noSuchKey *s3types.NoSuchKey
	if errors.As(err, &noSuchKey) {
		return true
	}
	// Smithy wraps the wire-level 404 as an APIError; match by code
	// so we still detect minio's flavour ("NoSuchKey").
	type apiError interface {
		ErrorCode() string
	}
	var apiErr apiError
	if errors.As(err, &apiErr) {
		code := apiErr.ErrorCode()
		if code == "NotFound" || code == "NoSuchKey" {
			return true
		}
	}
	return false
}

// keyExtension extracts the trailing ".ext" from a key, defaulting
// to empty string when there is none. Used so temp files keep the
// suffix the extractor expects.
func keyExtension(key string) string {
	// Strip any path component first.
	u, err := url.Parse(key)
	if err == nil && u.Path != "" {
		key = u.Path
	}
	idx := strings.LastIndex(key, ".")
	if idx == -1 {
		return ""
	}
	ext := key[idx:]
	if len(ext) > 8 {
		// Suspiciously long — probably not a real extension. Drop it.
		return ""
	}
	return ext
}

