# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Offline test for scripts/launch.py, the .cmd/.bat launcher used by implement.py.

Run: uv run --script plugins/task-flow/tests/test_launch.py
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import launch

CMD_EXE = r"C:\W\System32\cmd.exe"


class LaunchTest(unittest.TestCase):
    def test_command_line_format(self):
        line = launch.cmd_command_line(r"C:\t\npm.cmd", " run build", CMD_EXE)
        self.assertEqual(
            line, r'"C:\W\System32\cmd.exe" /d /s /c ""C:\t\npm.cmd" run build"'
        )
        spaced = launch.cmd_command_line(r"C:\Program Files\t\tool.cmd", " x", CMD_EXE)
        self.assertEqual(
            spaced,
            r'"C:\W\System32\cmd.exe" /d /s /c ""C:\Program Files\t\tool.cmd" x"',
        )

    def test_each_metachar_rejected(self):
        for char in "&|<>^%!\n\r":
            shown = "newline" if char in "\r\n" else char
            cases = [
                (rf"C:\t\tool.cmd{char}", " arg"),
                (r"C:\t\tool.cmd", f" arg{char}"),
                (r"C:\t\tool.cmd", f' "a{char}b"'),
            ]
            for program, rest in cases:
                with self.subTest(char=char, program=program, rest=rest):
                    with self.assertRaises(ValueError) as ctx:
                        launch.cmd_command_line(program, rest, CMD_EXE)
                    self.assertIn(shown, str(ctx.exception))

    def test_plain_arguments_allowed(self):
        rest = ' "two words" = - / : ( ) , . @ --flag=x'
        line = launch.cmd_command_line(r"C:\t\tool.cmd", rest, CMD_EXE)
        self.assertEqual(line, f'"{CMD_EXE}" /d /s /c ""C:\\t\\tool.cmd"{rest}"')

    def test_is_cmd_script(self):
        for name in ("a.cmd", "a.bat", "NPM.CMD", "x.Bat", r"C:\t\A.CMD"):
            self.assertTrue(launch.is_cmd_script(name), name)
        for name in ("a.exe", "a.com", "a.ps1", "a", "x.cmd.exe", "cmd"):
            self.assertFalse(launch.is_cmd_script(name), name)

    def test_cmd_exe_from_systemroot(self):
        self.assertEqual(
            launch.cmd_exe({"SYSTEMROOT": r"C:\Win"}), r"C:\Win\System32\cmd.exe"
        )
        self.assertEqual(
            launch.cmd_exe({"SystemRoot": r"D:\Win"}), r"D:\Win\System32\cmd.exe"
        )
        self.assertEqual(launch.cmd_exe({}), r"C:\Windows\System32\cmd.exe")

    @unittest.skipUnless(os.name == "nt", "cmd.exe exists only on Windows")
    def test_runs_cmd_script_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("plain", "shim dir"):
                folder = Path(tmp) / name
                folder.mkdir()
                script = folder / "hello.cmd"
                script.write_text(
                    "@echo off\r\necho arg1=[%~1]\r\necho arg2=[%~2]\r\nexit /b 3\r\n",
                    encoding="utf-8",
                    newline="",
                )
                line = launch.cmd_command_line(
                    str(script), ' "two words" x', launch.cmd_exe(os.environ)
                )
                done = subprocess.run(line, capture_output=True, text=True, check=False)
                self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
                self.assertIn("arg1=[two words]", done.stdout)
                self.assertIn("arg2=[x]", done.stdout)


if __name__ == "__main__":
    unittest.main()
