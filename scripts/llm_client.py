import os

class LLMClient:
    """간단한 LLM 클라이언트 래퍼.
    - ANTHROPIC_API_KEY 환경변수가 설정되어 있으면 `anthropic` 라이브러리를 사용합니다.
    사용법:
      set ANTHROPIC_API_KEY=...
      python scripts/orchestrator.py agent --name kyc --client client_20260531_001
    """

    def __init__(self):
        try:
            import anthropic
            self._anthropic = anthropic
        except ImportError as e:
            raise RuntimeError("anthropic 패키지가 필요합니다. `pip install anthropic`으로 설치하세요. 오류: %s" % e)
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY 환경변수를 설정하세요.")
        self._client = self._anthropic.Anthropic(api_key=key)
        self.model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    def generate(self, prompt: str) -> str:
        """프롬프트를 받아 LLM의 출력(문자열)을 반환합니다."""
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.content:
            return resp.content[0].text
        return ""
