import os
import sys
import pytest


if __name__ == "__main__":
    # 清理 ROS 的 PYTHONPATH，避免 pytest 插件冲突
    ros_paths = [p for p in sys.path if "ros" in p.lower()]
    for p in ros_paths:
        sys.path.remove(p)
    os.environ.pop("PYTHONPATH", None)

    sys.exit(pytest.main(["-v", "tests/"]))
