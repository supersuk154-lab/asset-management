"""
utils.py + send_report.py 단위 테스트
"""
import sys, os, json, tempfile, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
from utils import safe_write_json


class TestSafeWriteJson(unittest.TestCase):

    def test_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "out.json"
            safe_write_json(path, {"key": "value"})
            self.assertTrue(path.exists())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["key"], "value")

    def test_no_tmp_leftover_on_success(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "out.json"
            safe_write_json(path, {"x": 1})
            tmp = path.with_suffix(".json.tmp")
            self.assertFalse(tmp.exists(), ".tmp 파일이 남아있음")

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sub" / "deep" / "out.json"
            safe_write_json(path, {"nested": True})
            self.assertTrue(path.exists())

    def test_atomic_replace_preserves_old_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "out.json"
            safe_write_json(path, {"version": 1})
            # 비직렬화 객체로 실패 유발
            with self.assertRaises(Exception):
                safe_write_json(path, {"bad": object()})
            # 원본 파일 유지 확인
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)

    def test_no_tmp_leftover_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "out.json"
            try:
                safe_write_json(path, {"bad": object()})
            except Exception:
                pass
            tmp = path.with_suffix(".json.tmp")
            self.assertFalse(tmp.exists(), "실패 후 .tmp 파일이 남아있음")


class TestEmailRegex(unittest.TestCase):
    """send_report.py의 이메일 형식 검증 정규식 테스트"""

    _EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    def test_valid_emails(self):
        valids = [
            "user@example.com",
            "test.user+tag@domain.co.kr",
            "a@b.io",
        ]
        for email in valids:
            self.assertTrue(self._EMAIL_RE.match(email), f"유효한 이메일 거부됨: {email}")

    def test_invalid_emails(self):
        invalids = [
            "notanemail",
            "missing@tld",
            "@nodomain.com",
            "spaces in@email.com",
            "",
        ]
        for email in invalids:
            self.assertFalse(self._EMAIL_RE.match(email), f"잘못된 이메일 통과됨: {email}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
