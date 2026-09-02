// Mint a dev portal access JWT (HS256) for the local chat-edge — the SAME
// secret the container runs with (compose default). Dev only; never for prod.
const crypto = require("crypto");
const b64 = (o) => Buffer.from(typeof o === "string" ? o : JSON.stringify(o)).toString("base64url");
function mint({ secret, sub, email, tenantId, ttlSec = 3600 }) {
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: "HS256", typ: "JWT" };
  const claims = {
    sub, email, type: "portal", token_use: "access", tenant_id: tenantId, tenant_role: "owner", token_version: 0,
    iat: now, exp: now + ttlSec, iss: "sran-portal",
  };
  const data = `${b64(header)}.${b64(claims)}`;
  const sig = crypto.createHmac("sha256", secret).update(data).digest("base64url");
  return `${data}.${sig}`;
}
module.exports = { mint };
if (require.main === module) {
  const [sub, tenantId, email] = process.argv.slice(2);
  process.stdout.write(mint({ secret: process.env.PORTAL_JWT_SECRET || "dev-stable-portal-key-change-in-prod", sub, email: email || "probe@local", tenantId }));
}
