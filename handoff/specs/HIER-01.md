# HIER-01 — User-defined folder hierarchy and naming (user request 2026-09-24)

## User's words
"I would like the file nesting structure to reflect my current workflow … allow users to edit the file nesting and naming structure. The naming structure set by the user should update the report template and exports. My current workflow is nested as: Job # › Part Number › Lot — images are stored in the lot. The tables generated for the report should use this information in naming and link to image names as well."

## Concept: a Hierarchy Profile per workspace
Stored in `<workspace root>/workspace.json` (so every PC/operator using the same workspace sees the same structure). Atomic write, schema_version.

```python
# data/hierarchy.py  (no Qt)
@dataclass
class FieldDef:
    key: str                 # "customer", "material", "heat_number" …  (snake_case, unique per level)
    label: str               # "Customer"
    kind: str = "text"       # text | date | number | choice
    choices: list[str] = []
    required: bool = False

@dataclass
class LevelDef:
    key: str                 # internal slot: "project" | "sample" | "lot"   (v3.0: exactly 3 levels)
    label: str               # user-facing name: "Job #", "Part Number", "Lot"
    id_label: str            # label of the identifying value, e.g. "Job number"
    fields: list[FieldDef]   # extra metadata fields shown in wizard/metadata panel/report
    folder_template: str = "{id}"     # folder name built from the level's id + fields, e.g. "{id}" or "{id} - {customer}"

@dataclass
class HierarchyProfile:
    schema_version: int = 1
    name: str                 # "Job › Part › Lot"
    levels: list[LevelDef]    # len == 3
    images_location: str      # "lot" (images + results stored directly in the lot folder) | "session" (timestamped run subfolders, legacy)
    session_folder_template: str = "{date:%Y-%m-%d_%H%M%S}{label: - }"   # used only when images_location == "session"
    image_name_template: str = "{original}"      # how imported image copies are named, e.g. "{lot}_{index:02}" ; "{original}" keeps file names
    export_name_template: str = "{project}_{sample}_{lot}_Grain_Report_{date:%Y%m%d}"   # base name for .xlsx/.pptx
    report_title_template: str = "Grain Size Report — {sample_label} {sample}, {lot_label} {lot}"
    def to_dict(); from_dict()   # unknown keys ignored, missing defaulted

PRESETS = {
  "job_part_lot": Job # › Part Number › Lot, images_location="lot",
       level fields: job → customer, PO number; part → part description, material/alloy, drawing revision; lot → heat number, supplier, received date, quantity
       templates: export "{project}_{sample}_{lot}_Grain_Report_{date:%Y%m%d}", title "Grain Size Report — Part {sample}, Lot {lot} (Job {project})", image "{original}"
  "project_sample_lot_session": today's structure (Project › Sample › Lot › timestamped Session), images_location="session"
}
DEFAULT for NEW workspaces: "job_part_lot" (the user's workflow). EXISTING workspaces with no workspace.json: "project_sample_lot_session" (nothing moves).

load_profile(root) -> HierarchyProfile ; save_profile(root, profile)
render_template(template, context: dict, *, for_filename: bool) -> str
    # str.format-like with {key}, {key:%Y%m%d} for dates, {key:02} for ints, and {key: - } meaning "prefix ' - ' only if key non-empty";
    # missing keys -> ""; never raises on bad templates (fall back + report error via validate_template);
    # for_filename=True -> sanitize with data.models.sanitize_name, collapse repeated separators, strip trailing "_ -."
validate_template(template, available_keys) -> list[str problems]
context_for_session(loaded_session_or_path) -> dict
    # keys: project, sample, lot (id values), project_label, sample_label, lot_label (level labels),
    # every level field as "<levelkey>_<fieldkey>" (e.g. "lot_heat_number"), operator, date (datetime), label, instrument, index/original for images
```
Internal slot names stay project/sample/lot (no mass rename of the data layer); ONLY labels, fields and templates are user-facing.

## Images stored in the lot (images_location == "lot")
- The lot folder itself is the session: `manifest.json`, `images/`, `results/`, `thumbs/`, `report.json`, `exports/` live directly in the lot folder. One continuous record per lot; re-analysis updates it; adding images later appends.
- `save_session(lot_path, ..., in_place=True)` writes into the lot folder; `list_sessions(lot)` returns `[lot]` when the lot has a manifest; `load_session(lot_path)` works unchanged; catalog indexes it; trash/restore of a lot covers it.
- Optional history: when re-analysing, previous results are moved to `results/_history/<timestamp>/` (keep last 5) so nothing is silently lost.

## Reports & exports must use the profile
- ReportModel gets `hierarchy: list[{key, label, value}]` (ordered levels), and `export_name_template`, `image_name_template`, `title_template` (copied from the profile at build time, editable in the report designer).
- Excel Overview: header block shows each level label: value ("Job #: 24-117 | Part Number: 7718-A | Lot: L-44A"); the per-image table has columns for the level values using the USER'S labels (instead of hard-coded "Sample"/"Lot"); Image column text = image display name from image_name_template; it hyperlinks to that image's sheet, and a second "File" column hyperlinks to the image file on disk (local path, relative to the workbook when the export lives inside the lot/session folder so links survive moving the whole job folder).
- Per-image sheet names and slide titles use the image display name; PPTX title slide and footer use the level labels/values.
- Default export filename = render_template(export_name_template, context, for_filename=True) + ".xlsx"/".pptx".
- Report title default = render_template(report_title_template, context).

## UI
- Settings → "Folder structure & naming": pick preset or customise: rename the three levels, edit each level's metadata fields (add/remove/reorder, type, required), folder name template, images location (lot vs timestamped runs), image name template, export name template, report title template — each template field has a live preview using the current selection and a token picker (click to insert {lot}, {sample_label}, dates …) and inline validation.
- Changing templates affects NEW folders/exports; an explicit "Rename existing folders to match" action previews old→new and applies with trash-safe moves (undoable), never automatic.
- Everywhere the UI says Project/Sample/Lot/Session (tree, breadcrumb, wizard steps, metadata panel, search results, cards, report designer, status texts) it uses the profile labels. With images_location == "lot" the Session level disappears from tree, breadcrumb and wizard (the wizard's last step is "Lot" + images).

## Acceptance
- New workspace defaults to Job # › Part Number › Lot with images in the lot; creating Job 24-117 › Part 7718-A › Lot L-44A and importing 3 images produces `24-117/7718-A/L-44A/{images,results,manifest.json,…}`.
- Excel overview columns read "Job #", "Part Number", "Lot"; image names link to their sheets and to the files; default export name "24-117_7718-A_L-44A_Grain_Report_20260924.xlsx".
- Renaming the levels in Settings (e.g. "Work Order") updates tree, wizard, report designer and the next export without restarting.
- Existing workspaces keep working unchanged (legacy preset).
