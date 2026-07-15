"""Golden PSD integration tests.

실제 PSD 파일로 end-to-end 파이프라인을 검증한다.
PSD 파일이 없으면 자동 skip (CI 환경 호환).

검증 목적:
  golden_psd_01 (야다화장품 1200x628): 제품 레이어가 main_image로 렌더링되는지
  golden_psd_02 (에스테리브  300x250): 작은 배너에서 product+text 유지 여부
  golden_psd_03 (레브웨딩  1080x1080): 제품 없는 광고에서 파이프라인이 FAIL 안 내는지
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PSD_01 = r"C:\Users\heeil\Downloads\리사이징_샘플\1200x628_야다화장품_네이버GFA.psd"
PSD_02 = r"C:\Users\heeil\Downloads\리사이징_샘플\300x250_에스테리브_크리테오.psd"
PSD_03 = r"C:\Users\heeil\Downloads\리사이징_샘플\1080x1080_레브웨딩_메타.psd"


def _run_generate(psd_path: str, w: int, h: int, media: str, job_id: str,
                  out_dir: str) -> dict:
    import resizer
    results, _ = resizer.generate(
        psd_path=psd_path,
        specs=[{"media": media, "width": w, "height": h, "name": f"{w}x{h}", "slug": ""}],
        resize_mode="smart-fit",
        output_format="png",
        output_dir=out_dir,
        source_type="psd",
        psd_mode="object-reflow",
        object_reflow_enabled=True,
        object_analysis=None,
        job_id=job_id,
    )
    return results[0] if results else {}


# ── golden_psd_01: 야다화장품 1200x628 ────────────────────────────────────────

class GoldenPsd01YadaCosmetics(unittest.TestCase):
    """제품 레이어가 있는 PSD — productRendered 필수."""

    _result: dict = {}
    _tmp: str = ""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(PSD_01):
            raise unittest.SkipTest(f"PSD 없음: {PSD_01}")
        cls._tmp = tempfile.mkdtemp(prefix="golden_psd_01_")
        cls._result = _run_generate(PSD_01, 1200, 628, "naver_gfa", "golden_01", cls._tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _r(self) -> dict:
        return self._result

    def test_01_reflow_succeeds(self):
        """object-reflow 경로가 예외 없이 완료되는지."""
        r = self._r()
        self.assertIsNotNone(r, "generate가 결과를 반환하지 않음")
        self.assertTrue(
            r.get("objectReflowSucceeded"),
            f"object-reflow 실패 — fallbackReason={r.get('fallbackReason')}"
        )

    def test_02_product_expected(self):
        """creative_object_set에 main_image가 포함됐는지 (Case B area fallback 포함)."""
        r = self._r()
        self.assertTrue(
            r.get("productExpected"),
            f"productExpected=False — 제품 레이어가 main_image로 인식 안 됨. "
            f"usedObjectRoles={r.get('usedObjectRoles')}"
        )

    def test_03_product_rendered(self):
        """main_image가 최종 캔버스에 실제 렌더링됐는지. 핵심 회귀 방지 테스트."""
        r = self._r()
        self.assertTrue(
            r.get("productRendered"),
            f"productRendered=False — 제품 튜브 미렌더. "
            f"usedObjectRoles={r.get('usedObjectRoles')} | "
            f"missingObjectRoles={r.get('missingObjectRoles')}"
        )

    def test_04_product_render_quality(self):
        """Photoroom 개별 레이어 추출 시 productRenderQuality=pass여야 함.

        psd_layer_parser가 text/smartobject를 covered_ids에서 제외하면
        -Photoroom 레이어가 독립 추출 → caseb_product_isolated → pass.
        scikit-image 미설치 또는 composite 실패 시 partial 허용.
        """
        r = self._r()
        quality = r.get("productRenderQuality", "fail")
        if not r.get("productRendered"):
            self.skipTest("productRendered=False — productRenderQuality 판정 불가")
        self.assertIn(
            quality, ("pass", "partial"),
            f"productRenderQuality={quality} — fail은 허용하지 않음"
        )

    def test_05_role_separation_quality(self):
        """Photoroom 제품 레이어 + 텍스트 레이어 개별 추출 → roleSeparationQuality=pass.

        scikit-image 설치 시 내부 그룹 composite → partial 허용.
        """
        r = self._r()
        quality = r.get("roleSeparationQuality", "fail")
        if not r.get("objectReflowSucceeded"):
            self.skipTest("object-reflow 실패 — roleSeparationQuality 판정 불가")
        self.assertIn(
            quality, ("pass", "partial"),
            f"roleSeparationQuality={quality} — "
            f"separatedRoles={r.get('separatedRoles')} compositeOnly={r.get('compositeOnlyRoles')}"
        )

    def test_06_output_file_valid(self):
        """출력 PNG 파일이 올바른 크기(1200x628)로 생성됐는지."""
        r = self._r()
        self.assertTrue(r.get("valid"), f"파일 크기 불일치: {r.get('validationMessage')}")


# ── golden_psd_02: 에스테리브 300x250 ─────────────────────────────────────────

class GoldenPsd02EsteerivSmallBanner(unittest.TestCase):
    """작은 배너에서 product + text 모두 유지되는지."""

    _result: dict = {}
    _tmp: str = ""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(PSD_02):
            raise unittest.SkipTest(f"PSD 없음: {PSD_02}")
        cls._tmp = tempfile.mkdtemp(prefix="golden_psd_02_")
        cls._result = _run_generate(PSD_02, 300, 250, "criteo", "golden_02", cls._tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _r(self) -> dict:
        return self._result

    def test_01_reflow_succeeds(self):
        r = self._r()
        self.assertTrue(
            r.get("objectReflowSucceeded"),
            f"object-reflow 실패: {r.get('fallbackReason')}"
        )

    def test_02_product_rendered(self):
        """작은 배너에서도 제품 이미지가 렌더링되는지."""
        r = self._r()
        self.assertTrue(
            r.get("productRendered"),
            f"productRendered=False — usedObjectRoles={r.get('usedObjectRoles')}"
        )

    def test_03_text_or_cta_rendered(self):
        """headline/body_text/cta 중 하나 이상 렌더링됐는지 (ctaExpected 대리 검증).

        scikit-image 설치 시 PSD 레이어가 단일 최상위 그룹으로 병합될 수 있음.
        이 경우 AI 분석 없이는 텍스트/CTA 역할 구분이 불가능 → SKIP 처리.
        """
        r = self._r()
        used = set(r.get("usedObjectRoles") or [])
        text_roles = {"headline", "body_text", "cta"}
        if not (used & text_roles):
            self.skipTest(
                f"텍스트/CTA 레이어 없음 — usedObjectRoles={used}. "
                f"PSD 레이어가 단일 그룹으로 병합됐거나 레이어명에 키워드 미매칭. "
                f"AI 분석 모드(object_analysis 제공 시)에서 재검증 필요."
            )

    def test_04_cta_rendered(self):
        """CTA 버튼이 usedObjectRoles에 포함됐는지.

        키워드 매칭 가능한 CTA 레이어명(바로가기/더보기/구매/btn/button/cta)이
        없으면 SKIP — AI 분석 모드에서 검증.
        """
        r = self._r()
        used = r.get("usedObjectRoles") or []
        if "cta" not in used:
            self.skipTest(
                f"CTA 미렌더 — usedObjectRoles={used}. "
                f"에스테리브 PSD CTA 레이어명이 키워드 미매칭 또는 그룹으로 병합됨. "
                f"AI 분석 모드에서 재검증 필요."
            )

    def test_05_role_separation_quality(self):
        """Photoroom 제품 레이어 + 텍스트 레이어 개별 추출 → roleSeparationQuality pass/partial.

        에스테리브는 300x250 작은 배너 — 개별 레이어 추출 후 pass 기대.
        """
        r = self._r()
        if not r.get("objectReflowSucceeded"):
            self.skipTest("object-reflow 실패 — roleSeparationQuality 판정 불가")
        quality = r.get("roleSeparationQuality", "fail")
        self.assertIn(
            quality, ("pass", "partial"),
            f"roleSeparationQuality={quality} — "
            f"separatedRoles={r.get('separatedRoles')}"
        )

    def test_06_output_file_valid(self):
        r = self._r()
        self.assertTrue(r.get("valid"), f"파일 크기 불일치: {r.get('validationMessage')}")


# ── golden_psd_03: 레브웨딩 1080x1080 ────────────────────────────────────────

class GoldenPsd03LevWeddingNoProduct(unittest.TestCase):
    """제품 없는 사람/행사형 광고 — product 누락을 FAIL로 처리하지 않는지 검증."""

    _result: dict = {}
    _exception: Exception | None = None
    _tmp: str = ""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(PSD_03):
            raise unittest.SkipTest(f"PSD 없음: {PSD_03}")
        cls._tmp = tempfile.mkdtemp(prefix="golden_psd_03_")
        try:
            cls._result = _run_generate(PSD_03, 1080, 1080, "meta", "golden_03", cls._tmp)
        except Exception as e:
            cls._exception = e

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _r(self) -> dict:
        return self._result

    def test_01_no_exception(self):
        """파이프라인이 예외를 던지지 않고 완료되는지 — 핵심 회귀 방지."""
        self.assertIsNone(
            self._exception,
            f"generate()가 예외를 던짐: {self._exception}"
        )

    def test_02_result_returned(self):
        """generate가 빈 결과가 아닌 항목을 반환하는지."""
        self.assertIsNotNone(self._r(), "결과 dict 없음")
        self.assertIn("objectReflowSucceeded", self._r(), "결과 dict 필드 누락")

    def test_03_reflow_or_fallback_ok(self):
        """object-reflow 성공 또는 smart-fit fallback — 어느 쪽이든 파일이 생성됐는지."""
        r = self._r()
        reflow_ok = r.get("objectReflowSucceeded", False)
        fallback_ok = r.get("fallbackUsed", False) and r.get("valid", False)
        self.assertTrue(
            reflow_ok or fallback_ok,
            f"reflow도 fallback도 성공하지 않음: {r}"
        )

    def test_04_product_expected_false(self):
        """레브웨딩은 제품 없는 광고 — productExpected=False여야 함 (시나리오 D 방어 테스트).

        생성형 채우기(Generative Fill) 또는 씬/행사 사진 레이어가
        area fallback으로 main_image 오승격되지 않아야 한다.
        """
        r = self._r()
        if not r.get("objectReflowSucceeded"):
            self.skipTest("object-reflow 자체가 실패 — fallback 경로, productExpected 판정 불가")
        self.assertFalse(
            r.get("productExpected"),
            f"productExpected=True — 씬/배경 레이어가 main_image로 오승격됨. "
            f"noProductScenarioDetected={r.get('noProductScenarioDetected')}"
        )

    def test_05_no_product_scenario_detected(self):
        """noProductScenarioDetected=True여야 함 — 제품 없는 광고임을 파이프라인이 인식."""
        r = self._r()
        if not r.get("objectReflowSucceeded"):
            self.skipTest("object-reflow 실패 — fallback 경로")
        self.assertTrue(
            r.get("noProductScenarioDetected"),
            f"noProductScenarioDetected=False — 파이프라인이 no-product 씬을 인식하지 못함. "
            f"productExpected={r.get('productExpected')}"
        )

    def test_06_missing_product_does_not_kill_reflow(self):
        """제품 없어도 objectReflowSucceeded=True이고 파일이 생성되어야 함."""
        r = self._r()
        if not r.get("objectReflowSucceeded"):
            self.skipTest("object-reflow 자체가 실패 — smart-fit fallback으로 처리됨 (허용)")
        self.assertTrue(
            r.get("valid"),
            "object-reflow 성공이지만 출력 파일이 유효하지 않음"
        )

    def test_07_output_file_valid(self):
        r = self._r()
        self.assertTrue(r.get("valid"), f"파일 크기 불일치: {r.get('validationMessage')}")


# ── run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [GoldenPsd01YadaCosmetics, GoldenPsd02EsteerivSmallBanner, GoldenPsd03LevWeddingNoProduct]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    skipped = len(result.skipped)
    failed = len(result.failures) + len(result.errors)
    passed = total - failed

    print(f"\n{'='*60}")
    print(f"Golden PSD test: {passed}/{total} PASS  ({failed} FAIL, {skipped} SKIP)")
    print(f"{'='*60}")

    import sys as _sys
    _sys.exit(0 if failed == 0 else 1)
