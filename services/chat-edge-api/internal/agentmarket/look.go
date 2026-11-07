package agentmarket

import (
	"bytes"
	"context"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"path"
	"regexp"
	"strings"
	"unicode/utf8"
)

// ── How an agent looks (AM10) ─────────────────────────────────────────────
//
// The icon ships inside the agent package, so a third-party agent brings its
// own and the catalogue never has to know about it. What a package declares
// (docs/agent-market/ARCHITECTURE.md, "The icon"):
//
//	"icon_file": "icon.svg"                       — a file of the package
//	"accent":    {"light": "#1D4ED8", "dark": "#8FB0FF"}
//
// The file is a shape, not a picture: an SVG of viewBox 0 0 24 24 made of
// strokes (the set's line weight is 1.75, round caps and joins), at most
// MaxIconBytes, with nothing a browser could run or fetch. The clients never
// draw its colours — they paint the shape with the accent of the theme in use
// (a CSS mask), so one line icon reads the same at 32 px in the sidebar and at
// full size on the card, light and dark, and every agent of the catalogue is
// one visual language with its own colour.
//
// The runner reads the file at registration, checks it (CheckIcon) and writes
// it into the released definition's manifest as "icon_svg"; a package must not
// set that key itself. "icon" (a glyph name) stays as the fallback for an
// agent that ships no file.

// MaxIconBytes bounds an icon file.
const MaxIconBytes = 4096

// Accent is the agent's colour, one per theme.
type Accent struct {
	Light string `json:"light"`
	Dark  string `json:"dark"`
}

// Look is what a client needs to draw an agent: the shape and the colour.
type Look struct {
	IconSVG string  `json:"icon_svg,omitempty"`
	Accent  *Accent `json:"accent,omitempty"`
}

var hexColour = regexp.MustCompile(`^#[0-9A-Fa-f]{6}$`)

// CheckAccent: both themes, as six-digit hex.
func CheckAccent(a *Accent) error {
	if a == nil {
		return nil
	}
	if !hexColour.MatchString(a.Light) || !hexColour.MatchString(a.Dark) {
		return errors.New("accent: light and dark are each a #RRGGBB colour")
	}
	return nil
}

// CheckIconFile: a relative path to an .svg inside the package.
func CheckIconFile(name string) error {
	if name == "" {
		return nil
	}
	clean := path.Clean("/" + strings.ReplaceAll(name, "\\", "/"))
	if clean != "/"+name || strings.Contains(name, "..") || !strings.HasSuffix(strings.ToLower(name), ".svg") {
		return fmt.Errorf("icon_file %q: a relative path to an .svg file inside the package", name)
	}
	return nil
}

var (
	iconElements = map[string]bool{"svg": true, "g": true, "path": true, "circle": true, "ellipse": true, "rect": true,
		"line": true, "polyline": true, "polygon": true, "title": true}
	iconAttrs = map[string]bool{"xmlns": true, "viewBox": true, "width": true, "height": true, "fill": true, "stroke": true,
		"stroke-width": true, "stroke-linecap": true, "stroke-linejoin": true, "stroke-miterlimit": true,
		"stroke-dasharray": true, "fill-rule": true, "clip-rule": true, "opacity": true, "fill-opacity": true,
		"stroke-opacity": true, "transform": true, "d": true, "cx": true, "cy": true, "r": true, "rx": true, "ry": true,
		"x": true, "y": true, "x1": true, "y1": true, "x2": true, "y2": true, "points": true}
)

// CheckIcon accepts an icon a client may put straight into a page: UTF-8,
// at most MaxIconBytes, one <svg viewBox="0 0 24 24"> of shapes only — no
// script, style, text, image, link, use, foreignObject, no event or URL
// attribute, no entity or directive. Nothing else is read into the
// definition.
func CheckIcon(svg []byte) error {
	if len(svg) == 0 {
		return errors.New("icon: empty")
	}
	if len(svg) > MaxIconBytes {
		return fmt.Errorf("icon: %d bytes, at most %d", len(svg), MaxIconBytes)
	}
	if !utf8.Valid(svg) {
		return errors.New("icon: not UTF-8")
	}
	dec := xml.NewDecoder(bytes.NewReader(svg))
	dec.Strict = true
	dec.Entity = map[string]string{} // no entities at all
	var stack []string
	root := false
	for {
		tok, err := dec.Token()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return fmt.Errorf("icon: %w", err)
		}
		switch t := tok.(type) {
		case xml.StartElement:
			name := t.Name.Local
			if !iconElements[name] {
				return fmt.Errorf("icon: element <%s> is not allowed", name)
			}
			if len(stack) == 0 {
				if name != "svg" || root {
					return errors.New("icon: one <svg> root")
				}
				root = true
				if vb := attr(t, "viewBox"); strings.Join(strings.Fields(vb), " ") != "0 0 24 24" {
					return fmt.Errorf("icon: viewBox %q, the set is 0 0 24 24", vb)
				}
			}
			for _, a := range t.Attr {
				n := a.Name.Local
				if a.Name.Space == "xmlns" || (a.Name.Space == "" && n == "xmlns") {
					continue
				}
				if a.Name.Space != "" || !iconAttrs[n] || strings.HasPrefix(strings.ToLower(n), "on") {
					return fmt.Errorf("icon: attribute %q is not allowed", n)
				}
				v := strings.ToLower(a.Value)
				if strings.Contains(v, "url(") || strings.Contains(v, "javascript:") || strings.Contains(v, "data:") || strings.Contains(v, "<") {
					return fmt.Errorf("icon: attribute %q carries a reference", n)
				}
			}
			stack = append(stack, name)
		case xml.EndElement:
			stack = stack[:len(stack)-1]
		case xml.CharData:
			if strings.TrimSpace(string(t)) != "" && (len(stack) == 0 || stack[len(stack)-1] != "title") {
				return errors.New("icon: text outside <title>")
			}
		case xml.Directive:
			return errors.New("icon: directives are not allowed")
		case xml.ProcInst:
			if t.Target != "xml" {
				return fmt.Errorf("icon: processing instruction <?%s?> is not allowed", t.Target)
			}
		}
	}
	if !root {
		return errors.New("icon: no <svg> root")
	}
	return nil
}

func attr(e xml.StartElement, name string) string {
	for _, a := range e.Attr {
		if a.Name.Local == name {
			return a.Value
		}
	}
	return ""
}

// ReleaseManifest is the manifest a registered version carries: the package's
// agent.json with its checked icon written in as "icon_svg".
func ReleaseManifest(raw json.RawMessage, iconSVG string) (json.RawMessage, error) {
	if iconSVG == "" {
		return raw, nil
	}
	var m map[string]json.RawMessage
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	if _, ok := m["icon_svg"]; ok {
		return nil, errors.New("manifest: icon_svg is written by the runner from icon_file; a package does not set it")
	}
	b, _ := json.Marshal(strings.TrimSpace(iconSVG))
	m["icon_svg"] = b
	return json.Marshal(m)
}

// LookOf reads a released manifest's look.
func LookOf(raw json.RawMessage) Look {
	var m struct {
		IconSVG string  `json:"icon_svg"`
		Accent  *Accent `json:"accent"`
	}
	_ = json.Unmarshal(raw, &m)
	if CheckAccent(m.Accent) != nil {
		m.Accent = nil
	}
	if m.IconSVG != "" && CheckIcon([]byte(m.IconSVG)) != nil {
		m.IconSVG = ""
	}
	return Look{IconSVG: m.IconSVG, Accent: m.Accent}
}

// lookSQL picks a slug's look for a row joined to its own definition d: that
// version's, else the newest released version's that has an icon — an install
// pinned to a version from before the package had one shows the agent's icon
// too (presentation, not behaviour).
const lookSQL = `COALESCE(d.manifest->'icon_svg', (SELECT n.manifest->'icon_svg' FROM agent_market.agent_definitions n
			WHERE n.slug = d.slug AND n.manifest ? 'icon_svg' ORDER BY n.created_at DESC LIMIT 1), 'null'::jsonb),
		COALESCE(d.manifest->'accent', (SELECT n.manifest->'accent' FROM agent_market.agent_definitions n
			WHERE n.slug = d.slug AND n.manifest ? 'accent' ORDER BY n.created_at DESC LIMIT 1), 'null'::jsonb)`

// scanLook reads the two lookSQL columns.
func scanLook(iconSVG, accent json.RawMessage) Look {
	var l Look
	var s string
	if json.Unmarshal(iconSVG, &s) == nil && s != "" && CheckIcon([]byte(s)) == nil {
		l.IconSVG = s
	}
	var a Accent
	if json.Unmarshal(accent, &a) == nil && CheckAccent(&a) == nil && a.Light != "" {
		l.Accent = &a
	}
	return l
}

// LookForSlug: the newest released version's look for a slug.
func (s *Store) LookForSlug(ctx context.Context, slug string) Look {
	var icon, accent json.RawMessage
	err := s.Pool.QueryRow(ctx, `SELECT `+lookSQL+` FROM (SELECT slug, manifest FROM agent_market.agent_definitions
		WHERE slug = $1 ORDER BY created_at DESC LIMIT 1) d`, slug).Scan(&icon, &accent)
	if err != nil {
		return Look{}
	}
	return scanLook(icon, accent)
}
