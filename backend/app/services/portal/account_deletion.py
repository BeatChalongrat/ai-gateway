"""FIX132 C — a user deletes their own account.

The backend owns the account, so it orchestrates:

1. ``request_account_deletion`` — the moment the user confirms (typing
   their email). Refused with a Thai reason when the user is the only
   owner of a team that still has other members. Otherwise chat-edge is
   told first (hide every chat, stop tasks, refuse tokens there); only
   when it confirms is the account marked: ``status = 'pending_deletion'``
   (sign-in, refresh and every portal route refuse it), ``token_version``
   bumped (every session ends), API keys revoked,
   ``deletion_requested_at`` stamped. Nothing is erased yet.

2. ``purge_due_accounts`` — a step of the nightly retention pipeline.
   ``account_purge_days`` (30) after the request, chat-edge erases the
   chat data, then ``anonymise_portal_user`` removes or anonymises the
   rest here:

   * connector_tokens (OAuth tokens), generation_jobs (prompts),
     project_members — deleted;
   * api_keys — revoked and detached; projects — detached;
   * request_logs (and request_logs_archive) — user_id cleared and every
     text column (raw, masked, output, DLP findings, feedback comment)
     emptied; the counts, tokens and cost stay for the usage totals;
   * credit_transactions — KEPT as financial records (amounts, balance,
     type, time); they point at the tombstone row, which no longer
     names anyone;
   * portal_users — kept as a tombstone: email ``deleted-<id>@deleted.invalid``,
     no password, name, avatar (file removed) or OAuth link,
     ``status = 'deleted'``, ``deleted_at`` stamped. The email is free
     for a new sign-up.

Both steps write an admin_audit_log row with ids and counts only.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ValidationError
from app.models.api_key import ApiKeyORM
from app.models.connector_token import ConnectorTokenORM
from app.models.generation_job import GenerationJobORM
from app.models.portal_user import PortalUserORM
from app.models.project import ProjectORM
from app.models.project_member import ProjectMemberORM
from app.models.tenant import TenantORM

logger = structlog.get_logger(__name__)

STATUS_PENDING_DELETION = "pending_deletion"
STATUS_DELETED = "deleted"
PURGE_ACTOR = "system:account-purge"

CONFIRM_MISMATCH_TH = "พิมพ์อีเมลของบัญชีนี้ให้ตรงทุกตัวอักษรเพื่อยืนยันการลบบัญชี"
SOLE_OWNER_TH = (
    "ลบบัญชีนี้ไม่ได้ เพราะคุณเป็นเจ้าของทีมเพียงคนเดียว และทีมยังมีสมาชิกอีก {n} คน — "
    "นำสมาชิกออกจากทีมก่อน หรือติดต่อผู้ดูแลระบบให้ตั้งเจ้าของคนใหม่ แล้วจึงลบบัญชี"
)
CHAT_EDGE_DOWN_TH = (
    "ลบบัญชีไม่สำเร็จในตอนนี้ และยังไม่มีข้อมูลใดถูกลบ — โปรดลองใหม่อีกครั้งในอีกสักครู่"
)

# Text columns of a request-log row that can carry what the user wrote or
# received. Emptied on purge; the numeric columns stay.
REQUEST_LOG_TEXT_COLUMNS = (
    "input_raw",
    "input_masked",
    "output_raw",
    "output_final",
    "dlp_findings_json",
    "input_raw_encrypted",
    "feedback_comment",
)

AVATAR_DIR = Path(__file__).resolve().parent.parent.parent.parent / "uploads" / "avatars"


class ChatEdgeUnavailable(Exception):
    """chat-edge did not confirm; nothing was changed here."""


def normalise_purge_mode(value: str | None) -> str:
    v = (value or "").strip().lower()
    if v in ("", "on", "true", "1", "yes"):
        return "on"
    if v in ("off", "false", "0", "no", "disabled"):
        return "off"
    return "dry_run"


def other_members_blocking(db: Session, user: PortalUserORM) -> int:
    """How many other members make this deletion impossible (0 = none).

    Blocked only when the user is an owner, no other ACTIVE owner exists,
    and the team still has other members who are not themselves on the
    way out — deleting would leave them in a team nobody can manage.
    """
    if (user.tenant_role or "member") != "owner":
        return 0
    other_owners = db.execute(
        select(func.count(PortalUserORM.id)).where(
            PortalUserORM.tenant_id == user.tenant_id,
            PortalUserORM.id != user.id,
            PortalUserORM.tenant_role == "owner",
            PortalUserORM.status == "active",
        )
    ).scalar_one()
    if other_owners:
        return 0
    return int(
        db.execute(
            select(func.count(PortalUserORM.id)).where(
                PortalUserORM.tenant_id == user.tenant_id,
                PortalUserORM.id != user.id,
                PortalUserORM.status.notin_([STATUS_PENDING_DELETION, STATUS_DELETED]),
            )
        ).scalar_one()
    )


def request_account_deletion(
    db: Session,
    user: PortalUserORM,
    *,
    confirm: str,
    chat_edge,
    purge_days: int,
    now: datetime | None = None,
) -> dict:
    """Step 1. Raises ValidationError / ConflictError / ChatEdgeUnavailable."""
    now = now or datetime.now(timezone.utc)
    if (confirm or "").strip().lower() != (user.email or "").strip().lower():
        raise ValidationError(CONFIRM_MISMATCH_TH, code="confirm_mismatch")
    blocking = other_members_blocking(db, user)
    if blocking:
        raise ConflictError(
            SOLE_OWNER_TH.format(n=blocking),
            code="sole_owner_with_members",
            details={"other_members": blocking},
        )

    ack = chat_edge.request_deletion(user.id)
    if ack is None:
        raise ChatEdgeUnavailable()

    revoked = db.execute(
        update(ApiKeyORM)
        .where(ApiKeyORM.portal_user_id == user.id, ApiKeyORM.status == "active")
        .values(status="revoked")
    ).rowcount or 0
    user.status = STATUS_PENDING_DELETION
    user.deletion_requested_at = now
    user.token_version = (user.token_version or 1) + 1
    db.flush()

    from app.services.admin_audit_service import AdminAuditService

    AdminAuditService().record(
        db,
        actor_key_id=f"user:{user.id}",
        tenant_id=user.tenant_id,
        action="portal_user.account_deletion_requested",
        resource_type="portal_user",
        resource_id=user.id,
        details={
            "api_keys_revoked": revoked,
            "chats_hidden": (ack or {}).get("conversations_hidden"),
            "purge_after_days": purge_days,
        },
    )
    purge_after = now + timedelta(days=purge_days)
    logger.warning(
        "account_deletion_requested",
        user_id=user.id,
        api_keys_revoked=revoked,
        purge_after=purge_after.isoformat(),
    )
    return {"status": STATUS_PENDING_DELETION, "purge_after": purge_after.isoformat()}


def _anonymise_request_logs(db: Session, user_id: str) -> int:
    """Clear the user id and every text column of the user's request logs.

    Rows written before request_logs.user_id existed are reached through
    the credit ledger's request ids (the same join the usage page uses).
    """
    # The session's own connection: an inspector on the engine checks out
    # a connection of its own, and on a single-connection pool its return
    # would roll back this very transaction.
    insp = inspect(db.connection())
    total = 0
    for table in ("request_logs", "request_logs_archive"):
        if not insp.has_table(table):
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        if "user_id" not in cols:
            continue
        sets = ", ".join(
            ["user_id = NULL"] + [f"{c} = NULL" for c in REQUEST_LOG_TEXT_COLUMNS if c in cols]
        )
        # Two statements rather than one OR, so each can use its index
        # (user_id; request_id) on the partitioned table.
        res = db.execute(text(f"UPDATE {table} SET {sets} WHERE user_id = :uid"), {"uid": user_id})
        total += res.rowcount or 0
        res = db.execute(
            text(
                f"UPDATE {table} SET {sets} WHERE request_id IN ("
                "  SELECT request_id FROM credit_transactions"
                "  WHERE user_id = :uid AND request_id IS NOT NULL)"
            ),
            {"uid": user_id},
        )
        total += res.rowcount or 0
    return total


def anonymise_portal_user(db: Session, user_id: str, *, now: datetime | None = None) -> dict:
    """Step 2's portal half. Returns counts; the caller commits."""
    now = now or datetime.now(timezone.utc)
    user = db.get(PortalUserORM, user_id)
    if user is None:
        return {}
    counts: dict[str, int] = {}
    counts["connector_tokens"] = db.query(ConnectorTokenORM).filter(
        ConnectorTokenORM.portal_user_id == user_id
    ).delete(synchronize_session=False)
    counts["generation_jobs"] = db.query(GenerationJobORM).filter(
        GenerationJobORM.user_id == user_id
    ).delete(synchronize_session=False)
    counts["project_members"] = db.query(ProjectMemberORM).filter(
        ProjectMemberORM.user_id == user_id
    ).delete(synchronize_session=False)
    counts["api_keys"] = db.execute(
        update(ApiKeyORM)
        .where(ApiKeyORM.portal_user_id == user_id)
        .values(status="revoked", portal_user_id=None, name="deleted account")
    ).rowcount or 0
    counts["projects_detached"] = db.execute(
        update(ProjectORM).where(ProjectORM.portal_user_id == user_id).values(portal_user_id=None)
    ).rowcount or 0
    counts["request_log_rows_updated"] = _anonymise_request_logs(db, user_id)

    removed = 0
    for old in AVATAR_DIR.glob(f"{user_id}_*"):
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    counts["avatar_files"] = removed

    # The team name signup derived from the email ("Alice's Team") goes too
    # when nobody else is in that team.
    from app.services.portal.auth_service import _default_tenant_name_for_email

    tenant = db.get(TenantORM, user.tenant_id)
    if tenant is not None and user.email and tenant.name == _default_tenant_name_for_email(user.email):
        others = db.execute(
            select(func.count(PortalUserORM.id)).where(
                PortalUserORM.tenant_id == user.tenant_id, PortalUserORM.id != user_id
            )
        ).scalar_one()
        if not others:
            tenant.name = "Deleted account"
            counts["team_renamed"] = 1

    user.email = f"deleted-{user_id}@deleted.invalid"
    user.password_hash = "!deleted:" + secrets.token_urlsafe(24)
    user.display_name = None
    user.avatar_url = None
    user.oauth_provider = None
    user.oauth_id = None
    user.email_verified = False
    user.status = STATUS_DELETED
    user.deleted_at = now
    user.token_version = (user.token_version or 1) + 1
    db.flush()
    return counts


@dataclass
class AccountPurgeResult:
    mode: str
    due: int = 0
    purged: int = 0
    failed: int = 0
    counts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "due": self.due,
            "purged": self.purged,
            "failed": self.failed,
            "counts": self.counts,
        }


def purge_due_accounts(
    session_factory,
    *,
    chat_edge,
    mode: str,
    days: int,
    now: datetime | None = None,
    limit: int = 200,
) -> AccountPurgeResult:
    """Step 2 — the nightly pipeline step. Chat data first, then the portal
    row; a user whose chat-edge purge did not confirm is left for the next
    night (their portal row still says pending_deletion)."""
    from app.services.admin_audit_service import AdminAuditService

    mode = normalise_purge_mode(mode)
    result = AccountPurgeResult(mode=mode)
    if mode == "off":
        return result
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, int(days)))
    session = session_factory()
    try:
        due = list(
            session.execute(
                select(PortalUserORM.id, PortalUserORM.tenant_id)
                .where(
                    PortalUserORM.status == STATUS_PENDING_DELETION,
                    PortalUserORM.deletion_requested_at.isnot(None),
                    PortalUserORM.deletion_requested_at <= cutoff,
                )
                .order_by(PortalUserORM.deletion_requested_at.asc())
                .limit(limit)
            ).all()
        )
        result.due = len(due)
        if mode == "dry_run":
            if due:
                AdminAuditService().record(
                    session,
                    actor_key_id=PURGE_ACTOR,
                    tenant_id="__system__",
                    action="portal_user.account_purge_dry_run",
                    resource_type="portal_user",
                    resource_id="-",
                    details={"due": len(due), "days": days},
                )
                session.commit()
            logger.info("account_purge_dry_run", due=len(due))
            return result
        for user_id, tenant_id in due:
            ack = chat_edge.purge(user_id)
            if ack is None:
                result.failed += 1
                continue
            try:
                counts = anonymise_portal_user(session, user_id, now=now)
                chat_counts = (ack or {}).get("counts") or {}
                AdminAuditService().record(
                    session,
                    actor_key_id=PURGE_ACTOR,
                    tenant_id=tenant_id,
                    action="portal_user.account_purged",
                    resource_type="portal_user",
                    resource_id=user_id,
                    details={"portal": counts, "chat": chat_counts},
                )
                session.commit()
            except Exception as exc:  # noqa: BLE001 — next user, next night
                session.rollback()
                result.failed += 1
                logger.error("account_purge_failed", user_id=user_id, error=str(exc)[:200])
                continue
            result.purged += 1
            for k, v in counts.items():
                result.counts[k] = result.counts.get(k, 0) + int(v or 0)
    finally:
        session.close()
    logger.warning("account_purge_complete", **result.to_dict())
    return result
