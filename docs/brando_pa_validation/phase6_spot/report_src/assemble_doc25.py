import subprocess
from datetime import datetime, timezone
from pathlib import Path

SCR = Path(__file__).parent
ROOT = Path("/home/user/1hao-")
head = (SCR / "doc25_head.md").read_text()
results = subprocess.run(["python3", "-I", str(SCR / "render_phase6.py")], capture_output=True, text=True, check=True).stdout
n_tests = str(sum(open(p).read().count("def test_") for p in ROOT.glob("tests/test_brando_pa_*.py")))
conclusions = (SCR / "doc25_conclusions.md").read_text()
tail = (SCR / "doc25_tail.md").read_text().replace("{N_TESTS}", n_tests)
doc = head.replace("{GEN_TS}", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")).replace("{N_TESTS}", n_tests).replace("{CONCLUSIONS}", conclusions)
doc += "\n# 结果\n\n" + results + "\n" + tail
(ROOT / "docs/25_brando_pa_第六阶段_现货趋势基准审计与熔断修复.md").write_text(doc, encoding="utf-8")
print("written", len(doc), "chars; tests", n_tests)
