"""Smallest example of using minimal-SWE-agent as a Python library."""

from mini_agent.agent import Agent
from mini_agent.environments.local import LocalEnvironment
from mini_agent.model import Model


TASK = """
Use the bash tool to run this command without creating any files:

python -c 'print("Hello, world!")'

After confirming its exact output, call submit with `Hello, world!`.
""".strip()


def main() -> dict:
    """Run the minimal Model + Environment + Agent composition."""
    model = None
    environment = None
    try:
        model = Model()
        environment = LocalEnvironment()
        agent = Agent(model, environment)
        result = agent.run(TASK, max_steps=5)

        if result["exit_status"] != "submitted":
            raise RuntimeError(f"Agent stopped with {result['exit_status']!r}")

        print(result["submission"])
        return result
    finally:
        try:
            if model is not None:
                model.close()
        finally:
            if environment is not None:
                environment.cleanup()


if __name__ == "__main__":
    main()
