// FIX88 Part A — the arithmetic of the credit ceiling, in one place so the
// preview the admin reads before confirming and the backend's own rule
// cannot drift apart. The backend is authoritative
// (app/services/portal/credit_service.set_quota); this mirrors it for the
// dialog, and admin-ui/app/portal-users/page.test.tsx pins both against
// the owner's four rows.

/** The balance after a ceiling change: the owner's single rule,
 *  `balance += (newQuota − quota)`, floored at 0. Raising the ceiling on a
 *  full account lands it full again; 500k already spent stays spent. */
export function balanceAfterCeiling(balance: number, quota: number, newQuota: number): number {
  return Math.max(0, balance + (newQuota - quota));
}

/** How much a top-up may still add before it would pass the ceiling. */
export function roomToCeiling(balance: number, quota: number): number {
  return Math.max(0, quota - balance);
}
