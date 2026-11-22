import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from omp_kali.container import Runner


class RunnerStreamTests(unittest.TestCase):
    def test_stream_tee_to_console_and_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "provision.log"
            with log_path.open("a", encoding="utf-8") as log_file:
                runner = Runner()
                runner.log_file = log_file
                console = io.StringIO()
                with redirect_stdout(console):
                    result = runner.run(
                        ["sh", "-c", "printf 'hello\\n'; printf 'stderr-line\\n' >&2"],
                        stream=True,
                    )
            self.assertEqual(result.returncode, 0)
            self.assertIn("hello", console.getvalue())
            self.assertIn("stderr-line", console.getvalue())
            logged = log_path.read_text(encoding="utf-8")
            self.assertIn("$ sh -c", logged)
            self.assertIn("hello", logged)
            self.assertIn("stderr-line", logged)

    def test_stream_failure_reports_exit_code(self) -> None:
        runner = Runner()
        console = io.StringIO()
        with redirect_stdout(console):
            result = runner.run(["sh", "-c", "printf 'boom\\n'; exit 7"], stream=True, check=False)
        self.assertEqual(result.returncode, 7)
        self.assertIn("boom", console.getvalue())


if __name__ == "__main__":
    unittest.main()
