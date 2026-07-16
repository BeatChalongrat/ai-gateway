"""Embeddings utility service (gateway-fronted OpenAI embeddings).

FIX 2 — chat-edge used to call OpenAI ``/v1/embeddings`` directly, bypassing
DLP. The gateway now fronts it so the embedding text passes the raw-egress
gate (``app.services.dlp.egress_policy.scan_embedding_text``) first.
"""
