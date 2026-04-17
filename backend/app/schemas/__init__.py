"""Response contract strategy for the Secure AI Gateway.

Three tiers:

1. **Public API** (`/v1/*`)
   - OpenAI-compatible shapes.  `chat/completions` returns the resource
     directly; `models` returns `{"data": [...]}`.
   - No custom success envelope — callers that already integrate with
     OpenAI should be able to point at this gateway with minimal changes.

2. **Admin API** (`/admin/*`)
   - List endpoints return `{"items": [...]}`.  When pagination lands,
     `total`, `offset`, and `limit` will be added alongside `items`
     (additive, not breaking).
   - Single-resource endpoints return the resource directly.

3. **Infrastructure** (`/health`, `/health/deep`)
   - Simple typed response.  No envelope.

**Errors** (all tiers):
   Every error response uses the unified `ErrorResponse` envelope::

       {
         "error": {"code": "...", "message": "...", "details": ...},
         "request_id": "..."
       }

   See `common.ErrorResponse` and `middleware/error_handler.py`.
"""
