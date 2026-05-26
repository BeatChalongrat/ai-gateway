// TitleService computes the DLP-sanitized conversation title.
//
// The chat UI shows the title in the sidebar. Raw user prompts may
// contain PII (an email, a name), and we never want that in the
// sidebar list — that view is non-restoring by design. So we route
// the candidate title through Gateway's DLP scan and store the
// pseudonymized form. Session 3 fills in the call; Phase 1 just
// pins the dependency.
package service

type TitleService struct {
	gateway *GatewayClient
}

func NewTitleService(g *GatewayClient) *TitleService {
	return &TitleService{gateway: g}
}
