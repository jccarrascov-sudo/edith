"""Recipes: "Show it once. Edith does the next ones."

A recipe is a task the user did once by hand (e.g. a Word letter from a row of an Excel sheet),
compiled by Muse Spark into a program at the lowest level of the cascade that reproduces the
user's own result, verified on copies, and replayed with new values, always as a preview first.

  sandbox.py  the program's whitelist (AST) and its isolated process (audit hooks, timeout)
  docs.py     reading Excel/Word files and comparing two Word documents
  store.py    the recipe memory (state/recipes/), editable and deletable
  learn.py    finding the demonstration and compiling it, level by level, with verification
  run.py      choosing the recipe and its values for a request, running it, apply and undo
"""
