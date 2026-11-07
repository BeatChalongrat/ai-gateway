package config

import "testing"

const strong = "a-32-byte-or-longer-production-secret-value"

func base(env string) *Config {
	return &Config{
		AppEnv:           env,
		PortalJWTSecret:  strong,
		ServiceJWTSecret: strong,
		CookieSecure:     true,
	}
}

func TestValidate_DevAllowsInsecureDefaults(t *testing.T) {
	c := &Config{
		AppEnv:           "development",
		PortalJWTSecret:  "dev-stable-portal-key-change-in-prod",
		ServiceJWTSecret: "dev-service-jwt-secret-change-in-prod",
		CookieSecure:     false,
	}
	if err := c.Validate(); err != nil {
		t.Fatalf("dev should tolerate defaults, got: %v", err)
	}
}

func TestValidate_ProdRejectsDevDefaultSecret(t *testing.T) {
	c := base("production")
	c.PortalJWTSecret = "dev-stable-portal-key-change-in-prod"
	if err := c.Validate(); err == nil {
		t.Fatal("prod must reject a change-in-prod secret")
	}
}

func TestValidate_ProdRejectsShortSecret(t *testing.T) {
	c := base("production")
	c.ServiceJWTSecret = "too-short"
	if err := c.Validate(); err == nil {
		t.Fatal("prod must reject a <32-byte secret")
	}
}

func TestValidate_ProdRejectsInsecureCookies(t *testing.T) {
	c := base("production")
	c.CookieSecure = false
	if err := c.Validate(); err == nil {
		t.Fatal("prod must require Secure cookies")
	}
}

func TestValidate_ProdAcceptsStrongConfig(t *testing.T) {
	if err := base("production").Validate(); err != nil {
		t.Fatalf("strong prod config should pass, got: %v", err)
	}
}
