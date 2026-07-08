import traceback

PSD_ERROR_VERSION8 = "PSD_VERSION_8_UNSUPPORTED"
PSD_ERROR_OPEN_FAILED = "PSD_OPEN_FAILED"


def open_psd_safe(file_path: str):
    """PSDImage.open wrapper: 항상 (psd | None, meta dict) 반환.
    meta keys: success, engine, errorCode, error, patchedRetry"""
    from psd_tools import PSDImage
    try:
        psd = PSDImage.open(file_path)
        return psd, {
            "success": True,
            "engine": "psd-tools",
            "errorCode": None,
            "error": None,
            "patchedRetry": False,
        }
    except Exception as e:
        error = str(e)
        trace = traceback.format_exc()
        print(f"[PSD OPEN ERROR]\n{trace}")
        if "Invalid version 8" in error:
            return None, {
                "success": False,
                "engine": "psd-tools",
                "errorCode": PSD_ERROR_VERSION8,
                "error": error,
                "patchedRetry": False,
            }
        return None, {
            "success": False,
            "engine": "psd-tools",
            "errorCode": PSD_ERROR_OPEN_FAILED,
            "error": error,
            "patchedRetry": False,
        }


def apply_version8_compat_patch() -> bool:
    """psd-tools 내부 linked_layer.py version 8 체크를 우회하는 패치.
    traceback에서 확인된 위치에 적용. 패치 성공 시 True 반환."""
    try:
        import pathlib
        import psd_tools as _pt
        base = pathlib.Path(_pt.__file__).parent

        # 후보 경로: 버전마다 위치가 다를 수 있음
        candidates = [
            base / "psd" / "linked_layer.py",
            base / "composite" / "blend.py",
        ]

        patched_any = False
        for target in candidates:
            if not target.exists():
                continue
            text = target.read_text(encoding="utf-8")
            old = "        if version not in (1, 2, 3, 7):"
            new = (
                "        if version == 8:\n"
                "            version = 7\n"
                "        if version not in (1, 2, 3, 7):"
            )
            if old in text and "version == 8" not in text:
                target.write_text(text.replace(old, new, 1), encoding="utf-8")
                print(f"[PSD PATCH] linked_layer version 8 patch applied → {target}")
                # 패치된 모듈 재로드
                try:
                    import importlib
                    import psd_tools.psd.linked_layer
                    importlib.reload(psd_tools.psd.linked_layer)
                    print("[PSD PATCH] psd_tools.psd.linked_layer reloaded")
                except Exception as reload_err:
                    print(f"[PSD PATCH] reload warning (non-fatal): {reload_err}")
                patched_any = True
            else:
                print(f"[PSD PATCH] skipped {target.name} (already patched or pattern not found)")

        if not patched_any:
            # 디버그: 실제 파일 위치를 로그로 남겨 traceback에서 찾도록 안내
            psd_files = list(base.rglob("*.py"))
            print(f"[PSD PATCH] No patch target found. psd-tools path: {base}")
            print(f"[PSD PATCH] Available .py files: {[str(f.relative_to(base)) for f in psd_files[:20]]}")
        return patched_any

    except Exception as e:
        print(f"[PSD PATCH] patch failed: {e}\n{traceback.format_exc()}")
        return False


def open_psd_safe_with_patch(file_path: str):
    """open_psd_safe + version 8 감지 시 compatibility patch 후 retry.
    항상 (psd | None, meta dict) 반환."""
    psd, meta = open_psd_safe(file_path)
    if meta["success"]:
        return psd, meta

    if meta["errorCode"] == PSD_ERROR_VERSION8:
        patched = apply_version8_compat_patch()
        if patched:
            psd, retry_meta = open_psd_safe(file_path)
            retry_meta["patchedRetry"] = True
            return psd, retry_meta

    return psd, meta
