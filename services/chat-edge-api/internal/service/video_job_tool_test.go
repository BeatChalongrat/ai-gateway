package service

import (
	"context"
	"fmt"
	"github.com/google/uuid"
	"strings"
	"testing"
)

type fakeVideoJobProxy struct {
	unconfirmedVideo
	id    string
	calls []string
}

func (f *fakeVideoJobProxy) ProxyPublicMedia(_ context.Context, _ GatewayCallContext, method, path string, _ []byte) (int, []byte, error) {
	f.calls = append(f.calls, method+" "+path)
	if strings.Contains(path, "?conversation_id=") {
		return 200, []byte(fmt.Sprintf(`{"jobs":[{"job_id":%q}]}`, f.id)), nil
	}
	return 200, []byte(`{"status":"cancelled"}`), nil
}
func TestVideoJobToolConversationAndConfirmationBoundary(t *testing.T) {
	id, other := uuid.NewString(), uuid.NewString()
	fake := &fakeVideoJobProxy{id: id}
	e := NewToolExecutor(nil)
	e.videoGen = fake
	ec := ToolExecContext{ConversationID: uuid.New(), TenantID: uuid.New(), UserID: uuid.New()}
	e.execVideoJob(context.Background(), fmt.Sprintf(`{"job_id":%q,"action":"confirm"}`, id), ec)
	if len(fake.calls) != 0 {
		t.Fatal("agent confirmation reached service")
	}
	e.execVideoJob(context.Background(), fmt.Sprintf(`{"job_id":%q,"action":"cancel"}`, other), ec)
	if len(fake.calls) != 1 {
		t.Fatal("cross-conversation cancel reached service")
	}
	result := e.execVideoJob(context.Background(), fmt.Sprintf(`{"job_id":%q,"action":"cancel"}`, id), ec)
	if !strings.Contains(result, "cancelled") || fake.calls[2] != "POST /jobs/"+id+"/cancel" {
		t.Fatalf("%s %+v", result, fake.calls)
	}
}
