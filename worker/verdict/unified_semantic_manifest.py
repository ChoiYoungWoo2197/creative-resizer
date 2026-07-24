"""Stage E-2: Unified Semantic Manifest.

A single authority structure that drives:
  - preserve/removal masks passed to SSC
  - foreground extraction priority
  - layout composition order
  - validation assertions

All downstream decisions (SSC, compositor, validator) reference the same
SemanticManifest instance — no independent copies of role data.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

# ── Role constants ────────────────────────────────────────────────────────────

PRESERVE_ROLES = frozenset({
    "human_subject",
    "product",
    "product_primary",
    "product_secondary",
    "cta",
    "cta_text",
    "title",
    "title_text",
    "brand_logo",
    "foreground_object",
})

REMOVAL_ROLES = frozenset({
    "background",
    "background_gradient",
    "background_solid",
    "scene_element",
    "clutter",
    "decoration",
    "shadow",
    "reflection",
})

TEXT_ROLES = frozenset({
    "title_text",
    "body_text",
    "cta_text",
    "disclaimer_text",
    "text",
})

CTA_GROUP_ROLES = frozenset({
    "cta",
    "cta_text",
    "headline",
    "title",
    "title_text",
})

MANIFEST_VERSION = "e2.0"


# ── Manifest dataclass ────────────────────────────────────────────────────────

@dataclass
class SemanticManifest:
    """Unified semantic authority for one job+spec rendering pipeline.

    Built from D-2 virtual foreground extraction results and object roles.
    All downstream stages reference this single instance.

    P0-C: Once finalize() is called, semantic role fields are frozen.
    Any attempt to mutate them via try_mutate_field() raises
    MANIFEST_MUTATION_AFTER_FINALIZE and increments the counter.
    """
    job_id: str = ""
    spec_id: str = ""
    preserve_roles: list = field(default_factory=list)
    removal_roles: list = field(default_factory=list)
    preserve_object_ids: list = field(default_factory=list)
    removal_object_ids: list = field(default_factory=list)
    cta_group_ids: list = field(default_factory=list)
    text_human_contradictions: list = field(default_factory=list)
    mask_conflict_ids: list = field(default_factory=list)
    object_count: int = 0
    manifest_sha256: str = ""
    version: str = MANIFEST_VERSION
    # P0-C: Finalization guard
    finalized: bool = False
    manifest_owner: str = "worker"
    manifest_mutation_count_after_finalization: int = 0


# ── Builder ───────────────────────────────────────────────────────────────────

def build_semantic_manifest(
    *,
    job_id: str = "",
    spec_id: str = "",
    d2_fg_layers: list | None = None,
) -> SemanticManifest:
    """Build SemanticManifest from D-2 foreground layer list."""
    layers = d2_fg_layers or []
    preserve_roles_seen: set[str] = set()
    removal_roles_seen: set[str] = set()
    preserve_ids: list[str] = []
    removal_ids: list[str] = []
    cta_ids: list[str] = []
    contradictions: list[str] = []
    conflict_ids: list[str] = []

    for layer in layers:
        oid = layer.get("objectId") or layer.get("object_id") or ""
        role = (layer.get("semanticRole") or layer.get("semantic_role") or "").lower()
        layout_role = (layer.get("layoutRole") or layer.get("layout_role") or "").lower()

        if role in PRESERVE_ROLES or layout_role in PRESERVE_ROLES:
            preserve_roles_seen.add(role or layout_role)
            if oid:
                preserve_ids.append(oid)

        if role in REMOVAL_ROLES or layout_role in REMOVAL_ROLES:
            removal_roles_seen.add(role or layout_role)
            if oid:
                removal_ids.append(oid)

        if (role in CTA_GROUP_ROLES or layout_role in CTA_GROUP_ROLES) and oid:
            cta_ids.append(oid)

        # Text-as-human contradiction detection
        if (role in TEXT_ROLES or layout_role in TEXT_ROLES):
            md = layer.get("metadata") or {}
            is_human = md.get("is_human") or md.get("isHuman") or False
            if is_human and oid:
                contradictions.append(oid)

    # Conflict: same objectId in both preserve and removal
    preserve_set = set(preserve_ids)
    removal_set = set(removal_ids)
    conflict_ids = sorted(preserve_set & removal_set)

    manifest_sha = _sha256_manifest(
        job_id, spec_id, preserve_ids, removal_ids, cta_ids
    )

    return SemanticManifest(
        job_id=job_id,
        spec_id=spec_id,
        preserve_roles=sorted(preserve_roles_seen),
        removal_roles=sorted(removal_roles_seen),
        preserve_object_ids=preserve_ids,
        removal_object_ids=removal_ids,
        cta_group_ids=cta_ids,
        text_human_contradictions=contradictions,
        mask_conflict_ids=conflict_ids,
        object_count=len(layers),
        manifest_sha256=manifest_sha,
        version=MANIFEST_VERSION,
    )


def _sha256_manifest(job_id, spec_id, preserve, removal, cta):
    payload = json.dumps({
        "j": job_id, "s": spec_id,
        "p": sorted(preserve), "r": sorted(removal), "c": sorted(cta),
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ── Logging ───────────────────────────────────────────────────────────────────

def log_preserve_mask(manifest: SemanticManifest, *, job_id: str = "", spec_id: str = "") -> None:
    jid = job_id or manifest.job_id
    sid = spec_id or manifest.spec_id
    print(
        f"[PRESERVE_MASK] jobId={jid} specId={sid}"
        f" roles={manifest.preserve_roles}"
        f" objectCount={len(manifest.preserve_object_ids)}"
        f" manifestSha={manifest.manifest_sha256}",
        flush=True,
    )


def log_removal_mask(manifest: SemanticManifest, *, job_id: str = "", spec_id: str = "") -> None:
    jid = job_id or manifest.job_id
    sid = spec_id or manifest.spec_id
    print(
        f"[REMOVAL_MASK] jobId={jid} specId={sid}"
        f" roles={manifest.removal_roles}"
        f" objectCount={len(manifest.removal_object_ids)}"
        f" manifestSha={manifest.manifest_sha256}",
        flush=True,
    )


def log_mask_conflict(manifest: SemanticManifest, *, job_id: str = "", spec_id: str = "") -> None:
    jid = job_id or manifest.job_id
    sid = spec_id or manifest.spec_id
    if manifest.mask_conflict_ids:
        print(
            f"[MASK_CONFLICT] jobId={jid} specId={sid}"
            f" conflictCount={len(manifest.mask_conflict_ids)}"
            f" objectIds={manifest.mask_conflict_ids}",
            flush=True,
        )


def log_semantic_group(
    manifest: SemanticManifest,
    *,
    group_type: str = "cta_title",
    job_id: str = "",
    spec_id: str = "",
) -> None:
    jid = job_id or manifest.job_id
    sid = spec_id or manifest.spec_id
    print(
        f"[SEMANTIC_GROUP] jobId={jid} specId={sid}"
        f" groupType={group_type}"
        f" objectCount={len(manifest.cta_group_ids)}"
        f" objectIds={manifest.cta_group_ids}",
        flush=True,
    )


def log_text_human_contradictions(
    manifest: SemanticManifest,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    if not manifest.text_human_contradictions:
        return
    jid = job_id or manifest.job_id
    sid = spec_id or manifest.spec_id
    print(
        f"[TEXT_HUMAN_CONTRADICTION] jobId={jid} specId={sid}"
        f" count={len(manifest.text_human_contradictions)}"
        f" objectIds={manifest.text_human_contradictions}",
        flush=True,
    )


def emit_all_manifest_logs(
    manifest: SemanticManifest,
    *,
    job_id: str = "",
    spec_id: str = "",
) -> None:
    """Emit all applicable manifest log lines in standard order."""
    if manifest.preserve_object_ids or manifest.preserve_roles:
        log_preserve_mask(manifest, job_id=job_id, spec_id=spec_id)
    if manifest.removal_object_ids or manifest.removal_roles:
        log_removal_mask(manifest, job_id=job_id, spec_id=spec_id)
    if manifest.mask_conflict_ids:
        log_mask_conflict(manifest, job_id=job_id, spec_id=spec_id)
    if manifest.cta_group_ids:
        log_semantic_group(manifest, group_type="cta_title", job_id=job_id, spec_id=spec_id)
    if manifest.text_human_contradictions:
        log_text_human_contradictions(manifest, job_id=job_id, spec_id=spec_id)


# ── P0-C: Manifest finalization guard ────────────────────────────────────────

# Fields that are frozen after finalize()
_IMMUTABLE_FIELDS = frozenset({
    "preserve_roles",
    "removal_roles",
    "preserve_object_ids",
    "removal_object_ids",
    "cta_group_ids",
    "mask_conflict_ids",
    "text_human_contradictions",
})


def finalize(manifest: SemanticManifest) -> None:
    """Mark the manifest as finalized. After this point, role fields are frozen.

    Downstream stages (SSC, compositor, validator) must reference this manifest
    without mutating it. Use try_mutate_field() to detect violations.
    """
    manifest.finalized = True


def try_mutate_field(manifest: SemanticManifest, field_name: str, value: object) -> None:
    """Attempt to mutate a manifest field.

    If the manifest is finalized and the field is in the immutable set,
    increments manifest_mutation_count_after_finalization and raises
    MANIFEST_MUTATION_AFTER_FINALIZE.

    For non-immutable fields or non-finalized manifests, sets the value normally.
    """
    if manifest.finalized and field_name in _IMMUTABLE_FIELDS:
        manifest.manifest_mutation_count_after_finalization += 1
        raise RuntimeError(
            f"MANIFEST_MUTATION_AFTER_FINALIZE:"
            f" field={field_name!r}"
            f" manifestSha={manifest.manifest_sha256}"
            f" mutationCount={manifest.manifest_mutation_count_after_finalization}"
        )
    setattr(manifest, field_name, value)
