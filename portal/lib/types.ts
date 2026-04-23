// Types that shadow the backend's portal response shapes.
// Kept in one place so every page + component references the same
// contract and a backend field rename is easy to track down.

export interface PortalUser {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  plan: string;
  status: string;
  credit_balance: number;
  credit_used: number;
  credit_percent_used: number;
  is_low_credit: boolean;
  email_verified?: boolean;
  // Phase 3 tenancy. tenant_id may be null for orphaned users
  // between tenant-removal and re-invite redemption.
  tenant_id?: string | null;
  tenant_role?: "owner" | "member";
  created_at: string;
}

export interface TenantMember {
  id: string;
  email: string;
  display_name: string | null;
  tenant_role: "owner" | "member";
  status: string;
  created_at: string;
}

export interface TenantInvite {
  id: string;
  tenant_id: string;
  code: string;
  email_hint: string | null;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  used_by: string | null;
}

export interface AuthResponse {
  user: PortalUser;
  access_token: string;
  refresh_token: string;
}

export interface Project {
  id: string;
  name: string;
  tenant_id: string;
  created_at: string;
  updated_at: string | null;
}

export interface ApiKey {
  key_id: string;
  key_prefix: string;
  name: string;
  status: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
}

// Only surfaced when a key is first minted — never persisted.
export interface ApiKeyCreated extends ApiKey {
  api_key: string;
}

export interface TokenPack {
  id: string;
  name: string;
  tokens: number;
  price_usd: number;
  description: string | null;
  sort_order: number;
}

export interface CreditTransaction {
  id: string;
  amount: number;
  balance_after: number;
  type: string;
  description: string | null;
  request_id: string | null;
  created_by: string | null;
  created_at: string;
}

export interface UsageRequest {
  id: string | null;
  model: string | null;
  provider: string | null;
  status: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  latency_ms: number | null;
  created_at: string | null;
}

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}
