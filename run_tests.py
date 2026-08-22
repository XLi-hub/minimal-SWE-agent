import os
import sys
import pytest


if __name__ == "__main__":
    # 清理 ROS 的 PYTHONPATH，避免 pytest 插件冲突
    ros_paths = [p for p in sys.path if "ros" in p.lower()]
    for p in ros_paths:
        sys.path.remove(p)
    os.environ.pop("PYTHONPATH", None)
    # 默认只跑不产生费用的测试；E2E 需要显式单独运行。
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    sys.exit(pytest.main(["-v", "tests/", "-m", "not e2e"]))
