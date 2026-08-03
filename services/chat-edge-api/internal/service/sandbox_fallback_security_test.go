package service

import (
	"context"
	"errors"
	"testing"
)

func TestDocGeneratorFailsClosedWithoutSandbox(t *testing.T) {
	service := &DocService{}

	_, err := service.runGenerator(
		context.Background(),
		docxGeneratorScript,
		[]byte(`{"title":"synthetic"}`),
		"plan.json",
		"",
		"output.docx",
		"docx",
	)

	if !errors.Is(err, ErrDocSandboxUnavailable) {
		t.Fatalf("expected ErrDocSandboxUnavailable, got %v", err)
	}
}

func TestExcelExecutorFailsClosedWithoutSandbox(t *testing.T) {
	service := &ExcelService{}

	_, err := service.runExecutor(
		context.Background(),
		[]byte(`{"operations":[]}`),
		"plan.json",
		"source.xlsx",
		"output.xlsx",
	)

	if !errors.Is(err, ErrExcelSandboxUnavailable) {
		t.Fatalf("expected ErrExcelSandboxUnavailable, got %v", err)
	}
}

func TestDocInspectorFailsClosedWithoutSandbox(t *testing.T) {
	service := &DocService{}

	_, err := service.runReadOnlyScript(
		context.Background(),
		docxInspectorScript,
		"untrusted.docx",
		docInspectorTimeout,
	)

	if !errors.Is(err, ErrDocSandboxUnavailable) {
		t.Fatalf("expected ErrDocSandboxUnavailable, got %v", err)
	}
}

func TestExcelInspectorFailsClosedWithoutSandbox(t *testing.T) {
	service := &ExcelService{}

	_, err := service.runReadOnlyScript(
		context.Background(),
		inspectorScript,
		"untrusted.xlsx",
		inspectorTimeout,
	)

	if !errors.Is(err, ErrExcelSandboxUnavailable) {
		t.Fatalf("expected ErrExcelSandboxUnavailable, got %v", err)
	}
}
