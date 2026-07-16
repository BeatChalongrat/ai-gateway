"""Voice utility service (gateway-fronted OpenAI Whisper STT + TTS).

FIX 2 — chat-edge used to call OpenAI ``/v1/audio/transcriptions`` and
``/v1/audio/speech`` directly, bypassing DLP. The gateway now fronts both so
TTS input text passes the raw-egress gate
(``app.services.dlp.egress_policy.scan_voice_text``) and STT audio passes the
coarser ``assert_stt_egress_allowed`` policy/audit gate first.
"""
