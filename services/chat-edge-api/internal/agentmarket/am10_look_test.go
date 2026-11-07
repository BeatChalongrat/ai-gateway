package agentmarket

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// ── AM10: the icon ships inside the package ──────────────────────────────

const goodIcon = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h16"/><circle cx="12" cy="9" r="3"/><title>chart</title></svg>`

func TestAnIconIsAShapeAndNothingAClientCouldRun(t *testing.T) {
	if err := CheckIcon([]byte(goodIcon)); err != nil {
		t.Fatalf("the set's own icon: %v", err)
	}
	bad := map[string]string{
		"script":        `<svg viewBox="0 0 24 24"><script>alert(1)</script></svg>`,
		"foreignObject": `<svg viewBox="0 0 24 24"><foreignObject><div/></foreignObject></svg>`,
		"image":         `<svg viewBox="0 0 24 24"><image href="https://x/y.png"/></svg>`,
		"use":           `<svg viewBox="0 0 24 24"><use href="#a"/></svg>`,
		"style":         `<svg viewBox="0 0 24 24"><style>path{fill:url(x)}</style></svg>`,
		"text":          `<svg viewBox="0 0 24 24"><text>hi</text></svg>`,
		"onload":        `<svg viewBox="0 0 24 24" onload="x()"><path d="M0 0"/></svg>`,
		"href":          `<svg viewBox="0 0 24 24"><a href="x"><path d="M0 0"/></a></svg>`,
		"xlink":         `<svg viewBox="0 0 24 24" xmlns:xlink="http://www.w3.org/1999/xlink"><path xlink:href="#a" d="M0 0"/></svg>`,
		"url()":         `<svg viewBox="0 0 24 24"><path d="M0 0" fill="url(#g)"/></svg>`,
		"style attr":    `<svg viewBox="0 0 24 24"><path d="M0 0" style="fill:red"/></svg>`,
		"doctype":       `<!DOCTYPE svg [<!ENTITY x "y">]><svg viewBox="0 0 24 24"><path d="M0 0"/></svg>`,
		"viewBox":       `<svg viewBox="0 0 48 48"><path d="M0 0"/></svg>`,
		"no viewBox":    `<svg><path d="M0 0"/></svg>`,
		"two roots":     `<svg viewBox="0 0 24 24"/><svg viewBox="0 0 24 24"/>`,
		"not svg":       `<div/>`,
		"loose text":    `<svg viewBox="0 0 24 24">hello<path d="M0 0"/></svg>`,
		"empty":         ``,
		"too big":       `<svg viewBox="0 0 24 24"><path d="M0 0` + strings.Repeat(" l1 1", 1200) + `"/></svg>`,
	}
	for name, svg := range bad {
		if err := CheckIcon([]byte(svg)); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}

func TestTheManifestDeclaresTheIconFileAndAnAccent(t *testing.T) {
	ok := manifestWith(t, func(m map[string]any) {
		m["icon_file"] = "icon.svg"
		m["accent"] = map[string]any{"light": "#1D4ED8", "dark": "#8FB0FF"}
	})
	m, err := ParseManifest(ok)
	if err != nil || m.IconFile != "icon.svg" || m.Accent == nil || m.Accent.Dark != "#8FB0FF" {
		t.Fatalf("declared look not read: %+v %v", m.Accent, err)
	}
	for name, edit := range map[string]func(m map[string]any){
		"icon outside the package": func(m map[string]any) { m["icon_file"] = "../engine/icon.svg" },
		"icon not an svg":          func(m map[string]any) { m["icon_file"] = "icon.png" },
		"accent not a colour":      func(m map[string]any) { m["accent"] = map[string]any{"light": "blue", "dark": "#8FB0FF"} },
		"accent half declared":     func(m map[string]any) { m["accent"] = map[string]any{"light": "#1D4ED8"} },
	} {
		if _, err := ParseManifest(manifestWith(t, edit)); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	// a package does not write icon_svg itself: the runner does, from the file
	if _, err := ReleaseManifest(manifestWith(t, func(m map[string]any) { m["icon_svg"] = goodIcon }), goodIcon); err == nil {
		t.Error("a manifest carrying icon_svg was released")
	}
	rel, err := ReleaseManifest(ok, goodIcon)
	if err != nil {
		t.Fatal(err)
	}
	look := LookOf(rel)
	if look.IconSVG != goodIcon || look.Accent == nil || look.Accent.Light != "#1D4ED8" {
		t.Fatalf("the released manifest does not carry the look: %+v", look)
	}
	if got, _ := ReleaseManifest(ok, ""); string(got) != string(ok) {
		t.Error("no icon file: the manifest is released as it is")
	}
	// an upgrade's diff never lists the icon's markup
	for _, c := range VersionDiff(Thai, Definition{Manifest: ok}, Definition{Manifest: rel}) {
		if c.Key == "icon_svg" {
			t.Errorf("the upgrade diff lists the icon markup: %+v", c)
		}
	}
}

// Every agent the product ships brings its own icon, in the set's language,
// with its own colour — no two share either, and each colour stands out on
// the surface it is drawn on, light and dark.
func TestEveryPackageShipsItsOwnIconAndColour(t *testing.T) {
	files, _ := filepath.Glob(filepath.Join("..", "..", "..", "..", "backend", "agent_market", "packages", "*", "agent.json"))
	if len(files) < 5 {
		t.Fatalf("the packages were not found: %v", files)
	}
	icons, accents := map[string]string{}, map[string]string{}
	for _, f := range files {
		raw, _ := os.ReadFile(f)
		m, err := ParseManifest(raw)
		if err != nil {
			t.Fatal(err)
		}
		if m.IconFile == "" || m.Accent == nil {
			t.Errorf("%s: declares no icon_file / accent", f)
			continue
		}
		svg, err := os.ReadFile(filepath.Join(filepath.Dir(f), m.IconFile))
		if err != nil {
			t.Errorf("%s: %v", f, err)
			continue
		}
		if err := CheckIcon(svg); err != nil {
			t.Errorf("%s: %v", f, err)
		}
		s := string(svg)
		for _, want := range []string{`viewBox="0 0 24 24"`, `stroke-width="1.75"`, `stroke-linecap="round"`, `stroke-linejoin="round"`, `fill="none"`} {
			if !strings.Contains(s, want) {
				t.Errorf("%s: %s is not in the set's language (%s)", f, m.IconFile, want)
			}
		}
		if other, dup := icons[s]; dup {
			t.Errorf("%s shares its icon with %s", f, other)
		}
		icons[s] = f
		for _, a := range []string{m.Accent.Light, m.Accent.Dark} {
			if other, dup := accents[strings.ToUpper(a)]; dup {
				t.Errorf("%s shares the colour %s with %s", f, a, other)
			}
			accents[strings.ToUpper(a)] = f
		}
		// non-text contrast (WCAG 1.4.11, 3:1) against the app's surfaces
		if c := contrast(m.Accent.Light, "#FFFFFF"); c < 3 {
			t.Errorf("%s: light accent %s on white is %.2f:1", f, m.Accent.Light, c)
		}
		if c := contrast(m.Accent.Dark, "#17181C"); c < 3 {
			t.Errorf("%s: dark accent %s on the dark surface is %.2f:1", f, m.Accent.Dark, c)
		}
	}
}

func TestTheRailAndTheInstallsCarryTheLook(t *testing.T) {
	// the JSON shape the clients read: icon_svg and accent beside the glyph name
	b, _ := json.Marshal(RailInstall{Icon: "chart", Look: Look{IconSVG: goodIcon, Accent: &Accent{Light: "#1D4ED8", Dark: "#8FB0FF"}}})
	var m map[string]any
	_ = json.Unmarshal(b, &m)
	if m["icon_svg"] != goodIcon || m["accent"].(map[string]any)["dark"] != "#8FB0FF" || m["icon"] != "chart" {
		t.Fatalf("rail row: %s", b)
	}
	if l := scanLook(json.RawMessage(`"<svg onload=x()/>"`), json.RawMessage(`{"light":"#1D4ED8","dark":"#8FB0FF"}`)); l.IconSVG != "" || l.Accent == nil {
		t.Fatalf("a row with a bad icon keeps its accent and drops the icon: %+v", l)
	}
	if l := scanLook(json.RawMessage(`null`), json.RawMessage(`null`)); l.IconSVG != "" || l.Accent != nil {
		t.Fatalf("no look: %+v", l)
	}
}

func contrast(hexA, hexB string) float64 {
	la, lb := luminance(hexA), luminance(hexB)
	if la < lb {
		la, lb = lb, la
	}
	return (la + 0.05) / (lb + 0.05)
}

func luminance(hex string) float64 {
	v := func(i int) float64 {
		n, _ := strconv.ParseInt(hex[i:i+2], 16, 0)
		c := float64(n) / 255
		if c <= 0.03928 {
			return c / 12.92
		}
		return math.Pow((c+0.055)/1.055, 2.4)
	}
	return 0.2126*v(1) + 0.7152*v(3) + 0.0722*v(5)
}
