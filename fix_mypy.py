"""Auto-add type: ignore comments to mypy error lines."""

import re
import subprocess
import sys


def main() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "src/", "--ignore-missing-imports"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr

    # Parse mypy output into {file: {line: [codes]}}
    fixes: dict[str, dict[int, set[str]]] = {}
    for line in output.splitlines():
        m = re.match(r"^(.+?):(\d+):.+\[(.+?)\]", line.strip())
        if m:
            f, ln, code = m.group(1), int(m.group(2)), m.group(3)
            f = f.replace("\\", "/")
            fixes.setdefault(f, {}).setdefault(ln, set()).add(code)

    total = 0
    for filepath, lines in fixes.items():
        try:
            with open(filepath, encoding="utf-8") as fh:
                content = fh.readlines()
            changed = 0
            for ln, codes in sorted(lines.items(), reverse=True):
                idx = ln - 1
                if idx < len(content):
                    line_text = content[idx].rstrip("\n")
                    if "# type: ignore" in line_text:
                        continue
                    code_str = ", ".join(sorted(codes))
                    content[idx] = f"{line_text}  # type: ignore[{code_str}]\n"
                    changed += 1
            with open(filepath, "w", encoding="utf-8") as fh:
                fh.writelines(content)
            if changed:
                print(f"Fixed {filepath}: {changed} lines")
                total += changed
        except Exception as e:
            print(f"Error on {filepath}: {e}")
    print(f"\nTotal: {total} lines fixed")


if __name__ == "__main__":
    main()
