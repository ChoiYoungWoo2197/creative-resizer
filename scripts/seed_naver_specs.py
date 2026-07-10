"""
banner-specs/naver.json → MongoDB banner_spec 컬렉션 upsert.

사용법:
  MONGODB_URI=mongodb+srv://... python scripts/seed_naver_specs.py

또는 서버에서:
  cd /opt/creative-resizer
  MONGODB_URI=$(grep MONGODB_URI .env | cut -d= -f2-) python scripts/seed_naver_specs.py

결과 검증:
  python scripts/seed_naver_specs.py --verify
"""

import json
import os
import sys
from pathlib import Path


def main():
    verify_only = "--verify" in sys.argv

    uri = os.environ.get("MONGODB_URI")
    if not uri:
        print("ERROR: MONGODB_URI 환경변수가 없습니다.")
        print("  export MONGODB_URI=mongodb+srv://...")
        sys.exit(1)

    try:
        from pymongo import MongoClient, UpdateOne
    except ImportError:
        print("ERROR: pymongo 미설치. pip install pymongo")
        sys.exit(1)

    seed_file = Path(__file__).parent.parent / "src/main/resources/banner-specs/naver.json"
    if not seed_file.exists():
        print(f"ERROR: seed 파일 없음: {seed_file}")
        sys.exit(1)

    with open(seed_file, encoding="utf-8") as f:
        data = json.load(f)

    client = MongoClient(uri)
    db_name = uri.split("/")[-1].split("?")[0] or "creative_resizer"
    db = client[db_name]
    col = db["banner_spec"]

    if verify_only:
        _verify(col)
        return

    ops = []
    for raw in data:
        spec = _map_to_spec(raw)
        ops.append(UpdateOne(
            {"slug": spec["slug"]},
            {"$set": spec},
            upsert=True,
        ))

    if ops:
        result = col.bulk_write(ops)
        print(f"upserted: {result.upserted_count}, modified: {result.modified_count}")

    _verify(col)
    client.close()


def _verify(col):
    total = col.count_documents({"media": "naver"})
    parsed = col.count_documents({"media": "naver", "safeZoneParseStatus": "parsed_text"})
    needs_review = col.count_documents({"media": "naver", "needsReview": True})

    print("\n=== 검증 결과 ===")
    print(f"media=naver total:           {total} (expected 68)")
    print(f"safeZoneParseStatus=parsed_text: {parsed} (expected 3)")
    print(f"needsReview=true:            {needs_review} (expected 1)")

    target = col.find_one({"slug": "naver-gfa-mobile-da-image-banner-1250x560"})
    if target:
        sz = target.get("safeZone") or {}
        print(f"\nnaver-gfa-mobile-da-image-banner-1250x560 safeZone:")
        print(f"  top={sz.get('top')} right={sz.get('right')} bottom={sz.get('bottom')} left={sz.get('left')}")
        expected = {"top": 50, "right": 240, "bottom": 35, "left": 240}
        ok = all(sz.get(k) == v for k, v in expected.items())
        print(f"  PASS" if ok else f"  FAIL (expected {expected})")
    else:
        print("  ERROR: slug not found")

    if total == 68 and parsed == 3 and needs_review == 1:
        print("\n>>> SEED VERIFICATION: PASS")
    else:
        print("\n>>> SEED VERIFICATION: FAIL")


def _map_to_spec(raw: dict) -> dict:
    spec = {
        "media": raw.get("media"),
        "placementName": raw.get("placementName"),
        "slug": raw.get("slug"),
        "width": raw.get("width", 0),
        "height": raw.get("height", 0),
        "aspectRatio": raw.get("ratio"),
        "active": True,
        "sortOrder": raw.get("id", 0) + 10000,
        # 8단계 확장 필드
        "category": raw.get("category"),
        "placementType": raw.get("placementType"),
        "sourceUrl": raw.get("sourceUrl"),
        "sourceType": raw.get("sourceType"),
        "sourceRef": raw.get("sourceRef"),
        "ratio": raw.get("ratio"),
        "ratioLabel": raw.get("ratioLabel"),
        "fileFormats": raw.get("fileFormats") or [],
        "maxFileSizeKb": raw.get("maxFileSizeKb"),
        "minFileSizeKb": raw.get("minFileSizeKb"),
        "colorSpace": raw.get("colorSpace"),
        "safeTop": raw.get("safeTop"),
        "safeRight": raw.get("safeRight"),
        "safeBottom": raw.get("safeBottom"),
        "safeLeft": raw.get("safeLeft"),
        "safeZoneWidth": raw.get("safeZoneWidth"),
        "safeZoneHeight": raw.get("safeZoneHeight"),
        "safeZoneParseStatus": raw.get("safeZoneParseStatus"),
        "headlineMaxChars": raw.get("headlineMaxChars"),
        "descriptionMaxChars": raw.get("descriptionMaxChars"),
        "textMaxPct": raw.get("textMaxPct"),
        "bgTransparent": raw.get("bgTransparent"),
        "isVideo": raw.get("isVideo"),
        "notes": raw.get("notes"),
        "needsReview": raw.get("needsReview"),
        "lastVerified": raw.get("lastVerified"),
        "lastUpdated": raw.get("lastUpdated"),
    }
    # parsed_text → safeZone Map 자동 구성
    if raw.get("safeZoneParseStatus") == "parsed_text" and raw.get("safeTop") is not None:
        spec["safeZone"] = {
            "top": raw["safeTop"], "right": raw["safeRight"],
            "bottom": raw["safeBottom"], "left": raw["safeLeft"],
        }
    return spec


if __name__ == "__main__":
    main()
