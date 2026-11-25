"""Agent worker entrypoint (`python -m app.main`). Phase 4: linear planner pipeline;
the LangGraph multi-agent workflow replaces the runner in Phase 5."""

from app.worker import main

if __name__ == "__main__":
    main()
