"""Media-generation services (Sprint 1 foundation).

Plumbing for async video / music / TTS generation. Sprint 1 ships only
the adapter contract (``base.MediaGenAdapter``) and the lifespan job
loop (``job_loop.run_generation_job_loop``); no concrete provider
adapter exists yet — Sprint 2 adds the first (Replicate).
"""
