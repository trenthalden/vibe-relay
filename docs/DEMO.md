# Demo shot list (&lt; 90 s)

Assumes isolated `HERMES_HOME`, plugin enabled, `delegation.backend: vibe-relay`, executor model configured.

| t | Shot | Action / prompt | Expected screen |
|---|---|---|---|
| 0–10 s | Status | `/vibe` | Quota windows, policy, last gate `(none)`, pending park `(none)` |
| 10–25 s | Refuse | `delegate_task` goal only (no allowed/gate) | Tool error: refuses without `allowed` and `gate` |
| 25–50 s | Route | Stub or real low remaining; `delegate_task` with `allowed=src/**`, `gate=python -m unittest …`, `lane=auto` | Child label `executor (…)`; receipt asks for gate |
| 50–70 s | Accept | After gate `FINISH PASSED` in terminal; `vibe_accept` with same allowed | `ACCEPT` with inside paths; or `REJECT` if a file outside allowed |
| 70–80 s | Workshop | `skill_view("vibe-relay:systematic-debugging")` | Namespaced workshop skill loads (root-cause loop) |
| 80–90 s | Park + card | `/vibe park . --note "resume slice"` | `PARKED` + “Done only after a green gate on the real diff.” |

Commands to keep in the clipboard:

```text
/vibe
/vibe park . --note "quota wall — resume after reset"
```
