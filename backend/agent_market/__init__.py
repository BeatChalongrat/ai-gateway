"""Agent Market — Python side (AM1).

Everything here runs inside the locked-down sandbox container that
``agent-runner`` launches per step (``--network none``, read-only rootfs,
uid 65534). It is shipped to the sandbox as a read-only zip on ``/job/in``;
nothing in the backend application imports it.

Layout:
    engine/        request/response protocol, file reading, findings shape,
                   Thai/Decimal helpers
    deliverables/  theme registry + report (.docx), deck (.pptx), tables
                   (.xlsx), charts and the quality gate
    packages/      one folder per installable agent (manifest, AGENT.md,
                   checks/, scripts/, templates/, sample-data/, evals/)
    tools/         fixture generator (deterministic, seeded)
    tests/         unittest suite (the sandbox image has no pytest)

Removal: delete this folder. See docs/agent-market/REMOVAL.md.
"""

ENGINE_VERSION = "1.0.0"
