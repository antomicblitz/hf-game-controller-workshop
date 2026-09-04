"""Contract tests for the inline two-mode editor page."""

from __future__ import annotations

import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
_session = _here.parent
sys.path.insert(0, str(_session / "tools"))


def html() -> str:
    from tools.editor.frontend import MINIMAL_HTML

    return MINIMAL_HTML


def test_frontend_has_accessible_two_mode_toggle_and_defaults_to_2d():
    source = html()
    assert 'data-view-mode="2d" aria-pressed="true"' in source
    assert 'data-view-mode="3d" aria-pressed="false"' in source
    assert "2D Layout" in source
    assert "3D Review" in source
    assert 'role="group" aria-label="Editor view mode"' in source
    assert "#viewer { visibility: hidden; opacity: 0" in source
    assert "#stage.view-3d #viewer { visibility: visible; opacity: 1" in source


def test_frontend_defaults_3d_shell_to_opaque_with_an_xray_toggle():
    source = html()

    assert 'role="group" aria-label="3D shell appearance"' in source
    assert 'data-shell-view="exterior" aria-pressed="true"' in source
    assert 'data-shell-view="xray" aria-pressed="false"' in source
    assert "#stage.view-3d #shell-view-toggle { display: flex; }" in source
    assert 'getMaterialByName("case_shell")' in source
    assert 'shellView === "xray" ? shellXrayColor[3] : 1' in source
    assert 'shellMaterial.setAlphaMode(shellView === "xray" ? "BLEND" : "OPAQUE")' in source
    assert "setShellView(button.dataset.shellView)" in source
    assert source.index("applyShellAppearance();") < source.index(
        'VIEWER.classList.add("model-ready");'
    )


def test_frontend_renders_canonical_scene_shapes_with_uniform_case_local_scale():
    source = html()
    assert 'id="layout-svg"' in source
    assert "preserveAspectRatio" in source
    assert "Math.min(rect.width / L, rect.height / W)" in source
    assert "sceneNodes()" in source
    assert "sceneRoutes()" in source
    assert "case_dimensions" in source
    assert "layout-shell-envelope" in source
    assert "layout-top-shell" in source
    assert "layout-action-button" in source
    assert "feather_board" in source
    assert "layout-feather" in source
    assert "half_size_breadboard" in source
    assert "layout-breadboard" in source
    assert "grid_columns" in source
    assert "grid_rows" in source
    assert "mounting_holes_mm" in source
    assert "micro_usb_connector" in source
    assert "rear_usb_opening" in source
    assert "layout-unknown" in source
    assert "function buildToScreen" in source
    assert "function screenToBuild" in source
    # Drag-to-move handlers use the same case-local projection helpers.
    assert 'LAYOUT.addEventListener("pointerdown"' in source
    assert "function buildToScreen" in source
    assert "function screenToBuild" in source
    assert "const moves" in source
    assert "drawDragGhost" not in source


def test_frontend_uses_mobility_metadata_and_only_drags_in_2d():
    source = html()
    assert 'node.mobility === "constrained_xy"' in source
    assert "clampMovablePosition" in source
    assert "nodeDimensions(node, true)" in source
    assert 'viewMode !== "2d"' in source
    assert 'LAYOUT.addEventListener("pointerdown"' in source
    assert "new_position" in source
    assert "expected_position" in source
    assert "Math.round(next[0] * 10) / 10" in source
    assert "const moves" in source
    assert "model-viewer" in source
    assert "layoutDrag" in source


def test_frontend_renders_every_canonical_demo_node_and_route_by_id():
    source = html()
    from tools.cadkit.assembly import build_demo_assembly

    assembly = build_demo_assembly().to_dict()
    node_ids = {node["id"] for node in assembly["nodes"]}
    route_ids = {route["id"] for route in assembly["routes"]}

    # The renderer iterates the canonical records and places their stable IDs
    # on the emitted SVG groups; this keeps this contract independent of demo
    # coordinates and retained manifest.elements compatibility fields.
    assert "for (const node of baseNodes)" in source
    assert 'data-element-id="${escapeXml(node.id)}"' in source
    assert "for (const route of sceneRoutes().slice().sort(compareRouteOrder))" in source
    assert 'data-route-id="${escapeXml(route.id)}"' in source
    assert len(node_ids) == len(assembly["nodes"])
    assert len(route_ids) == len(assembly["routes"])
    assert {
        "case.shell",
        "case.bottom",
        "case.top",
        "electronics.breadboard",
        "feather",
        "usb.opening",
        "harness.header_12.connector",
        "harness.header_12.strain_relief",
        "harness.header_12.no_pinch",
        "harness.header_16.connector",
        "harness.header_16.strain_relief",
        "harness.header_16.no_pinch",
        "control.up",
        "control.down",
        "control.right",
        "control.left",
        "control.action_a",
        "control.action_b",
    } <= node_ids
    assert {
        "wire.control_up",
        "wire.control_ground_action_a",
        "wire.control_ground_action_b",
        "wire.harness.header_12",
        "wire.harness.header_16",
        "wire.feather_ground_rail",
    } <= route_ids


def test_frontend_marks_provisional_breadboard_route_contacts() -> None:
    source = html()

    assert ".layout-breadboard-contact.signal" in source
    assert ".layout-breadboard-contact.ground" in source
    assert "layout-breadboard-contact ${contactClass}" in source
    assert 'data-port-name="${escapeXml(port.name)}"' in source
    assert 'String(port.name || "").startsWith("signal_")' in source
    assert 'String(port.name || "").startsWith("gnd_")' in source
    assert 'cy="${-Number(position[1])}"' in source


def test_frontend_projects_button_routes_as_dupont_alligator_cables() -> None:
    source = html()

    assert "function routeCableMetadata(route)" in source
    assert 'cable_kind: "Dupont"' in source
    assert 'termination_kind: "alligator clips"' in source
    assert 'data-cable-kind="${escapeXml(cable.cable_kind)}"' in source
    assert 'data-termination-kind="${escapeXml(cable.termination_kind)}"' in source
    assert "Dupont cables with alligator clips" in source


def test_frontend_keeps_layers_deterministic_and_top_controls_uppermost():
    source = html()
    assert '"top-shell": 30' in source
    assert '"top-controls": 40' in source
    assert "Number(node.layer || 0)" in source
    assert "Number(node.z_order || 0)" in source
    assert "sort(compareNodeOrder)" in source
    assert "if (layerVisibility[nodeLayer(node)]) parts.push(renderNode(node));" in source
    assert "APPROVED · EXACT-SIX ASSEMBLY" in source
    assert 'data-layer="${Number(node.layer || 0)}"' in source
    # Positions belong to the canonical scene; the renderer must not copy the
    # current example's four button coordinates into the page.
    assert "35.0, 20.0" not in source
    assert "55.0, 40.0" not in source


def test_frontend_quick_prompts_use_supported_visible_exterior_profiles():
    source = html()

    assert 'data-chip="snes_curve"' in source
    assert 'data-chip="n64_lobes"' in source
    assert 'data-chip="angular_shell"' in source
    assert 'data-chip="add_grip_space"' not in source
    assert 'data-chip="make_thinner"' not in source
    assert 'data-chip="widen_case"' not in source
    assert 'data-chip="tighten_controls"' not in source
    assert 'data-chip="tighten_buttons"' not in source
    assert "const CHIPS" in source
    assert "snes_inspired" in source
    assert "n64_inspired" in source
    assert "roundness_mm to 6" in source
    assert "FILLET_RADIUS_MM" not in source
    assert "Increase CASE_LENGTH_MM" not in source
    assert "Reduce CASE_THICKNESS_MM" not in source
    assert "Move controls only by dragging them in 2D Layout" in source
    assert "preserve every CONTROLS x/y position" in source
    assert "protected routes are immovable" in source
    assert "USB-C" not in source


def test_frontend_keeps_before_and_after_glbs_for_direct_visual_comparison():
    source = html()

    assert 'role="group" aria-label="Compare generated geometry"' in source
    assert 'data-comparison-view="before"' in source
    assert 'data-comparison-view="after" aria-pressed="true"' in source
    assert "let previousGlbB64 = null;" in source
    assert 'comparisonView === "before" ? previousGlbB64 : glbB64' in source
    assert "previousGlbB64 = previousGlb !== body.glb_b64 ? previousGlb : null;" in source
    assert "3D geometry changed; compare Before and After" in source
    assert 'VIEWER.setAttribute("camera-orbit", "0deg 30deg 85%")' in source


def test_frontend_reset_restores_default_source_and_clears_local_editor_state():
    source = html()

    assert 'id="reset-design"' in source
    assert "Reset design" in source
    assert "Reset exterior and control positions to workshop defaults?" in source
    assert 'fetch("/reset"' in source
    assert "editor_revision: manifest.editor_revision" in source
    assert "await loadModel(body.manifest, body.glb_b64, body.snapshot_b64);" in source
    assert "resetEditorState();" in source
    assert "Reset to the default rounded case and control layout." in source


def test_frontend_initializes_2d_and_3d_from_one_snapshot_revision():
    source = html()
    init = source[
        source.index("async function init()") : source.index("async function jsonResponse")
    ]

    assert "Promise.all" not in init
    assert 'fetch("/manifest?' not in init
    assert 'fetch("/snapshot"' in init
    assert "loadModel(snapshot.manifest, snapshot.glb_b64, snapshot.snapshot_b64);" in init


def test_frontend_surfaces_fatal_constraint_errors_after_retry_exhaustion():
    source = html()

    assert '.filter(v => v.severity === "error")' in source
    assert '.filter(v => v.severity === "fatal")' not in source


def test_frontend_gives_select_mode_to_model_viewer_and_tools_overlay_input():
    source = html()
    assert 'VIEWER.cameraControls = viewMode === "3d" && tool === "select"' in source
    assert "annotationTool || deleteMode" in source
    assert 'OVERLAY.classList.toggle("interactive"' in source
    assert "annotation.view_mode !== viewMode" in source
    assert "view_mode: viewMode" in source
    assert 'VIEWER.cameraOrbit = "35deg 65deg 80%"' in source


def test_internal_component_details_use_tooltips_instead_of_overlapping_labels():
    source = html()
    assert "<title>Feather ·" in source
    assert "-pin stacking header</title>" in source
    assert "control_role" in source
    assert ">${pinCount} HEADER</text>" not in source
    assert "D-PAD" not in source
    assert 'data-signal="${escapeXml(signal)}"' in source
    assert "layout-route-label" in source


def test_frontend_resets_arrays_in_place_after_load_and_submit():
    source = html()
    assert "function resetEditorState" in source
    assert "annotations.length = 0" in source
    assert "moves.length = 0" in source
    assert "await loadModel(body.manifest" in source
    assert "m.elements" not in source


def test_frontend_shows_generation_progress_before_waiting_for_submit() -> None:
    source = html()

    assert 'id="generation-progress"' in source
    assert 'role="status" aria-live="polite"' in source
    assert 'STAGE.setAttribute("aria-busy", "true")' in source
    assert 'SEND.textContent = "Working…"' in source
    assert "generationElapsedSeconds" in source
    assert "beginGenerationProgress(promptText)" in source
    assert "await nextPaint()" in source
    assert source.index("await nextPaint()") < source.index('fetch("/submit"')
    assert "endGenerationProgress()" in source


def test_frontend_reports_visible_2d_and_3d_changes_after_submit() -> None:
    source = html()

    assert 'id="change-summary"' in source
    assert "function describeVisibleChanges" in source
    assert "visual.corner_radius_mm" in source
    assert "candidateManifest.case_dimensions;" in source
    assert "candidateManifest.case_dimensions_mm" not in source
    assert "previousGlbB64 !== nextGlbB64" in source
    assert "2D corner radius" in source
    assert "3D geometry changed; compare Before and After" in source
    assert "No visible geometry change was produced" in source
    assert "showChangeSummary(changeSummary" in source


def test_frontend_feedback_preserves_move_shape_and_selected_context():
    source = html()
    assert (
        "moves.map(move => ({ element_id: move.element_id, new_position: move.new_position,\n     expected_position: move.expected_position }))"
        in source
    )
    assert "selected_element_id" in source
    assert "annotations: annotations.map(annotation => ({" in source
    assert "anchor: annotation.anchor || annotation.center || annotation.start || null" in source
    assert "view_mode: annotation.view_mode" in source


def test_vision_prompt_describes_case_local_moves_and_mode_tagged_marks():
    from tools.agent.vision import VISION_SYSTEM_PROMPT

    assert "exact case-local XY" in VISION_SYSTEM_PROMPT
    assert "view_mode" in VISION_SYSTEM_PROMPT
    assert "screen-space" in VISION_SYSTEM_PROMPT
    assert "not" in VISION_SYSTEM_PROMPT and "3D" in VISION_SYSTEM_PROMPT
    assert "CONTROLS" in VISION_SYSTEM_PROMPT
    assert "control.action_a" in VISION_SYSTEM_PROMPT
    assert "control.action_b" in VISION_SYSTEM_PROMPT
    assert "protected routes are immovable" in VISION_SYSTEM_PROMPT.lower()
    assert "Translating moves" in VISION_SYSTEM_PROMPT
    assert "element_id" in VISION_SYSTEM_PROMPT
    assert "new_position" in VISION_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Bug fixes (Slice 10c): undo, delete, rotation, placement
# ---------------------------------------------------------------------------
def test_undo_button_is_present():
    from tools.editor.frontend import MINIMAL_HTML

    assert 'data-tool="undo"' in MINIMAL_HTML
    assert "ctrlKey" in MINIMAL_HTML or "metaKey" in MINIMAL_HTML


def test_delete_button_is_present():
    from tools.editor.frontend import MINIMAL_HTML

    assert 'data-tool="delete"' in MINIMAL_HTML
    assert "deleteMode" in MINIMAL_HTML
    assert "annotations.find" in MINIMAL_HTML


def test_undo_stack_caps_at_50():
    from tools.editor.frontend import MINIMAL_HTML

    assert "50" in MINIMAL_HTML
    assert "undoStack.shift" in MINIMAL_HTML


def test_overlay_passes_through_for_rotation():
    from tools.editor.frontend import MINIMAL_HTML

    assert "pointer-events: none" in MINIMAL_HTML
    assert "pointer-events: auto" in MINIMAL_HTML
    assert "interactive" in MINIMAL_HTML
    assert "classList.toggle" in MINIMAL_HTML


def test_stage_aspect_ratio_matches_case():
    from tools.editor.frontend import MINIMAL_HTML

    assert "aspect-ratio: 13 / 9" in MINIMAL_HTML
    assert (
        'document.getElementById("stage").style.aspectRatio = `${width} / ${height}`'
        in MINIMAL_HTML
    )
    assert "#stage" in MINIMAL_HTML


def test_drag_start_records_old_position_for_undo():
    from tools.editor.frontend import MINIMAL_HTML

    assert "oldPos" in MINIMAL_HTML
    assert "oldPosition: nodePosition(element).slice(0, 2)" in MINIMAL_HTML
    assert "oldPosition[0]" in MINIMAL_HTML
    assert "oldPosition[1]" in MINIMAL_HTML


def test_esc_key_clears_delete_mode():
    from tools.editor.frontend import MINIMAL_HTML

    assert "Escape" in MINIMAL_HTML
    assert "deleteMode = false" in MINIMAL_HTML


def test_frontend_persists_and_restores_case_local_moves_after_validation():
    source = html()

    assert "const MOVE_STORAGE_KEY = `hf-il3-editor-moves:${CASE_PATH}`;" in source
    assert "window.localStorage.getItem(MOVE_STORAGE_KEY)" in source
    assert "stored.editor_revision !== manifest.editor_revision" in source
    assert "Array.isArray(stored.moves)" in source
    assert "window.localStorage.removeItem(MOVE_STORAGE_KEY)" in source
    assert "JSON.stringify({ editor_revision: manifest.editor_revision, moves })" in source
    assert "moves.push(...restoredMoves)" in source
    assert "const validation = await validateMovesOnServer(moves)" in source

    accepted_move = source.index("moves.push(moveRecord);")
    assert source.index("persistMoves();", accepted_move) < source.index(
        "applyValidatedMoveModel(validation, moveRecord.element_id)", accepted_move
    )


def test_frontend_binds_downloads_to_validated_moved_revision():
    source = html()
    apply_model = source[
        source.index("function applyValidatedMoveModel") : source.index(
            "async function refreshMoveModels"
        )
    ]

    assert 'typeof validation.editor_revision === "string"' in apply_model
    assert "editorRevision = validation.editor_revision" in apply_model
    assert "revisionMoves = validation.revision_moves" in apply_model
    assert "updatePrototypeDownloads();" in apply_model
    assert "&moves=${encodeURIComponent(JSON.stringify(revisionMoves))}" in source


def test_frontend_clear_and_undo_refresh_both_move_models():
    source = html()

    clear = source[source.index('if (tool === "clear")') : source.index('setTool("select");')]
    undo = source[source.index("async function undo()") : source.index("function escapeXml")]
    undo_action_start = source.index("pushUndo({ undo: async () => {")
    undo_action = source[undo_action_start : source.index("}});", undo_action_start)]
    assert "await refreshMoveModels();" in clear
    assert "await refreshMoveModels();" in undo_action
    assert "await action.undo();" in undo

    apply_model = source[
        source.index("function applyValidatedMoveModel") : source.index(
            "async function refreshMoveModels"
        )
    ]
    assert "renderLayout();" in apply_model
    assert "beginViewerLoad(glbB64);" in apply_model


def test_frontend_does_not_complete_replacement_from_stale_loaded_state():
    source = html()

    assert "const replacingLoadedModel = Boolean(VIEWER.src && VIEWER.loaded);" in source
    assert "let observedUnloaded = !replacingLoadedModel;" in source
    assert "if (!VIEWER.loaded) { observedUnloaded = true; return; }" in source
    assert "if (observedUnloaded) onLoad();" in source


def test_frontend_coarse_pointer_contract_sizes_controls_and_limits_touch_suppression():
    source = html()
    assert "@media (pointer: coarse)" not in source
    coarse_start = source.index("@media (any-pointer: coarse)")
    coarse_end = source.index("@media (max-width: 800px) and (any-pointer: coarse)")
    coarse = source[coarse_start:coarse_end]

    assert "#mode-toggle button, #shell-view-toggle button, #comparison-toggle button," in coarse
    assert "#toolbar button, .chip, #reset-design, #send, #prompt-input," in coarse
    assert ".layer-toggle, #elements li { min-width: 44px; min-height: 44px; }" in coarse
    assert "#prompt-input { font-size: 16px; }" in coarse
    assert ".layer-toggle input { width: 24px; height: 24px;" in coarse

    surface_start = source.index("#layout-svg, #annotation-overlay", coarse_start)
    surface_rule = source[surface_start : source.index("}", surface_start) + 1]
    assert "touch-action: none" in surface_rule
    assert "user-select: none" in surface_rule
    assert "-webkit-user-select: none" in surface_rule
    assert "-webkit-touch-callout: none" in surface_rule
    assert "#viewer" not in surface_rule
    assert "#viewer" not in coarse


def test_frontend_serializes_validation_against_state_mutating_controls():
    source = html()

    controls = source[
        source.index("function updateMoveActionControls") : source.index(
            "async function validateMovesOnServer"
        )
    ]
    assert "const disabled = moveValidationPending || moveActionPending;" in controls
    assert (
        'querySelectorAll("#send, #reset-design, #undo-btn, [data-tool=\\"clear\\"]")' in controls
    )
    assert "control.disabled = disabled;" in controls
    assert 'control.setAttribute("aria-disabled", String(disabled));' in controls
    assert "return moveValidationPending || moveActionPending;" in controls

    clear_start = source.index('if (tool === "clear")')
    clear_end = source.index('} else if (tool === "undo")', clear_start)
    clear = source[clear_start:clear_end]
    assert clear.index("if (moveActionsBlocked()) return;") < clear.index("window.confirm")
    assert clear.index("setMoveActionPending(true);") < clear.index("try {")
    assert clear.index("await refreshMoveModels();") < clear.index("finally {")
    assert clear.index("setMoveActionPending(false);") > clear.index("finally {")

    undo_toolbar = source[clear_end : source.index('window.addEventListener("keydown"', clear_end)]
    assert undo_toolbar.index("if (!moveActionsBlocked()) undo();") < undo_toolbar.index("undo();")

    keydown = source[
        source.index('window.addEventListener("keydown"') : source.index("function elementById")
    ]
    assert "event.preventDefault();" in keydown
    assert "undo();" in keydown

    undo = source[source.index("async function undo()") : source.index("function escapeXml")]
    assert undo.index("if (moveActionsBlocked()) return;") < undo.index("undoStack.pop()")
    assert undo.index("setMoveActionPending(true);") < undo.index("try {")
    assert undo.index("await action.undo();") < undo.index("finally {")
    assert undo.index("setMoveActionPending(false);") > undo.index("finally {")

    send_start = source.index('document.getElementById("send").addEventListener')
    send = source[send_start:]
    assert send.index("if (moveActionsBlocked() || layoutDrag) return;") < send.index(
        "setMoveActionPending(true);"
    )
    assert send.index("setMoveActionPending(true);") < send.index("try {")
    assert send.index("setMoveActionPending(false);") > send.index("finally {")

    pointerdown_start = source.index('LAYOUT.addEventListener("pointerdown"')
    pointerdown = source[pointerdown_start : source.index('LAYOUT.addEventListener("pointermove"')]
    assert "moveActionsBlocked()" in pointerdown
    assert pointerdown.index("moveActionsBlocked()") < pointerdown.index("const group")

    validation_sections = (
        source[source.index("const restoredMoves") : source.index("function jsonResponse")],
        source[
            source.index("async function refreshMoveModels") : source.index(
                "function releaseLayoutPointerCapture"
            )
        ],
        source[
            source.index('LAYOUT.addEventListener("pointerup"') : source.index(
                'LAYOUT.addEventListener("pointercancel"'
            )
        ],
    )
    for section in validation_sections:
        assert section.index("setMoveValidationPending(true);") < section.index("try {")
        assert section.index("finally {") < section.rindex("setMoveValidationPending(false);")


def test_frontend_gives_controls_an_enlarged_transparent_hit_target():
    source = html()
    css_start = source.index(".layout-button-hit-target")
    css_rule = source[css_start : source.index("}", css_start) + 1]
    render_button = source[
        source.index("function renderPbsButton(node)") : source.index(
            "function renderFeather(node)"
        )
    ]

    assert "fill: transparent" in css_rule
    assert "stroke: none" in css_rule
    assert "pointer-events: all" in css_rule
    assert 'class="layout-button-hit-target"' in render_button
    assert 'r="${diameter / 2 + 4}"' in render_button
    assert render_button.index("layout-button-hit-target") < render_button.index(
        'class="layout-pbs-button${selected}"'
    )


def test_frontend_tracks_pointer_capture_and_rolls_back_cancelled_drags():
    source = html()
    drag_start = source.index('LAYOUT.addEventListener("pointerdown"')
    drag_end = source.index("function annotationCoords")
    drag_handlers = source[drag_start:drag_end]
    cancel_drag = source[
        source.index("function cancelLayoutDrag") : source.index(
            'LAYOUT.addEventListener("pointerdown"'
        )
    ]
    release = source[
        source.index("function releaseLayoutPointerCapture") : source.index(
            "function revertLayoutDrag"
        )
    ]

    assert "pointerId: event.pointerId" in drag_handlers
    assert "event.pointerId !== layoutDrag.pointerId" in drag_handlers
    assert "try {" in release
    assert (
        "if (LAYOUT.hasPointerCapture(pointerId)) LAYOUT.releasePointerCapture(pointerId);"
        in release
    )
    assert "catch (_error)" in release
    assert "function cancelLayoutDrag(pointerId)" in cancel_drag
    assert "revertLayoutDrag(drag);" in cancel_drag
    assert "layoutDrag = null;" in cancel_drag
    assert (
        'LAYOUT.addEventListener("pointercancel", event => cancelLayoutDrag(event.pointerId));'
        in source
    )
    assert (
        'LAYOUT.addEventListener("lostpointercapture", event => cancelLayoutDrag(event.pointerId));'
        in source
    )


def test_frontend_pointerup_validates_persists_and_refreshes_both_models():
    source = html()
    pointerup_start = source.index('LAYOUT.addEventListener("pointerup"')
    pointerup_end = source.index('LAYOUT.addEventListener("pointercancel"')
    pointerup = source[pointerup_start:pointerup_end]
    apply_model = source[
        source.index("function applyValidatedMoveModel") : source.index(
            "async function refreshMoveModels"
        )
    ]

    assert "const validation = await validateMoveOnServer(moveRecord);" in pointerup
    assert "moves.push(moveRecord);" in pointerup
    assert "persistMoves();" in pointerup
    assert "applyValidatedMoveModel(validation, moveRecord.element_id);" in pointerup
    assert pointerup.index("validateMoveOnServer(moveRecord)") < pointerup.index(
        "moves.push(moveRecord)"
    )
    assert pointerup.index("moves.push(moveRecord)") < pointerup.index("persistMoves()")
    assert pointerup.index("persistMoves()") < pointerup.index("applyValidatedMoveModel")
    assert "renderLayout();" in apply_model
    assert "beginViewerLoad(glbB64);" in apply_model
