# Research Findings (2026-09-23)

Condensed from two research agents. Items marked ⚠ were flagged as unverified by the researchers.

## Claude Code tooling
- Custom subagents: `.claude/agents/NAME.md`, frontmatter keys confirmed: `name`, `description` (keep terse — combined cap ~15k tokens), `tools`, `disallowedTools`, `model` (`sonnet|opus|haiku|fable|inherit`), `permissionMode`, `maxTurns`, `skills`, `memory`, `isolation: worktree`, `effort`, `background`. Docs: https://code.claude.com/docs/en/sub-agents
- Skills: `.claude/skills/<name>/SKILL.md` with `name`, `description`, `allowed-tools`, optional `context: fork`. Docs: https://code.claude.com/docs/en/skills
- Orchestration guidance (Anthropic multi-agent research post; agent-teams docs): self-contained prompts, cheap models for mechanical work, orchestrator sees summaries not raw output, ≤3–4 live agents, handoff files instead of transcript replay. This brief follows those rules.
- Official plugin marketplace (anthropics/claude-plugins-official): nothing PyQt/installer-specific ⚠; `claude-md-management` may help maintain CLAUDE.md. Community: VoltAgent/awesome-claude-code-subagents (`qa-expert`, `test-automator`, `documentation-engineer`, `product-manager`), wshobson/agents (`python-pro`). We wrote project-specific agents instead of importing generic ones.
- Worktree isolation was considered and rejected: the 5 GB `.venv` is untracked, so worktrees could not run tests.

## Libraries and licences
| Library | Licence | Decision |
|---------|---------|----------|
| PyQt6 | GPL v3 **or** commercial (Riverbank) | ⚠ Corporate closed distribution likely needs a commercial licence, or migrate to PySide6 (LGPL). **User decision D-03.** |
| PyQt6-Fluent-Widgets | GPL v3 / commercial | **Rejected** |
| qt-material | BSD-2 | allowed (optional) |
| PyQtDarkTheme | MIT (maintenance gaps ⚠) | allowed (optional) |
| QtAwesome | MIT; fonts SIL OFL | **Use** for icons |
| xlsxwriter | BSD-2 | **Use** for Excel: `set_x_axis({'name':…,'num_format':…})`, `set_tab_color`, gridlines, chart sheets; write-only is fine (fresh workbook each export) |
| openpyxl | MIT | keep for reading only |
| python-pptx 1.0.x | MIT | **Use** for PPTX: `add_picture`, `add_chart` (native, editable), axis titles via `chart.category_axis.axis_title`, template via `Presentation("template.pptx")` |
| Native Qt animation | (Qt) | `QPropertyAnimation`, `QGraphicsOpacityEffect`, `QParallelAnimationGroup`, `QVariantAnimation`, custom splash instead of `QSplashScreen` |

## Black-region filtering (SAM + classical)
- `SamAutomaticMaskGenerator` knobs: `pred_iou_thresh` (default 0.88; app uses 0.80), `stability_score_thresh` (default 0.95; app uses 0.88), `min_mask_region_area`, `points_per_side`, `box_nms_thresh`. Raising them reduces spurious masks generally but does not target black regions.
- Standard post-filters (apply in all pipelines): mean-intensity floor, intensity std-dev floor (uniform regions have none), area-fraction limits, rejection of masks on the black frame border/vignette, per-mask `predicted_iou`/`stability_score` gates. Thresholds must be tuned empirically on the lab's images ⚠ — expose in UI.

## Data management
- No public spec for Keyence/Zeiss/Olympus internal project formats ⚠; general pattern is Project → Sample → Measurement with an embedded DB or observation file.
- Recommended: folder hierarchy + `manifest.json` per session (portable, crash-resilient, network-share friendly) + SQLite catalog for search (WAL, `timeout=5`, rebuildable). Suggested manifest fields: sample_id, lot_number, project, operator, date (ISO 8601), instrument, magnification, accelerating_voltage, working_distance, pixel_size, detector_mode, notes, software_version, detection_params, per-image stats.

## Packaging
- PyInstaller + torch: onedir (already), CPU-only wheel to shrink bundle, `--debug=imports` to find hidden imports, exclude `torch.distributed`/`torch.testing`. python-pptx/xlsxwriter/qtawesome are low-risk; add `collect_data_files('qtawesome')` for fonts. Keep SAM checkpoint as bundled data (already in spec).

## Competitor reference (for the innovator)
Keyence VHX grain analysis, Zeiss ZEN Intellesis, Olympus Stream, Clemex Vision, MIPAR, ImageJ/Fiji, Gatan DigitalMicrograph.
