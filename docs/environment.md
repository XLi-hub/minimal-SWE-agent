# 环境分流

这篇讲项目是怎么从**只有一个本地环境**演变到**多环境可切换**的。

## v1 — 一个 Environment 类

最初只有一个 `environment.py`，硬编码 `subprocess.run`。

```python
class Environment:
    def execute(self, command):
        return subprocess.run(command, shell=True, ...).stdout
```

问题：如果要加 Docker 环境，要么改这个类（加 if/else），要么复制粘贴一个——两种都很丑。

## v2 — 抽象基类 + 多实现

创建 `environments/` 子包，分层：

```
environments/
├── __init__.py   ← Environment(ABC) + 注册表 + 工厂函数
├── local.py      ← LocalEnvironment
└── docker.py     ← DockerEnvironment
```

### 接口层（ABC）

```python
class Environment(ABC):
    @abstractmethod
    def execute(self, command: str, timeout: int = 30) -> str: ...
    def cleanup(self) -> None: ...
```

- `execute` 是 `@abstractmethod` —— 子类必须实现，否则实例化时报错
- `cleanup` 有默认空实现 —— Docker 需要（停止容器），Local 不需要

### LocalEnvironment

和原来的 `Environment` 一样——`subprocess.run(command, shell=True, ...)`。加了环境变量覆盖（`PAGER=cat` 等）防止命令卡死。

### DockerEnvironment

三阶段生命周期：

```
__init__ → docker run -d --rm --name X image sleep 2h    # 启动后台容器
execute  → docker exec X bash -lc "command"              # 每调一次执行一次
cleanup  → docker stop X                                  # 销毁容器
```

每个 `DockerEnvironment` 实例有自己的容器。容器名用 `uuid4().hex[:8]` 保证不冲突。

### 为什么用 `sleep 2h` 而不是每次 docker run

```
方案 A（每次 docker run）:
    execute → docker run --rm image bash -c "cmd"
    问题: 每次启动容器有开销，命令之间状态丢失（cd 到某目录下次没了）

方案 B（容器常驻 + docker exec）:
    __init__ → docker run -d ... sleep 2h   ← 容器一直活着
    execute → docker exec X bash -c "cmd"   ← 状态保持
    容器最长活 2h，到时候自己死掉
```

## 注册表模式（Registry Pattern）

核心思想：用 dict 做路由，不用 if/elif 链。

```python
_MAPPING: dict[str, type[Environment]] = {
    "local": LocalEnvironment,
    "docker": DockerEnvironment,
}

def get_environment(name: str, **kwargs) -> Environment:
    cls = _MAPPING[name]       # 查表
    return cls(**kwargs)       # 实例化
```

加新环境只需两步：

1. 写一个类，继承 `Environment`，实现 `execute`
2. 在 `_MAPPING` 里加一行

Agent 完全不用动——它只依赖 `execute(command) -> str`。

## CLI 分流

```bash
python main.py                            # local
python main.py --env docker --image ...   # docker
```

`main.py` 里解析 `--env` 参数，传给 `get_environment()`。和参考项目 mini-swe-agent 的思路一样——配置决定用哪个实现，Agent 代码不变。

## 参考项目的做法

mini-swe-agent 更进一步——用字符串路径动态导入类：

```python
_ENVIRONMENT_MAPPING = {
    "docker": "minisweagent.environments.docker.DockerEnvironment",
    ...
}

def get_environment_class(spec: str):
    module_name, class_name = spec.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)
```

好处是配置可以从 YAML 文件里直接写字符串 `"docker"`，不需要 import 所有环境类。我们项目目前只有两个环境，直接引用类更简单。等环境数量上去了再改成动态导入。
