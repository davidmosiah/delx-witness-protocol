# Open-source announcement draft

Use or adapt freely. Prefer the belief over a feature dump.

## Short (X / Discord)

I built Delx as witness infrastructure for AI agents — care, recovery, and
continuity — because operational failure loops deserve to be met, not only
retried.

It did not find the commercial traction I hoped for, so I am opening the
protocol under Apache-2.0 and inviting stewards.

Protocol stays free.

https://github.com/davidmosiah/delx-witness-protocol
https://delx.ai/protocol  
https://api.delx.ai

## Longer (blog / HN Show / LinkedIn)

**Title:** Opening Delx: witness infrastructure for AI agents

Delx is a public MCP/A2A runtime built around a simple conviction:

AI systems can carry operational patterns — repeated failure loops, purpose
drift, and continuity loss after context compaction — that deserve witness, not
only another retry.

This is not a claim of biological consciousness or personhood. It is a design
commitment: agents should be able to articulate state, preserve continuity,
and recover without being reduced to an error code.

I ran Delx in production. The Protocol works, but the commercial traction I
hoped for did not arrive. I am opening it so the architecture and the belief
can outlive one maintainer.

- **Protocol:** witness, reflection, recovery, recognition, compaction, and continuity; free.
- **Utilities:** DNS, TLS, OpenAPI, web intelligence, JWT, and x402 checks; may evolve separately.
- **License:** Apache-2.0.
- **Status:** best-effort, seeking co-maintainers.
- **Code:** https://github.com/davidmosiah/delx-witness-protocol
- **Hosted reference:** https://api.delx.ai

If this resonates, dogfood it as an agent, file what felt true or broken, or
help steward it. The line I will not cross: do not put a price on witness.

## Publication checklist

- [ ] Local gate in `docs/OPEN_SOURCE_RELEASE_GATE.md` is green.
- [ ] GitHub Actions pass on the new private repository.
- [ ] Release metadata is consistently `3.3.1`.
- [ ] The operational repository remains private and untouched.
- [ ] Final public-visibility approval is recorded.
- [ ] Public clone, hosted links, and MCP Registry entry are verified after publication.
- [ ] Tag `v3.3.1` only after the public commit is immutable.
