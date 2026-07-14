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
