"""Registered-but-unimplemented provider adapters.

Adapters in this package are surfaced in ``/admin/providers`` so operators
can see them and pre-configure API keys, but ``chat()`` raises
``ProviderNotImplementedError`` (HTTP 501) until the real integration lands.

Each stub carries a TODO marker + link to the upstream schema docs:

    grep -rn "TODO(sran-provider-adapters)" backend/app/services/providers/_pending/

Remove a provider from this package by replacing its file with a real
adapter in the parent ``providers/`` directory and updating
``provider_dispatcher.py`` + ``model_registry.py``.
"""
