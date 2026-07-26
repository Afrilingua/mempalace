# Write routing policy

This document defines the shared policy used by the staged Tier 3 daemon
rollout tracked in #1963.

This foundation PR does not change existing hook or CLI routing. It provides
one tested policy model that later hook and CLI PRs can consume without
inventing different fallback rules.

## Policies

`direct`

    Always execute through the existing direct local path.

`prefer`

    Use an available daemon. A caller that is allowed to start the daemon may
    do so. Otherwise, fall back to the direct path.

`require`

    Use an available daemon. A caller that is allowed to start the daemon may
    do so. If neither is possible, block the operation. Never fall back to a
    direct ChromaDB writer.

## Concrete routing outcomes

The shared decision function returns one of:

- `direct`
- `daemon`
- `blocked`

It also reports whether the caller should auto-start the daemon and why the
route was selected.

Hooks generally pass `daemon_can_start=False` because hook execution has a
tight latency budget.

Interactive CLI commands can pass `daemon_can_start=True`.

## Configuration

Global environment policy:

    MEMPALACE_WRITE_ROUTING=direct|prefer|require

Hook-specific environment policy:

    MEMPALACE_HOOK_WRITE_ROUTING=direct|prefer|require

CLI-specific environment policy:

    MEMPALACE_CLI_WRITE_ROUTING=direct|prefer|require

Configuration-file shape:

    {
      "write_routing": {
        "default": "direct",
        "hooks": "prefer",
        "cli": "require"
      }
    }

## Precedence

For hooks:

1. `MEMPALACE_HOOK_WRITE_ROUTING`
2. `MEMPALACE_WRITE_ROUTING`
3. legacy `MEMPALACE_HOOKS_DAEMON`
4. `write_routing.hooks`
5. `write_routing.default`
6. legacy `hooks.daemon`
7. `direct`

For CLI writes:

1. `MEMPALACE_CLI_WRITE_ROUTING`
2. `MEMPALACE_WRITE_ROUTING`
3. `write_routing.cli`
4. `write_routing.default`
5. `direct`

## Backward compatibility

The existing `MEMPALACE_HOOKS_DAEMON` environment variable and
`hooks.daemon` config value remain supported.

Legacy true values map to `prefer`.

Legacy false values map to `direct`.

The existing `MempalaceConfig.hook_use_daemon` property is intentionally
unchanged in this PR. Hook and CLI behavior remains unchanged until their
policy-aware rollout PRs land.

## Invalid policy values

New policy settings accept only:

- `direct`
- `prefer`
- `require`

Invalid values fail with a source-specific error rather than silently falling
back. This is important because silently turning a misspelled `require` into a
direct write would violate the safety purpose of the policy.

## Local backend single-writer safety

File-backed backends such as `chroma`, `sqlite_exact`, and Milvus Lite support
exactly one writable process per palace. Serializing individual calls is not
enough because each long-lived process can retain SQLite/WAL, FTS, or vector
index state between calls.

- A writable daemon owns the palace writer lease for its full lifetime.
- Writable MCP HTTP acquires that lease before binding and refuses startup if
  another process owns it.
- MCP stdio may coexist for reads, but mutating tools refuse while another
  process owns the lease and become available after that owner exits.
- Read-only MCP HTTP may coexist with the writer.
- Direct CLI and hook writes must not run beside a writable daemon or MCP HTTP
  owner. Route them through the daemon with `require` when the daemon owns the
  palace.

`MEMPALACE_MCP_ALLOW_PEER_WRITER` cannot bypass this protection for local
file-backed or unknown plugin backends. It is retained only for explicitly
remote service backends (`qdrant` and `pgvector`) that coordinate concurrent
clients themselves.

Do not delete or unlink a live palace lock to recover ownership. Stop the
owning process cleanly; the operating system releases its lock automatically.
If corruption is suspected, back up the palace and run integrity/repair
operations offline, with no writable service running.

## Follow-up PRs

Hook-triggered writes now consume this policy; see
`docs/hook-write-routing.md`.

The remaining rollout PR will apply the policy to routine CLI writes.

Maintenance operations such as repair, migration, and index rebuild are not
ordinary routed writes. They require a separate exclusive-maintenance policy.
