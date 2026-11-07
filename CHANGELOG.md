# Changelog

## 0.2.1

- Removed in-repo privacy-name scan from workshop tests (privacy checks stay in external review tooling).
- Honest disclosure: `df_init` local Python child installer, acceptance `git` child processes, template scripts may call `git`/`gh` when run.
- README evidence from daily use filled; credit for condensed Hermes bundled skill adaptations.
- Catalog draft: drop `provides_skills` (unknown capabilities key under official validator).

## 0.2.0

- Agent Workshop skill pack (14 skills) under `skills/`, registered via `ctx.register_skill` as `vibe-relay:<name>`; `skills/INDEX.md`.
- Privacy scan over the whole repo + frontmatter parse tests for every workshop skill.
- README / catalog description updated for the skill pack.

## 0.1.0

- Initial MVP: quota cache, `vibe-relay` delegation backend, gate capture + `pre_verify`, `vibe_accept`, `/vibe` park/resume, vendored DEV Framework tools (`df_check` returns terminal command).
- Plugin root on `sys.path` so sibling modules resolve under Hermes load and `hermes plugins validate` capability probe.
