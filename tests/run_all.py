# -*- coding: utf-8 -*-
"""의존성 없는 테스트 러너.

pytest 미설치 환경에서도 `python tests/run_all.py` 로 전체 단위 테스트를 실행한다.
(pytest 설치 시에는 `pytest tests/` 로도 동일하게 동작한다.)
"""
import importlib
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

TEST_MODULES = ["test_risk_calculator", "test_correlation", "test_schemas"]


def main() -> int:
    total = passed = failed = skipped = 0
    failures = []

    for modname in TEST_MODULES:
        try:
            mod = importlib.import_module(modname)
        except Exception as e:
            print(f"[MODULE IMPORT FAIL] {modname}: {e}")
            failed += 1
            failures.append((modname, "<import>", str(e)))
            continue

        skip_reason = getattr(mod, "SKIP_REASON", None)
        if skip_reason:
            print(f"[SKIP MODULE] {modname}: {skip_reason}")

        tests = sorted(n for n in dir(mod) if n.startswith("test_"))
        for tname in tests:
            fn = getattr(mod, tname)
            if not callable(fn):
                continue
            total += 1
            try:
                fn()
                # SKIP_REASON 이 설정된 모듈의 테스트는 내부에서 no-op return → skip 집계
                if skip_reason:
                    skipped += 1
                    print(f"  [skip] {modname}.{tname}")
                else:
                    passed += 1
                    print(f"  [ok]   {modname}.{tname}")
            except Exception:
                failed += 1
                failures.append((modname, tname, traceback.format_exc().splitlines()[-1]))
                print(f"  [FAIL] {modname}.{tname}")

    print("\n" + "=" * 50)
    print(f"총 {total} | 통과 {passed} | 실패 {failed} | 스킵 {skipped}")
    if failures:
        print("\n실패 상세:")
        for m, t, e in failures:
            print(f"  - {m}.{t}: {e}")
    print("=" * 50)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
