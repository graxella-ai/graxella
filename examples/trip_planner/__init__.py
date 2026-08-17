"""Shared trip-planner code — imported by both notebooks in this directory.

Kept as a package so `01_pure_langgraph.ipynb` and `02_with_graxella.ipynb`
run the SAME LangGraph app. The only difference between the two notebooks is
whether the app is wrapped with `graxella.wrap(...)`.
"""
