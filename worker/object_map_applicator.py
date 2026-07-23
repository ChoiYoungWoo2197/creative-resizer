"""Object Map Applicator — Stage 21.5

Applies stored GPT object analysis (PsdObjectAnalysis.objects) to classified PSD layers,
overriding heuristic roles with analysis-backed roles.

Matching priority:
  1. Exact layerId match        (matchedLayerId == layer["id"])  — always enabled
  2. layerName + layerType      (name_fallback)                  — non-strict only
  3. textContent match          (text_content_fallback)          — non-strict only

Modes:
  strict=False (default)
    - All three methods allowed.
    - Methods 2/3 log WARNING to signal fallback usage.
    - No confidence threshold (accepts any confidence if matchStatus is valid).

  strict=True  (activated by --require-analysis in Golden Batch)
    - Only method 1 (layerId exact) is allowed.
    - Methods 2/3 are REJECTED (logged, not applied).
    - Confidence must be >= min_confidence (default 0.8).
    - Entries with confidence < min_confidence are SKIPPED.

Rules (both modes):
  - Only entries with matchStatus "ready" or "matched_low_confidence" are considered.
  - "missing_layer" entries are always skipped.
  - "unknown" or None role entries are always skipped.
  - Every fallback match is explicitly logged — no silent application.

Returns (updated_layers, apply_logs) where each log entry contains:
  layerId, layerName, layerType, oldRole, newRole, matchMethod, confidence,
  applied, rejectReason (if not applied)
"""
from __future__ import annotations

_APPLICABLE_MATCH_STATUSES = {"ready", "matched_low_confidence"}
_DEFAULT_STRICT_MIN_CONFIDENCE = 0.8


def apply_object_map(
    classified_layers: list,
    object_results: list,
    strict: bool = False,
    min_confidence: float | None = None,
) -> tuple[list, list]:
    """Apply Object Map role overrides to classified layers.

    Args:
        classified_layers: layer dicts from classify_layers()
        object_results:    ObjectResult dicts from PsdObjectAnalysis.objects snapshot
        strict:            When True, only layerId exact match is applied.
                           name/text fallbacks are rejected.
        min_confidence:    Minimum confidence threshold for applying a match.
                           Defaults to 0.8 in strict mode, 0.0 in non-strict.
    Returns:
        (updated_layers, apply_logs)
    """
    if not object_results:
        return classified_layers, []

    # Resolve effective confidence threshold
    if min_confidence is None:
        effective_min_conf = _DEFAULT_STRICT_MIN_CONFIDENCE if strict else 0.0
    else:
        effective_min_conf = min_confidence

    # Build lookup indices from object_results
    # Always build all indices — strict mode rejects fallback matches AFTER lookup.
    by_layer_id: dict[str, dict] = {}       # matchedLayerId → entry
    by_name_type: dict[tuple, dict] = {}    # (lower_name, "") → entry
    by_text_content: dict[str, dict] = {}   # textContent/recommendedLayerName → entry

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

        # Long recommendedLayerName hint for textContent matching
        text_hint = mname if len(mname) > 10 else rlabel

        if mid and not _is_placeholder(mid):
            by_layer_id[mid] = entry

        # Fallback indices are always populated; strict mode rejects after lookup
        if mname:
            by_name_type[(mname.lower(), "")] = entry
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

        # Priority 1: exact layerId (always allowed)
        if lid and lid in by_layer_id:
            match_entry = by_layer_id[lid]
            match_method = "layerId_exact"

        # Priority 2: name + type fallback
        # Always looked up — strict mode REJECTS the result below, non-strict applies with WARNING.
        if match_entry is None:
            key_any = (lname.lower(), "")
            if key_any in by_name_type:
                match_entry = by_name_type[key_any]
                match_method = "name_fallback"

        # Priority 3: textContent match (text layers only)
        # Always looked up — strict mode REJECTS the result below.
        if match_entry is None and ltype in ("type", "text") and ltext:
            if ltext in by_text_content:
                match_entry = by_text_content[ltext]
                match_method = "text_content_fallback"
            elif match_entry is None:
                for tc_key, tc_entry in by_text_content.items():
                    if len(tc_key) >= 5 and (
                        ltext.startswith(tc_key) or tc_key.startswith(ltext[:8])
                    ):
                        match_entry = tc_entry
                        match_method = "text_content_partial"
                        break

        # If strict and match_method would be a fallback, reject explicitly
        if strict and match_entry is not None and match_method != "layerId_exact":
            print(
                f"[OBJECT_MAP_APPLY] STRICT_REJECT method={match_method}"
                f" layerName={lname!r} type={ltype}: name/text fallback forbidden in strict mode",
                flush=True,
            )
            apply_logs.append({
                "layerId":      lid,
                "layerName":    lname,
                "layerType":    ltype,
                "oldRole":      old_role,
                "newRole":      match_entry.get("role"),
                "matchMethod":  match_method,
                "confidence":   match_entry.get("confidence") or 0.0,
                "applied":      False,
                "rejectReason": "strict_no_name_fallback",
            })
            continue

        if match_entry is None:
            continue

        new_role = match_entry.get("role")
        confidence = match_entry.get("confidence") or 0.0

        # Confidence gate
        if confidence < effective_min_conf:
            reason = f"confidence_{confidence:.2f}_below_threshold_{effective_min_conf:.2f}"
            print(
                f"[OBJECT_MAP_APPLY] CONF_REJECT layerId={lid!r} name={lname!r}"
                f" role={new_role!r} conf={confidence:.2f} < {effective_min_conf:.2f}",
                flush=True,
            )
            apply_logs.append({
                "layerId":      lid,
                "layerName":    lname,
                "layerType":    ltype,
                "oldRole":      old_role,
                "newRole":      new_role,
                "matchMethod":  match_method,
                "confidence":   confidence,
                "applied":      False,
                "rejectReason": reason,
            })
            continue

        log_entry = {
            "layerId":      lid,
            "layerName":    lname,
            "layerType":    ltype,
            "oldRole":      old_role,
            "newRole":      new_role,
            "matchMethod":  match_method,
            "confidence":   confidence,
            "applied":      False,
            "rejectReason": None,
        }

        # Human immutable guard: pixel/smartobject layers originally classified as
        # human_subject cannot be downgraded to any other role by Object Map.
        # AI generates pixels in removal_mask areas; downgrading human_subject to
        # a removal role would cause AI to overwrite the person/hand pixels.
        if old_role == "human_subject" and ltype in ("pixel", "smartobject") and new_role != "human_subject":
            print(
                f"[OBJECT_MAP_APPLY] HUMAN_IMMUTABLE_REJECT"
                f" layerId={lid!r} name={lname!r}"
                f" attempted {old_role!r}->{new_role!r}: human_subject is immutable",
                flush=True,
            )
            log_entry["rejectReason"] = "human_subject_immutable"
            apply_logs.append(log_entry)
            continue

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


def _is_placeholder(value: str) -> bool:
    """Return True if the value is a placeholder that should not be used for matching."""
    if not value:
        return True
    markers = ("__POPULATE_FROM_SERVER__", "__PLACEHOLDER__", "__GENERATE_FROM_SERVER__")
    return any(value.startswith(m) for m in markers)


def has_placeholders(fixture: dict) -> list[str]:
    """Return list of placeholder field paths in a fixture dict.

    Used to detect unfilled fixtures before applying them in strict mode.
    """
    problems: list[str] = []
    src = fixture.get("sourceFileSha256", "")
    if _is_placeholder(src):
        problems.append(f"sourceFileSha256={src!r}")

    objects = fixture.get("objects") or []
    for i, obj in enumerate(objects):
        mid = obj.get("matchedLayerId", "")
        if _is_placeholder(mid):
            problems.append(f"objects[{i}].matchedLayerId={mid!r}")

    return problems
