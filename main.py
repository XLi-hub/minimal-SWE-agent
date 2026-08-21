"""兼容入口：``python main.py`` 等价于 ``minimal`` / ``python -m mini_agent``.

CLI 逻辑已移到 :mod:`mini_agent.cli`，这里只做转发，保留脚本形态的用法。
"""

from mini_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
