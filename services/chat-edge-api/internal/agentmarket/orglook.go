package agentmarket

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"regexp"
	"strings"

	"github.com/jackc/pgx/v5"
)

// ── the organisation's look: an admin's setting, audited (AM21 §6.2, §6.3) ──
//
// A product has several complete themes a customer picks from; the choice
// re-skins every deliverable of every agent and touches no content. The
// organisation's own brand — its name on every document, a primary and an
// accent colour — is laid over the theme. The name may not carry a build or
// card marker ("[AM1b]" sat on the owner's own report for a month), and a
// colour that fails contrast against what it sits on is refused here, with
// the ratio, so the document stays readable. The choice is written to the
// admin audit log like every other admin setting.
//
//	GET /admin/agent-market/look   the theme in force, its version, the presets
//	PUT /admin/agent-market/look   {id, company, primary?, accent?}

// LookPreset is one built-in theme as the admin app shows it: its colours, to pick by eye. (The themes
// themselves are the renderer's: backend/agent_market/deliverables/look/look.json.)
type LookPreset struct {
	ID      string     `json:"id"`
	Name    PersonText `json:"name"` // said in the language of the request that reads it (AM11)
	Primary string     `json:"primary"`
	Accent  string     `json:"accent"`
	Surface string     `json:"surface"`
}

// lookPresets are built when asked, not at package level: a sentence (Text) is made only once the
// English catalogue is registered (AM11).
func lookPresets() []LookPreset {
	return []LookPreset{
		{ID: "navy", Name: Text("กรมท่า"), Primary: "12264A", Accent: "2F6FD6", Surface: "F3F5F9"},
		{ID: "teal", Name: Text("เขียวหยก"), Primary: "0F4C4C", Accent: "1C8C84", Surface: "F2F7F6"},
		{ID: "charcoal", Name: Text("ถ่านหิน"), Primary: "24272E", Accent: "D9642B", Surface: "F4F4F5"},
	}
}

// lookContrastMin is the least contrast a brand colour must have against what it sits on (WCAG AA).
const lookContrastMin = 4.5

// lookCompanyMax is the longest an organisation's name on a document may be.
const lookCompanyMax = 120

var hex6 = regexp.MustCompile(`^#?[0-9A-Fa-f]{6}$`)

// LookPatch is what an admin may set.
type LookPatch struct {
	ID      string  `json:"id"`
	Company string  `json:"company"`
	Primary *string `json:"primary,omitempty"`
	Accent  *string `json:"accent,omitempty"`
}

// lookTheme is the theme as the registry keeps it (the renderer reads id, company and palette).
type lookTheme struct {
	ID      string            `json:"id"`
	Company string            `json:"company,omitempty"`
	Palette map[string]string `json:"palette,omitempty"`
}

func lookPreset(id string) *LookPreset {
	all := lookPresets()
	for i := range all {
		if all[i].ID == id {
			return &all[i]
		}
	}
	return nil
}

// lookLuminance and lookContrast are WCAG's, as the renderer computes them (look/tokens.py).
func lookLuminance(hex string) float64 {
	hex = strings.TrimPrefix(hex, "#")
	ch := func(i int) float64 {
		var v int
		fmt.Sscanf(hex[i:i+2], "%02x", &v)
		c := float64(v) / 255
		if c <= 0.03928 {
			return c / 12.92
		}
		return math.Pow((c+0.055)/1.055, 2.4)
	}
	return 0.2126*ch(0) + 0.7152*ch(2) + 0.0722*ch(4)
}

func lookContrast(a, b string) float64 {
	la, lb := lookLuminance(a), lookLuminance(b)
	hi, lo := math.Max(la, lb), math.Min(la, lb)
	return math.Round((hi+0.05)/(lo+0.05)*100) / 100
}

// lookRefusal says why a patch cannot be taken — a text for a person, in the request's language — or
// an empty text.
func lookRefusal(p LookPatch) PersonText {
	if lookPreset(p.ID) == nil {
		return Text("ธีมต้องเป็นหนึ่งในธีมที่มี: กรมท่า เขียวหยก ถ่านหิน")
	}
	if len([]rune(p.Company)) > lookCompanyMax {
		return Text("ชื่อองค์กรบนเอกสารต้องไม่เกิน %s ตัวอักษร", Count(lookCompanyMax))
	}
	if m := MarkerIn(p.Company); m != "" {
		return Text("ชื่อองค์กรบนเอกสารต้องไม่มีรหัสงานหรือรหัสรุ่น เช่น %s — เอกสารนี้ผู้ตรวจสอบของลูกค้าจะอ่าน", Typed(m, 24))
	}
	for _, c := range []struct {
		what  string
		value *string
	}{{"primary", p.Primary}, {"accent", p.Accent}} {
		if c.value == nil || strings.TrimSpace(*c.value) == "" {
			continue
		}
		what := c.what
		if !hex6.MatchString(strings.TrimSpace(*c.value)) {
			return Text("สี %s ต้องเป็นรหัสฐานสิบหก 6 หลัก เช่น 12264A", Each(func(Lang) string { return what }))
		}
		v := strings.ToUpper(strings.TrimPrefix(strings.TrimSpace(*c.value), "#"))
		if r := lookContrast(v, "FFFFFF"); r < lookContrastMin {
			return Text("สี %s %s มีคอนทราสต์ %s กับพื้นขาว ต่ำกว่า %s ที่อ่านได้ จึงรับไม่ได้",
				Each(func(Lang) string { return what }), Each(func(Lang) string { return v }),
				Number(r), Number(lookContrastMin))
		}
	}
	return PersonText{}
}

func (p LookPatch) theme() lookTheme {
	t := lookTheme{ID: p.ID, Company: strings.TrimSpace(p.Company)}
	for _, c := range []struct {
		what  string
		value *string
	}{{"primary", p.Primary}, {"accent", p.Accent}} {
		if c.value != nil && strings.TrimSpace(*c.value) != "" {
			if t.Palette == nil {
				t.Palette = map[string]string{}
			}
			t.Palette[c.what] = strings.ToUpper(strings.TrimPrefix(strings.TrimSpace(*c.value), "#"))
		}
	}
	return t
}

func (a *API) adminLook(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	c, _ := caller(r)
	raw, version, logo, err := a.store.LatestTheme(r.Context(), c.TenantUUID)
	if err != nil {
		a.fail(w, err, "look")
		return
	}
	var t lookTheme
	if len(raw) > 0 {
		_ = json.Unmarshal(raw, &t)
	}
	if t.ID == "" {
		t.ID = lookPresets()[0].ID
	}
	writeJSON(w, http.StatusOK, map[string]any{"theme": t, "version": version, "logo": logo != nil, "presets": lookPresets(),
		// the one file a person opens to change the look of everything (said on the page)
		"file": "backend/agent_market/deliverables/look/look.json"})
}

func (a *API) adminPutLook(w http.ResponseWriter, r *http.Request) {
	if !a.requireAdmin(w, r) {
		return
	}
	c, _ := caller(r)
	var body LookPatch
	dec := json.NewDecoder(io.LimitReader(r.Body, 1<<14))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	if why := lookRefusal(body); !why.Empty() {
		say(w, http.StatusBadRequest, "BAD_VALUE", "detail", why.In(langOfWriter(w)))
		return
	}
	after := body.theme()
	ctx := r.Context()
	var version int
	err := a.audited(ctx, func(tx pgx.Tx) ([]AuditEntry, error) {
		raw, _, logo, err := a.store.LatestTheme(ctx, c.TenantUUID)
		if err != nil {
			return nil, err
		}
		var before lookTheme
		if len(raw) > 0 {
			_ = json.Unmarshal(raw, &before)
		}
		data, _ := json.Marshal(after)
		if err := tx.QueryRow(ctx, `INSERT INTO agent_market.agent_themes (tenant_id, version, theme, logo_key, created_by)
			VALUES ($1, COALESCE((SELECT max(version) FROM agent_market.agent_themes WHERE tenant_id = $1), 0) + 1, $2, $3, $4)
			RETURNING version`, c.TenantUUID, data, logo, c.UserID).Scan(&version); err != nil {
			return nil, err
		}
		return []AuditEntry{entry(r, "agent_market.org.look", "agent_market_theme", c.TenantUUID.String(),
			map[string]any{"from": before, "to": after, "version": version})}, nil
	})
	if err != nil {
		a.fail(w, err, "look")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"theme": after, "version": version, "presets": lookPresets})
}

// themeCompanyRefusal (the chat app's own theme setting, PUT /theme) applies the same rule to the name
// a person types: no marker on a customer's document.
func themeCompanyRefusal(body json.RawMessage) PersonText {
	var t struct {
		Company string `json:"company"`
	}
	_ = json.Unmarshal(body, &t)
	if m := MarkerIn(t.Company); m != "" {
		return Text("ชื่อองค์กรบนเอกสารต้องไม่มีรหัสงานหรือรหัสรุ่น เช่น %s", Typed(m, 24))
	}
	return PersonText{}
}
