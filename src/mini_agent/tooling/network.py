"""Pure detection of shell commands that require external network access."""

import re
import shlex


_NETWORK_CLIENTS = {"curl", "wget", "ftp", "sftp", "scp", "rsync"}
_PACKAGE_MANAGER_VERBS = {
    "apt": {"install", "update", "upgrade", "download"},
    "apt-get": {"install", "update", "upgrade", "download"},
    "apk": {"add", "update", "upgrade", "fetch"},
    "yum": {"install", "update", "upgrade", "download"},
    "dnf": {"install", "update", "upgrade", "download"},
    "npm": {"install", "add", "update"},
    "yarn": {"install", "add", "upgrade"},
    "pnpm": {"install", "add", "update"},
    "conda": {"install", "update", "create"},
    "mamba": {"install", "update", "create"},
    "micromamba": {"install", "update", "create"},
    "gem": {"install", "update"},
    "cargo": {"install"},
    "go": {"get", "install"},
}
_GIT_NETWORK_VERBS = {"clone", "fetch", "pull", "push", "ls-remote"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
_CONTROL_WORDS = {"!", "if", "then", "elif", "while", "until", "do"}


def find_network_command(command: str) -> str | None:
    """Return the first obvious external-network command in a shell string."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        # The shell will report malformed quoting.  Network isolation remains
        # enforced independently by the container runtime.
        return None

    segment: list[str] = []
    for token in [*tokens, ";"]:
        if token and all(char in ";&|()" for char in token):
            blocked = _network_command_in_segment(segment)
            if blocked is not None:
                return blocked
            segment = []
        else:
            segment.append(token)
    return None


def _network_command_in_segment(tokens: list[str]) -> str | None:
    tokens = list(tokens)
    while tokens and tokens[0] in _CONTROL_WORDS:
        tokens.pop(0)
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        tokens.pop(0)
    if not tokens:
        return None

    executable = tokens.pop(0).rsplit("/", 1)[-1].lower()
    while executable in {"sudo", "env", "command", "nohup", "timeout"}:
        if executable == "timeout":
            while tokens and tokens[0].startswith("-"):
                tokens.pop(0)
            if tokens:
                tokens.pop(0)  # duration
        else:
            while tokens and (
                tokens[0].startswith("-")
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0])
            ):
                tokens.pop(0)
        if not tokens:
            return None
        executable = tokens.pop(0).rsplit("/", 1)[-1].lower()

    if executable in _SHELLS and "-c" in tokens:
        index = tokens.index("-c")
        if index + 1 < len(tokens):
            return find_network_command(tokens[index + 1])

    lowered = [token.lower() for token in tokens]
    if executable in _NETWORK_CLIENTS:
        return executable
    if re.fullmatch(r"pip(?:\d+(?:\.\d+)*)?", executable):
        pip_verb = next((token for token in lowered if token in {"install", "download"}), "")
        if pip_verb == "download" or (pip_verb == "install" and "--no-index" not in lowered):
            return f"{executable} {pip_verb}"
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
        if len(lowered) >= 2 and lowered[0:2] == ["-m", "pip"]:
            pip_verb = next(
                (token for token in lowered[2:] if token in {"install", "download"}),
                "",
            )
            if pip_verb == "download" or (
                pip_verb == "install" and "--no-index" not in lowered
            ):
                return f"{executable} -m pip {pip_verb}"
    if executable == "git":
        git_verb = next((token for token in lowered if token in _GIT_NETWORK_VERBS), "")
        if git_verb:
            return f"git {git_verb}"
    if executable in _PACKAGE_MANAGER_VERBS:
        manager_verb = next(
            (token for token in lowered if token in _PACKAGE_MANAGER_VERBS[executable]),
            "",
        )
        if manager_verb:
            return f"{executable} {manager_verb}"
    return None


__all__ = ["find_network_command"]
