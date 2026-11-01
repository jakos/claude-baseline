# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Start Windows .cmd/.bat programs through cmd.exe without shell features.

`implement.py`'s `run_command` starts ONE program with arguments. On Windows a program
that resolves to a `.cmd`/`.bat` file cannot be started directly by CreateProcess, so it
is run as `cmd.exe /d /c /s` with one quoted command line: `"<cmd.exe>" /d /s /c
""<program>"<rest>"`, where `<rest>` is the caller's text after the first token,
unchanged (`/d` skips AutoRun registry commands; with `/s`, cmd strips exactly the
first and the last quote of the text after `/c` and runs the rest verbatim).

cmd.exe interprets `& | < > ^ %` always, `!` when delayed expansion is enabled (a
registry setting can turn it on), and a CR or LF ends the command. Those characters are
therefore rejected anywhere in the command line, inside or outside quotes, so no quote
parsing difference between cmd and the C runtime (the "BatBadBut" class) can reach an
interpreted character: a quote can then only change how arguments are split, not run
anything. There are no pipes, redirects or `&&` for any tool.

This module never adds arguments and never expands anything. Stdlib only, started by
`uv run --script` or imported as a sibling module by implement.py, so it runs on macOS,
Linux and Windows; `cmd_command_line` is a pure function and testable on every platform.
"""

CMD_METACHARS = "&|<>^%!\r\n"


def is_cmd_script(path: str) -> bool:
    """True when the suffix is `.cmd` or `.bat`, case-insensitive."""
    return path.lower().endswith((".cmd", ".bat"))


def cmd_exe(env: dict) -> str:
    """The cmd.exe to run, from SYSTEMROOT in `env`, falling back to C:\\Windows."""
    root = env.get("SYSTEMROOT") or env.get("SystemRoot") or r"C:\Windows"
    return root.rstrip("\\/") + r"\System32\cmd.exe"


def cmd_command_line(program: str, rest: str, interpreter: str) -> str:
    """Build `"<interpreter>" /d /s /c ""<program>"<rest>"`, refusing metacharacters.

    Raises ValueError when `program + rest` contains any CMD_METACHARS character.
    """
    for char in CMD_METACHARS:
        if char in program + rest:
            shown = "newline" if char in "\r\n" else char
            raise ValueError(
                f"the command contains {shown!r}, which cmd.exe would interpret: "
                ".cmd/.bat programs run through cmd.exe, and there are no pipes, "
                "redirects or && for any tool"
            )
    return f'"{interpreter}" /d /s /c ""{program}"{rest}"'
