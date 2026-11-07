"""Web-search provider abstraction + fallback service.

Mirrors the LLM provider pattern: a common adapter interface, one
adapter per provider, and a service that tries enabled providers in
priority order until one succeeds.
"""
