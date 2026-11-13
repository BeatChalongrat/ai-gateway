package service

import (
	"context"
	"encoding/json"
	"github.com/google/uuid"
	"net/http"
)

type videoJobProxy interface {
	ProxyPublicMedia(context.Context, GatewayCallContext, string, string, []byte) (int, []byte, error)
}

func videoJobToolDef() GatewayTool {
	return GatewayTool{Type: "function", Function: GatewayToolFunction{Name: "video_job", Description: "Read status or cancel a recent video job in this conversation. Finished output is available in the Video jobs panel. Agents cannot confirm costs; only the user can approve pending jobs.", Parameters: map[string]any{"type": "object", "properties": map[string]any{"job_id": map[string]any{"type": "string"}, "action": map[string]any{"type": "string", "enum": []string{"status", "cancel"}}}, "required": []string{"job_id", "action"}}}}
}

func (e *ToolExecutor) execVideoJob(ctx context.Context, args string, ec ToolExecContext) string {
	proxy, ok := e.videoGen.(videoJobProxy)
	if !ok {
		return "Video job service unavailable."
	}
	var req struct {
		ID     string `json:"job_id"`
		Action string `json:"action"`
	}
	if json.Unmarshal([]byte(args), &req) != nil || (req.Action != "status" && req.Action != "cancel") {
		return "Invalid video job action."
	}
	id, err := uuid.Parse(req.ID)
	if err != nil {
		return "Invalid video job ID."
	}
	call := GatewayCallContext{TenantID: ec.TenantID, UserID: ec.UserID, ConversationID: ec.ConversationID}
	status, body, err := proxy.ProxyPublicMedia(ctx, call, http.MethodGet, "/jobs?conversation_id="+ec.ConversationID.String(), nil)
	if err != nil || status != 200 {
		return "Could not read this conversation's video jobs."
	}
	var list struct {
		Jobs []struct {
			ID string `json:"job_id"`
		} `json:"jobs"`
	}
	if json.Unmarshal(body, &list) != nil {
		return "Invalid video job list."
	}
	found := false
	for _, job := range list.Jobs {
		if job.ID == id.String() {
			found = true
		}
	}
	if !found {
		return "Video job is not in this conversation's recent jobs."
	}
	method, path := http.MethodGet, "/jobs/"+id.String()
	if req.Action == "cancel" {
		method = http.MethodPost
		path += "/cancel"
	}
	status, body, err = proxy.ProxyPublicMedia(ctx, call, method, path, nil)
	if err != nil || status != 200 {
		return "Video job request failed; check the Video jobs panel."
	}
	return string(body)
}
