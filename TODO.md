# Character screen HTMX responsiveness

Status of incremental HTMX work to make add/remove actions responsive on the character screen.

## Done
- Added partial templates for HTMX rendering:
  - `camp/character/templates/character/_character_summary.html` (issues + feature groups; links open feature modal via HTMX)
  - `camp/character/templates/character/_feature_form.html` (feature form/choices; posts via HTMX, re-renders the modal content)
- Updated character detail page to use HTMX and a modal target:
  - `camp/character/templates/character/character_detail.html`
    - Wraps summary in a `#character-summary` container that loads via `hx-get` from `character-summary` and refreshes on `refresh-character` events (listens from body)
    - Adds a Bootstrap modal container `#featureModal` with `#featureModalContent` as HTMX target
- Backend endpoints updated/added:
  - `camp/character/views.py`
    - `feature_view`: HTMX-aware GET/POST. Returns `_feature_form.html` and sets `HX-Trigger: refresh-character` (targeted at body) after successful mutations; also injects a hidden `hx-get` to force-refresh `#character-summary` upon success
    - `character_summary_view`: returns `_character_summary.html` with issues + feature groups
    - Manage actions now HTMX-aware with correct responses:
      - `set_attr`, `set_name`, `apply_view`, `undo_view` return 204 + `HX-Trigger: refresh-character` (target body)
      - `delete_character`, `copy_view` set `HX-Redirect` to navigate appropriately
  - `camp/character/urls.py`: added `path("<int:pk>/summary/", views.character_summary_view, name="character-summary")`
- Client helpers:
  - Base template (`templates/base.html`) already sets global `hx-headers` with CSRF and re-initializes tooltips on HTMX loads
  - `_feature_form.html` includes small script to enable/disable freeform option field based on "Other" radio selection
  - Added out-of-band messages partial `templates/snippets/messages.html` and include it in HTMX partials so alerts update without full reload
  - Added script to close any open Bootstrap modal when `refresh-character` fires
- Test suite passes (`poetry run pytest`): 39 passed

## TODO (to reach feature completeness)
- Manage actions via HTMX
  - [x] Set Attributes (Level/CP): convert modal form to `hx-post` and have server respond with `HX-Trigger: refresh-character`
  - [x] Change Name: convert to `hx-post` + trigger
  - [x] Delete/Discard: convert to `hx-post` + redirect fallback; trigger a summary refresh or navigate away when appropriate
  - [x] Undo: convert to `hx-post`; on success, trigger summary refresh (and possibly close modal)
  - [x] Copy Character: convert to `hx-post`; on success, navigate to the new character (HTMX: `HX-Redirect`)
  - [x] Full Character Respend: update `apply_view` HTMX branch to send `HX-Trigger: refresh-character` (avoid full page ClientRefresh)
  - [ ] QA: verify summary refresh across all feature types (classes, breeds, subfeatures, choices) and remove hidden refresher if redundant

- Modal UX
   - [ ] Optionally load large available lists lazily (category sections `hx-get` on expand) to reduce initial DOM size

- Messages and feedback
  - [x] Add a messages fragment target on the page and, for HTMX responses, also update it so feedback is visible without a full page load

- Template consistency / cleanup
  - [ ] DRY up `character/feature_form.html` by including `_feature_form.html` for full-page fallback instead of duplicating markup
  - [ ] Ensure all feature links in summary and nested lists include HTMX attributes (sanity pass)

- Testing
  - [ ] Add view tests for `character_summary_view`
  - [ ] Add HTMX behavior tests for `feature_view` POST: returns partial, sends `HX-Trigger`, retains fallback behavior on non-HTMX requests

- Optional enhancements
  - [ ] Replace "Add New <Group>" in-summary Bootstrap modals with HTMX-driven on-demand loading into the feature modal to simplify DOM
  - [ ] Persist/restore accordion collapse state across HTMX swaps if desirable

## Notes
- CSRF is handled via global `hx-headers` in `templates/base.html`
- Tooltips are re-initialized on every HTMX load via `htmx.onLoad` in `templates/base.html`
- Progressive enhancement preserved: `href` and standard form posts remain for non-JS users
