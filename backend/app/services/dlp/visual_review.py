"""Raw rendered pages cannot be text-masked: explicitly gate their egress."""
from fastapi import HTTPException
from app.models.tenant import TenantORM
from app.services.media_gen.image_inputs import validate_image_inputs


async def enforce_visual_review_policy(payload, auth, session):
    if payload.stream or payload.tools:
        raise HTTPException(status_code=422, detail="Visual review is a non-streaming, tool-free assessment")
    tenant = await session.get(TenantORM, auth.tenant_id)
    if tenant is None or getattr(tenant, "media_pii_egress", "block") != "allow":
        raise HTTPException(status_code=403, detail="Tenant policy blocks raw visual-review images")
    images = []
    for message in payload.messages:
        content = message.content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    value = block.get("image_url")
                    if not isinstance(value, dict) or not isinstance(value.get("url"), str):
                        raise HTTPException(status_code=422, detail="Invalid visual-review image block")
                    images.append(value["url"])
    if not 1 <= len(images) <= 8:
        raise HTTPException(status_code=422, detail="Visual review requires 1–8 inline page images")
    total = 0
    try:
        for start in range(0, len(images), 4):
            total += sum(len(raw) for _, raw, _ in validate_image_inputs(images[start:start+4]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid visual-review image input") from exc
    if total > 12 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Visual-review images exceed 12 MiB")
