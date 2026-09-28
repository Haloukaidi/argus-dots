# Named workflow profiles

A vertical may declare several complete task scopes without creating more
verticals. The Manager chooses `WORKFLOW_PROFILE` from the advertised menu.
This is separate from `WORKFLOW_MODE` (`direct`/`staged`): a scoped profile uses
staged execution over only its selected stages, not direct early completion.

```python
WORKFLOW_PROFILES = {
    "rtl": {
        "purpose": "implement and verify RTL without synthesis",
        "stages": ("specification", "rtl", "verification"),
    },
    "full": {
        "purpose": "complete synthesized delivery",
        "stages": STAGE_ORDER,
    },
}
```

Every profile needs a nonempty purpose and a nonempty, duplicate-free, ordered
subsequence of the provider's existing stage order. `full` must exactly preserve
the original order. Existing checklist items, validators, review policy and
completion strength remain attached. Providers may accept a keyword-only
`workflow_profile` in `stage_completion_issues`; the framework supplies the saved
profile, or `full` for legacy tasks. This lets tool-readiness checks match scope
without weakening selected-stage evidence.

For a new task in a profile-capable vertical, the Manager must choose the smallest
profile covering the requested deliverable. Full delivery remains explicit.
Missing/unknown choices fail rather than silently falling back to another flow.
Supplemental work retains the active profile. Changing it requires the existing
operator-authorized replacement handoff, which resets stage state.

`PIPELINE_STATE.json` records `workflow_profile` and `workflow_stages` together
with the vertical. Project-local provider views drive planning, checklist
rendering, progression and completion without mutating cached modules. The saved
stage order must still match the provider: a later plugin update cannot silently
change an active task's scope. Use a new handoff for an intentional change.

All selected stages must be completed. Stage jumps and direct early-completion
flags cannot omit a profile stage. A shorter profile finishes at its own final
stage; omitted stages are outside scope, not accepted or skipped. Validators
still receive the separate evidence directory and state root.

**Compatibility:** providers without profiles and existing projects without a
saved profile retain their previous behavior. Profile-capable plugins should
check for `VerticalContract.for_profile` and reject older frameworks visibly.
Upgrade the framework before installing such plugins. This does not install
external tools, change backend-account configuration or migrate existing work.
