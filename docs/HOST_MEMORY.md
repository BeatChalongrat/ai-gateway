# Host memory — who gives way, and how a gate is run

This host is a 32 GB laptop (memory soldered) that runs live and every build and test beside it.
Live's 27 containers use about 3.2 GB inside a Docker VM capped at 12 GB (`%USERPROFILE%\.wslconfig`).
A gate adds a test stack (about 2 GB), sandboxes, image builds, and on the Windows side Go, Node, a
browser and a desktop build. Run together they took the host to 1.1 GB free five times on 7 Oct 2026;
the integration lane was cut twice and the page file was 8–10 GB deep. Run alone, that lane takes 41
minutes and passes.

Two things hold it now. Neither needs a restart.

## 1. Live keeps its floor and its turn

Every live service declares three settings in its compose file:

| Setting | What it is |
|---|---|
| `mem_limit` (or `deploy.resources.limits.memory`) | a ceiling — one runaway service cannot take the VM |
| `mem_reservation` | the floor the kernel leaves it when memory is short (5.8 GB in all) |
| `oom_score_adj` | its turn when the VM runs out: databases −900, the front door −800, the applications −700 … −100. Anything a gate starts has 0, so the kernel takes a gate's containers first |

`backend/tests/test_memory_guard_compose.py` holds the compose files to this. A container that is
recreated takes the settings by itself. For the ones already running:

```
python scripts/ops/live_memory.py status    # each live container against what compose says
python scripts/ops/live_memory.py apply     # put them right in place — no restart
python scripts/ops/live_memory.py check     # exit 1 if any differs
```

`apply` sets the ceiling and floor with `docker update` and writes the turn to the container's
processes. A container that is restarted (not recreated) loses the written turn until `apply` runs
again; the gate runs `check` first and applies when needed.

## 2. Whatever a gate starts goes first

Nothing a gate starts has a floor or a protected turn:

- The test stack's compose file is generated from live's (`scripts/test_stack_gen.py`). The generator
  keeps each service's ceiling, drops its floor and gives it `oom_score_adj: 500`
  (`scripts/tests/test_test_stack_gen.py`). Copied as they were, a test database would have been as
  protected as the live one, in the same VM.
- A lane, a build and a sandbox are plain containers: turn 0, no floor.

So when the VM runs out the order is: helper containers (1000), the test stack (500), lanes, builds and
sandboxes (0), then live from −100 down to the databases at −900.

A gate's heavy phases are also run one at a time, each waiting for free memory on the host before it
starts — the gate script and its phases are described with the product they gate.

## Traps

- **After a compose file of a running stack is edited, `docker compose run` and `docker compose up`
  recreate every dependency whose settings changed.** A backup started that way recreated the chat
  database (three seconds down, 7 Oct 2026). The deploy scripts pass `--no-deps`, and so does every
  written instruction for a backup by hand (`docs/ops/backups.md`, the pager's own text). Never run
  either without it on live.
- The Windows "free memory" figure moves by gigabytes in a minute as the VM returns cache
  (`autoMemoryReclaim=gradual`). Trust the gate's wait, not one reading.
- The harness that runs the coding assistant kills background commands when the host is critically
  short of memory. A lane killed that way has no verdict; it is not a failure of the lane.
