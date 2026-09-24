"""
Local data persistence package: workspace/project/sample/lot/session
hierarchy, manifest + results IO, a rebuildable SQLite search catalog, and
plain-JSON app settings. No Qt imports (CLAUDE.md).

See ``data.workspace.Workspace``, ``data.session_io`` (save_session /
update_session / load_session / import_loose_images), ``data.catalog.Catalog``,
``data.settings`` for the public API.
"""
