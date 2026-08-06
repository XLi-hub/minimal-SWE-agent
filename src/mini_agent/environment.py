"""向后兼容的导入路径 — 让旧代码继续工作."""

from src.mini_agent.environments.local import LocalEnvironment as Environment

execute_action = Environment().execute
