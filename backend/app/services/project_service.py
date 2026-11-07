"""Project resolution service — DB-backed lookup."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.api.deps import ResolvedApiKey
from app.core.exceptions import AuthorizationError, NotFoundError
from app.domain.entities import RequestContext
from app.models.org_settings import OrgSettingsORM
from app.models.project import ProjectORM
from app.models.tenant import TenantORM
from app.services.policy.profiles import is_override_permitted


class ProjectService:
    def resolve_project_context(
        self,
        session: Session,
        auth: ResolvedApiKey,
        request_id: str | None = None,
        *,
        policy_profile_override: str | None = None,
    ) -> RequestContext:
        """Look up the project from DB and return a typed RequestContext.

        Raises
        ------
        NotFoundError
            Project does not exist.
        AuthorizationError
            Per-request ``policy_profile_override`` is less restrictive
            than the project's configured profile. Existence of the
            profile ID itself is validated upstream in
            ``ChatCompletionsRequest`` (422 on unknown), so reaching
            this check implies the requested profile is known.
        """
        # Web-chat / service-JWT path (Bug 1) — see the async variant.
        if getattr(auth, "internal_trusted", False):
            return self._tenant_scoped_context(
                session, auth, request_id,
                policy_profile_override=policy_profile_override,
            )

        project = session.get(ProjectORM, auth.project_id)
        if project is None:
            raise NotFoundError(
                f"Project '{auth.project_id}' not found.",
                details={"project_id": auth.project_id},
            )

        # Look up tenant region + NER flag
        tenant = session.get(TenantORM, project.tenant_id)
        region = tenant.region if tenant else "global"
        tenant_ner = bool(getattr(tenant, "ner_enabled", False))

        # Resolve NER enablement: tenant opt-in OR global org default.
        # See ner_status docstring and migration 019 for the rationale.
        org_settings = session.get(OrgSettingsORM, 1)
        org_ner = bool(getattr(org_settings, "ner_enabled", False))
        ner_enabled = tenant_ner or org_ner

        # Resolve the effective policy profile for this request.
        # Precedence: explicit per-request override > project default.
        # Override is rejected with 403 if it would lower restrictiveness
        # relative to the project's configured profile. Runs BEFORE any
        # DLP scan so an unauthorized caller never gets their content
        # scanned with a looser profile.
        effective_profile_id = project.policy_profile_id
        if policy_profile_override is not None:
            if not is_override_permitted(
                policy_profile_override,
                project.policy_profile_id,
            ):
                raise AuthorizationError(
                    "Policy profile override not permitted for this "
                    "project — override would relax restrictions below "
                    "the project default.",
                    details={
                        "requested": policy_profile_override,
                        "project_profile": project.policy_profile_id,
                    },
                )
            effective_profile_id = policy_profile_override

        return RequestContext(
            tenant_id=project.tenant_id,
            project_id=project.project_id,
            project_name=project.name,
            policy_profile_id=effective_profile_id,
            request_id=request_id,
            region=region,
            ner_enabled=ner_enabled,
            user_id=getattr(auth, "log_user_id", None),
        )

    # ------------------------------------------------------------------
    # Bug 1 — tenant-scoped context for the web-chat / service-JWT path
    # (no project row required; billing hits the user, DLP uses default).
    # ------------------------------------------------------------------

    _WEBCHAT_DEFAULT_PROFILE = "default"

    def _tenant_scoped_ner(self, tenant, org_settings) -> bool:
        tenant_ner = bool(getattr(tenant, "ner_enabled", False))
        org_ner = bool(getattr(org_settings, "ner_enabled", False))
        return tenant_ner or org_ner

    def _tenant_scoped_profile(self, policy_profile_override: str | None) -> str:
        """Default DLP profile for web-chat, honoring a permitted override.
        Web-chat sends no override today; if one arrives it may only tighten."""
        effective = self._WEBCHAT_DEFAULT_PROFILE
        if policy_profile_override is not None:
            if not is_override_permitted(
                policy_profile_override, self._WEBCHAT_DEFAULT_PROFILE
            ):
                raise AuthorizationError(
                    "Policy profile override not permitted — override would "
                    "relax restrictions below the default profile.",
                    details={
                        "requested": policy_profile_override,
                        "project_profile": self._WEBCHAT_DEFAULT_PROFILE,
                    },
                )
            effective = policy_profile_override
        return effective

    def _tenant_scoped_context(
        self, session: Session, auth: ResolvedApiKey, request_id: str | None,
        *, policy_profile_override: str | None = None,
    ) -> RequestContext:
        tenant = session.get(TenantORM, auth.tenant_id)
        org_settings = session.get(OrgSettingsORM, 1)
        return RequestContext(
            tenant_id=auth.tenant_id,
            project_id=getattr(auth, "project_id", None),  # user-scoped label; never looked up
            project_name=None,
            policy_profile_id=self._tenant_scoped_profile(policy_profile_override),
            request_id=request_id,
            region=(tenant.region if tenant else "global"),
            ner_enabled=self._tenant_scoped_ner(tenant, org_settings),
            user_id=getattr(auth, "log_user_id", None),
        )

    async def _tenant_scoped_context_async(
        self, session: AsyncSession, auth: ResolvedApiKey, request_id: str | None,
        *, policy_profile_override: str | None = None,
    ) -> RequestContext:
        tenant = await session.get(TenantORM, auth.tenant_id)
        org_settings = await session.get(OrgSettingsORM, 1)
        return RequestContext(
            tenant_id=auth.tenant_id,
            project_id=getattr(auth, "project_id", None),  # user-scoped label; never looked up
            project_name=None,
            policy_profile_id=self._tenant_scoped_profile(policy_profile_override),
            request_id=request_id,
            region=(tenant.region if tenant else "global"),
            ner_enabled=self._tenant_scoped_ner(tenant, org_settings),
            user_id=getattr(auth, "log_user_id", None),
        )

    async def resolve_project_context_async(
        self,
        session: AsyncSession,
        auth: ResolvedApiKey,
        request_id: str | None = None,
        *,
        policy_profile_override: str | None = None,
    ) -> RequestContext:
        """Async variant of :meth:`resolve_project_context`.

        Identical logic and raises, but reads from an
        :class:`~sqlalchemy.ext.asyncio.AsyncSession`. All three lookups
        (project, tenant, org_settings) are simple primary-key gets — no
        lazy relationship access — so each ``session.get`` is just awaited.

        Raises
        ------
        NotFoundError
            Project does not exist.
        AuthorizationError
            Per-request ``policy_profile_override`` is less restrictive
            than the project's configured profile.
        """
        # Web-chat / service-JWT path (Bug 1): a user's chat is NOT a developer
        # project. The service-JWT resolver bills/attributes to the end USER +
        # tenant (portal_credit_check reads portal_user_id, not project_id), so
        # there is no project row to require — resolve the request context from
        # the TENANT + the default DLP profile instead of a phantom project.
        if getattr(auth, "internal_trusted", False):
            return await self._tenant_scoped_context_async(
                session, auth, request_id,
                policy_profile_override=policy_profile_override,
            )

        project = await session.get(ProjectORM, auth.project_id)
        if project is None:
            raise NotFoundError(
                f"Project '{auth.project_id}' not found.",
                details={"project_id": auth.project_id},
            )

        # Look up tenant region + NER flag
        tenant = await session.get(TenantORM, project.tenant_id)
        region = tenant.region if tenant else "global"
        tenant_ner = bool(getattr(tenant, "ner_enabled", False))

        # Resolve NER enablement: tenant opt-in OR global org default.
        # See ner_status docstring and migration 019 for the rationale.
        org_settings = await session.get(OrgSettingsORM, 1)
        org_ner = bool(getattr(org_settings, "ner_enabled", False))
        ner_enabled = tenant_ner or org_ner

        # Resolve the effective policy profile for this request.
        # Precedence: explicit per-request override > project default.
        # Override is rejected with 403 if it would lower restrictiveness
        # relative to the project's configured profile. Runs BEFORE any
        # DLP scan so an unauthorized caller never gets their content
        # scanned with a looser profile.
        effective_profile_id = project.policy_profile_id
        if policy_profile_override is not None:
            if not is_override_permitted(
                policy_profile_override,
                project.policy_profile_id,
            ):
                raise AuthorizationError(
                    "Policy profile override not permitted for this "
                    "project — override would relax restrictions below "
                    "the project default.",
                    details={
                        "requested": policy_profile_override,
                        "project_profile": project.policy_profile_id,
                    },
                )
            effective_profile_id = policy_profile_override

        return RequestContext(
            tenant_id=project.tenant_id,
            project_id=project.project_id,
            project_name=project.name,
            policy_profile_id=effective_profile_id,
            request_id=request_id,
            region=region,
            ner_enabled=ner_enabled,
            user_id=getattr(auth, "log_user_id", None),
        )
