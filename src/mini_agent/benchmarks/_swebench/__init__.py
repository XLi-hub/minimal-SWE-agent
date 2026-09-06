"""Internal building blocks for :mod:`mini_agent.benchmarks.swebench`.

The public compatibility surface remains in ``swebench.py``.  These modules
separate stateless dataset handling from prediction persistence so each piece
can evolve and be tested without importing the runner orchestration.
"""

