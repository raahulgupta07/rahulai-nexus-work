"""Ask a running server what is wrong with it, in plain sentences.

    docker exec -w /app/backend bow-app-cai python -m app.core.doctor

★A separate entry point rather than a shell script: the operator here works in
Docker and does not run .sh files, and this needs the application's own
settings, database session and alembic configuration anyway.

Exit code is 0 unless a check FAILED, so it can gate a deploy script. A warning
(no model provider yet, debug left on) does not fail the command — those are
states an operator should see, not states that should stop an upgrade.
"""
import asyncio
import sys


def main() -> int:
    # Imported inside main so that a broken import surfaces as a readable
    # message from this command rather than a traceback at module load.
    try:
        import main as _app_main  # noqa: F401  registers the full ORM registry

        from app.core.health_report import collect, render
        from app.settings.config import settings
    except Exception as e:
        print(f"doctor could not load the application: {type(e).__name__}: {e}")
        return 2

    report = asyncio.run(collect())
    print()
    print(render(report, settings.PROJECT_VERSION))
    print()
    return 1 if report.worst == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
