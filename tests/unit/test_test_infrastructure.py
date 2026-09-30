"""Guards for H10: the suite must stay pytest-based, isolated and honestly reported."""

import configparser
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"


def _test_modules():
    return sorted(p for p in TESTS.rglob("test_*.py") if "__pycache__" not in p.parts)


class TestPytestSetup(unittest.TestCase):

    def test_pytest_is_a_declared_dev_dependency(self):
        text = (ROOT / "requirements-dev.txt").read_text()
        self.assertRegex(text, r"(?m)^pytest")
        self.assertIn("-r requirements.txt", text)

    def test_ini_registers_markers_and_excludes_external_tests_by_default(self):
        cfg = configparser.ConfigParser()
        cfg.read(ROOT / "pytest.ini")
        section = cfg["pytest"]
        for marker in ("docker", "gpu", "integration"):
            self.assertRegex(section["markers"], rf"(?m)^\s*{marker}:")
            self.assertIn(marker, section["addopts"])
        self.assertIn("not docker", section["addopts"])

    def test_ci_installs_dev_requirements_and_runs_the_runner(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("requirements-dev.txt", ci)
        self.assertIn("run_all_tests.py", ci)

    def test_shell_runner_delegates_to_pytest_runner(self):
        sh = (ROOT / "scripts" / "run_tests.sh").read_text()
        self.assertIn("run_all_tests.py", sh)
        self.assertNotIn("unittest", sh)

    def test_runner_uses_pytest_not_unittest(self):
        runner = (TESTS / "run_all_tests.py").read_text()
        self.assertIn('"pytest"', runner)
        self.assertNotIn('"unittest"', runner)


class TestNoSilentlyEmptyModules(unittest.TestCase):
    """A pytest-style module under a unittest-only runner reports success with 0 tests."""

    def test_every_module_defines_collectable_tests(self):
        for path in _test_modules():
            src = path.read_text(encoding="utf-8")
            has_tests = re.search(r"(?m)^\s*(async\s+)?def test_", src)
            self.assertTrue(has_tests, f"{path.relative_to(ROOT)} defines no tests")

    def test_modules_needing_external_resources_are_marked(self):
        docker_like = ("test_piper_e2e_real.py", "test_piper_integration.py")
        for name in docker_like:
            src = (TESTS / "integration" / name).read_text()
            self.assertIn("pytest.mark.docker", src, name)
            self.assertIn("pytest.mark.integration", src, name)
        gpu = (TESTS / "integration" / "test_xtts_synthesis.py").read_text()
        self.assertIn("pytest.mark.gpu", gpu)
        self.assertNotIn("requires_gpu", gpu)


if __name__ == "__main__":
    unittest.main()
