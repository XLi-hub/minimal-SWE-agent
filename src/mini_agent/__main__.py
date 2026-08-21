"""让 ``python -m mini_agent`` 等价于 ``minimal`` 命令。"""

from mini_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
