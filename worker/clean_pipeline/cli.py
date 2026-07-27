"""CLI entry point.

Usage:
  python -m worker.clean_pipeline.cli health
  python -m worker.clean_pipeline.cli prepare-source --input <path> --output-dir <dir>
  python -m worker.clean_pipeline.cli analyze --job-dir <dir> [--api-key <key>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid


def cmd_health() -> None:
    print("clean_pipeline: OK")


def cmd_prepare_source(args: argparse.Namespace) -> None:
    from clean_pipeline.pipeline_logger import PipelineLogger
    from clean_pipeline.source.canonical_source import prepare

    job_id = str(uuid.uuid4())[:8]
    logger = PipelineLogger(job_id)

    result, meta = prepare(args.input, args.output_dir, job_id, logger)

    if result.status.value == "PASS":
        print(f"\njob_id   : {job_id}")
        print(f"canonical: {meta.canonical_path}")
        print(f"size     : {meta.width}x{meta.height}")
        print(f"mode     : {meta.mode}")
        print(f"sha256   : {meta.sha256}")
    else:
        print(f"\nFAIL — {result.reasons}", file=sys.stderr)
        sys.exit(1)


def cmd_analyze(args: argparse.Namespace) -> None:
    from clean_pipeline.analysis.openai_analyzer import analyze
    from clean_pipeline.pipeline_logger import PipelineLogger

    job_dir = args.job_dir
    source_json_path = f"{job_dir}/clean_v1/01_source/source.json"
    canonical_path = f"{job_dir}/clean_v1/01_source/canonical.png"

    try:
        with open(source_json_path, encoding="utf-8") as f:
            src = json.load(f)
    except FileNotFoundError:
        print(f"FAIL — source.json not found: {source_json_path}", file=sys.stderr)
        sys.exit(1)

    api_key = args.api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("BACKGROUND_AI_API_KEY", "")

    # Derive job_id and output_dir from job_dir path
    import pathlib
    job_path = pathlib.Path(job_dir)
    job_id = job_path.name
    output_dir = str(job_path.parent)

    logger = PipelineLogger(job_id)

    result, manifest = analyze(
        canonical_path=canonical_path,
        image_width=src["width"],
        image_height=src["height"],
        source_sha256=src["sha256"],
        api_key=api_key,
        output_dir=output_dir,
        job_id=job_id,
        logger=logger,
    )

    if result.status.value == "PASS":
        print(f"\njob_id  : {job_id}")
        print(f"objects : {manifest.objectCount if hasattr(manifest, 'objectCount') else len(manifest.objects)}")
        print(f"manifest: {result.artifacts.get('manifest', '')}")
    else:
        print(f"\nFAIL — {result.reasons}", file=sys.stderr)
        sys.exit(1)


def cmd_run(args: argparse.Namespace) -> None:
    import json as _json
    import uuid

    from clean_pipeline.contracts import CleanPipelineRequest, TargetSpec
    from clean_pipeline.orchestrator import run

    # Parse spec: accept inline JSON string or path to .json file
    spec_str = args.spec
    try:
        if spec_str.endswith(".json") and not spec_str.strip().startswith("{"):
            with open(spec_str, encoding="utf-8-sig") as f:
                spec_data = _json.load(f)
        else:
            spec_data = _json.loads(spec_str)
    except Exception as exc:
        print(f"FAIL — cannot parse --spec: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        spec = TargetSpec(
            width=int(spec_data["width"]),
            height=int(spec_data["height"]),
            safe_top=int(spec_data.get("safeTop", spec_data.get("safe_top", 0))),
            safe_right=int(spec_data.get("safeRight", spec_data.get("safe_right", 0))),
            safe_bottom=int(spec_data.get("safeBottom", spec_data.get("safe_bottom", 0))),
            safe_left=int(spec_data.get("safeLeft", spec_data.get("safe_left", 0))),
        )
    except (KeyError, ValueError) as exc:
        print(f"FAIL — spec must include 'width' and 'height': {exc}", file=sys.stderr)
        sys.exit(1)

    api_key = args.api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("BACKGROUND_AI_API_KEY", "")

    job_id = str(uuid.uuid4())[:8]
    request = CleanPipelineRequest(
        job_id=job_id,
        source_path=args.input,
        target_specs=[spec],
        output_directory=args.output_dir,
    )

    print(f"job_id : {job_id}")
    print(f"source : {args.input}")
    print(f"spec   : {spec.width}×{spec.height} safe=({spec.safe_left},{spec.safe_top},{spec.safe_right},{spec.safe_bottom})")
    print(f"output : {args.output_dir}")
    print()

    result = run(request, api_key=api_key)

    if result.status.value == "PASS":
        print(f"PASS — result: {result.output_paths[0] if result.output_paths else '(none)'}")
        for sr in result.stage_results:
            print(f"  {sr.stage.value}: {sr.status.value}")
    else:
        print(f"FAIL — {result.failure_code}: {result.failure_message}", file=sys.stderr)
        for sr in result.stage_results:
            status_str = sr.status.value
            reason_str = f" [{sr.reasons[0]}]" if sr.reasons else ""
            print(f"  {sr.stage.value}: {status_str}{reason_str}", file=sys.stderr)
        sys.exit(1)


def cmd_extract(args: argparse.Namespace) -> None:
    import json as _json
    import pathlib

    from clean_pipeline.analysis.models import SceneManifest
    from clean_pipeline.extraction.object_extractor import extract
    from clean_pipeline.pipeline_logger import PipelineLogger

    job_path = pathlib.Path(args.job_dir)
    job_id = job_path.name
    output_dir = str(job_path.parent)

    canonical_path = str(job_path / "clean_v1" / "01_source" / "canonical.png")
    manifest_path = job_path / "clean_v1" / "02_analysis" / "manifest.json"

    if not manifest_path.exists():
        print(f"FAIL — manifest.json not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    manifest = SceneManifest.from_json(manifest_path.read_text(encoding="utf-8"))
    logger = PipelineLogger(job_id)

    result, extraction = extract(
        canonical_path=canonical_path,
        manifest=manifest,
        output_dir=output_dir,
        job_id=job_id,
        logger=logger,
    )

    if result.status.value == "PASS":
        print(f"\njob_id   : {job_id}")
        print(f"extracted: {len(extraction.objects)} objects")
        print(f"protected: {len(extraction.protected)} masks")
        print(f"output   : {extraction.extraction_json_path}")
    else:
        print(f"\nFAIL — {result.reasons}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m worker.clean_pipeline.cli <command>", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]

    if command == "health":
        cmd_health()

    elif command == "prepare-source":
        parser = argparse.ArgumentParser(prog="cli prepare-source")
        parser.add_argument("--input", required=True)
        parser.add_argument("--output-dir", required=True, dest="output_dir")
        cmd_prepare_source(parser.parse_args(sys.argv[2:]))

    elif command == "analyze":
        parser = argparse.ArgumentParser(prog="cli analyze")
        parser.add_argument("--job-dir", required=True, dest="job_dir",
                            help="Job output directory (e.g. ./output/c4c46b25)")
        parser.add_argument("--api-key", default=None, dest="api_key")
        cmd_analyze(parser.parse_args(sys.argv[2:]))

    elif command == "extract":
        parser = argparse.ArgumentParser(prog="cli extract")
        parser.add_argument("--job-dir", required=True, dest="job_dir",
                            help="Job output directory (e.g. ./output/c4c46b25)")
        cmd_extract(parser.parse_args(sys.argv[2:]))

    elif command == "run":
        parser = argparse.ArgumentParser(prog="cli run")
        parser.add_argument("--input", required=True,
                            help="Source image path (.png/.jpg/.psd)")
        parser.add_argument("--spec", required=True,
                            help='Target spec as JSON string or .json file path. '
                                 'Example: \'{"width":1200,"height":628,"safeTop":60}\'')
        parser.add_argument("--output-dir", required=True, dest="output_dir",
                            help="Directory where per-job output is written")
        parser.add_argument("--api-key", default=None, dest="api_key",
                            help="OpenAI API key (falls back to OPENAI_API_KEY env)")
        cmd_run(parser.parse_args(sys.argv[2:]))

    else:
        print(f"Unknown command: {command!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
