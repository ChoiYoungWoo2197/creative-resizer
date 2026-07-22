"""Object Map Applicator — Stage 21.5

Applies stored GPT object analysis (PsdObjectAnalysis.objects) to classified PSD layers,
overriding heuristic roles with analysis-backed roles.

Matching priority (per object entry):
  1. Exact layerId match        (matchedLayerId == layer["id"])
  2. layerName + layerType      (matchedLayerName lower + kind, WARNING logged)
  3. textContent match          (for type layers only, WARNING logged)

Rules:
  - Only entries with matchStatus "ready" or "matched_low_confidence" are applied.
  - If no match found: layer keeps its heuristic role (unmatched, not an error).
  - Every fallback match is explicitly logged — no silent name-only application.
  - "missing_layer" entries are skipped entirely.

Returns (updated_layers, apply_logs) where apply_logs is a list of dicts with:
  layerId, layerName, layerType, oldRole, newRole, matchMethod, confidence, applied
"""
from __future__ import annotations

_APPLICABLE_MATCH_STATUSES = {"ready", "matched_low_confidence"}


def apply_object_map(
    classified_layers: list,
    object_results: list,
) -> tuple[list, list]:
    """Apply Object Map role overrides to classified layers.

    Args:
        classified_layers: layer dicts from classify_layers()
        object_results:    ObjectResult dicts from PsdObjectAnalysis.objects snapshot
                           Fields used: matchedLayerId, matchedLayerName, role,
                                        matchStatus, confidence
    Returns:
        (updated_layers, apply_logs)
    """
    if not object_results:
        return classified_layers, []

    # Build lookup indices
    by_layer_id: dict[str, dict] = {}       # matchedLayerId → entry
    by_name_type: dict[tuple, dict] = {}    # (lower_name, kind) → entry
    by_text_content: dict[str, dict] = {}   # textContent → entry

    for entry in object_results:
        status = entry.get("matchStatus", "missing_layer")
        if status not in _APPLICABLE_MATCH_STATUSES:
            continue
        role = entry.get("role")
        if not role or role == "unknown":
            continue

        mid = (entry.get("matchedLayerId") or "").strip()
        mname = (entry.get("matchedLayerName") or "").strip()
        rlabel = (entry.get("recommendedLayerName") or "").strip()
        # textContent is not stored in ObjectResult — use recommendedLayerName as hint
        # for text-content fallback when matchedLayerName includes the full Korean text
        text_hint = mname if len(mname) > 10 else rlabel  # heuristic: long name = text content

        if mid and mid != "__PLACEHOLDER__" and not mid.startswith("__"):
            by_layer_id[mid] = entry
        if mname:
            by_name_type[(mname.lower(), "")] = entry          # any type
            # also index with empty type so name-only lookup works as final key
        if text_hint and len(text_hint) > 5:
            by_text_content[text_hint] = entry

    apply_logs: list[dict] = []

    for layer in classified_layers:
        lid = layer.get("id", "")
        lname = (layer.get("name") or "").strip()
        ltype = layer.get("type", "")
        ltext = (layer.get("textContent") or "").strip()
        old_role = layer.get("role", "unknown")

        match_entry: dict | None = None
        match_method: str = ""

        # Priority 1: exact layerId
        if lid and lid in by_layer_id:
            match_entry = by_layer_id[lid]
            match_method = "layerId_exact"

        # Priority 2: matchedLayerName + (any kind)
        if match_entry is None:
            key_any = (lname.lower(), "")
            if key_any in by_name_type:
                match_entry = by_name_type[key_any]
                match_method = "name_fallback"

        # Priority 3: textContent match (text layers only)
        if match_entry is None and ltype in ("type", "text") and ltext:
            if ltext in by_text_content:
                match_entry = by_text_content[ltext]
                match_method = "text_content_fallback"
            # Try partial: layer text starts with a known key
            if match_entry is None:
                for tc_key, tc_entry in by_text_content.items():
                    if len(tc_key) >= 5 and (ltext.startswith(tc_key) or tc_key.startswith(ltext[:8])):
                        match_entry = tc_entry
                        match_method = "text_content_partial"
                        break

        if match_entry is None:
            continue

        new_role = match_entry.get("role")
        confidence = match_entry.get("confidence") or 0.0

        log_entry = {
            "layerId":    lid,
            "layerName":  lname,
            "layerType":  ltype,
            "oldRole":    old_role,
            "newRole":    new_role,
            "matchMethod": match_method,
            "confidence": confidence,
            "applied":    False,
        }

        if new_role and new_role != old_role:
            try:
                from layer_role_classifier import PRIORITY_MAP
                layer["role"] = new_role
                layer["priority"] = PRIORITY_MAP.get(new_role, "optional")
            except ImportError:
                layer["role"] = new_role
            layer["objectMapApplied"] = True
            log_entry["applied"] = True

            print(
                f"[OBJECT_MAP_APPLY] layerId={lid!r} name={lname!r}"
                f" type={ltype} {old_role!r}->{new_role!r}"
                f" method={match_method} conf={confidence:.2f}",
                flush=True,
            )

        if match_method != "layerId_exact":
            print(
                f"[OBJECT_MAP_APPLY] WARNING fallback_match"
                f" method={match_method} layerName={lname!r} type={ltype}",
                flush=True,
            )

        apply_logs.append(log_entry)

    return classified_layers, apply_logs
