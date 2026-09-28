# ftw-global-data-catalog — developer guide

Git-backed Portolan/STAC catalog for the Fields of the World (FTW) Global **beta** release.
The build plan is in [docs/plan.md](docs/plan.md) — read it first.

## Models
The main session runs on Fable (`.claude/settings.json`); subagents default to Opus
(`CLAUDE_CODE_SUBAGENT_MODEL`). Use `model: "sonnet"` explicitly for lightweight
exploration/search subagents; keep Opus (the default) for implementation and review subagents.
