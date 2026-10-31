"""A minimal cleaner to show the interface: clean the files in the current directory, in place.

    piibench run --cmd "python $PWD/examples/rules_cleaner.py"            # from the repository root

`piibench run` copies a task's input into a fresh workspace and runs the command there (the working directory
is the workspace, also available as $PIIBENCH_WORKSPACE). Any program that edits the files in place works the same
way: a script, a rule system, or an LLM agent with file access.
"""

from pathlib import Path

from piibench.cleaners import rules

rules.clean(Path.cwd(), free_text=True)
