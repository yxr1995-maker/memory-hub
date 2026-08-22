#!/usr/bin/env python3
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "verify_tokens.py"


def scan(text: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        page = pathlib.Path(tmp) / "page.md"
        page.write_text(text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCANNER), tmp], text=True, capture_output=True, check=False
        )


def main() -> None:
    assert scan("Use Bearer token authentication.").returncode == 0
    assert scan("Bearer tokens,").returncode == 0
    assert scan("Bearer [REDACTED_BEARER]").returncode == 0
    assert scan("Bearer 令牌").returncode == 0
    assert scan("Bearer 凭据").returncode == 0
    assert scan("Authorization: Bearer $TOKEN").returncode == 0
    assert scan("Authorization: Bearer $TOKEN); ").returncode == 0
    assert scan(r'Authorization: Bearer $TOKEN\\"').returncode == 0
    assert scan("Authorization: Bearer $(security find-generic-password -s GitHub -w 2>/dev/null)").returncode == 0
    assert scan("Bearer ***").returncode == 0
    leaked = scan("Bearer short")
    assert leaked.returncode == 1
    assert "BAD page.md" in leaked.stdout
    assert "short" not in leaked.stdout
    substituted = scan("Bearer $(printf literal-secret)")
    assert substituted.returncode == 1
    assert "literal-secret" not in substituted.stdout
    for name, value in (
        ("here-string", "$(cat <<< literal-placeholder)"),
        ("pipe", "$(security find-generic-password -w | printf literal-placeholder)"),
        ("semicolon", "$(security find-generic-password -w; printf literal-placeholder)"),
        ("logic", "$(security find-generic-password -w && printf literal-placeholder)"),
        ("stdout-redirection", "$(security find-generic-password -w > literal-placeholder)"),
    ):
        dangerous = scan(f"Bearer {value}")
        assert dangerous.returncode == 1, name
        assert "literal-placeholder" not in dangerous.stdout, name
    assert scan("eyJ.synthetic.payload").returncode == 1
    assert scan("sk-ant-synthetic_token_0123456789").returncode == 1


if __name__ == "__main__":
    main()
