from sqlalchemy import Column, String, Integer, DateTime, Index

from app.models.base import BaseSchema


class AuthThrottle(BaseSchema):
    """One counter per (what is being limited, who is doing it, which window).

    ★★★DB-backed, not a module-level dict, because the app runs
    ``--workers 4``. A counter in process memory is invisible to the other
    three, so "5 attempts per minute" would actually be twenty — a limit that
    states one number and enforces another. This codebase has already been
    bitten by that twice (the learn-progress tracker and the LLM circuit
    breaker), and for a login limiter the gap is the whole point of the
    feature.

    Rows are disposable. The window is rolled forward in place rather than
    inserting per attempt, so a brute-force flood costs one UPDATE per attempt
    instead of one INSERT plus unbounded growth.
    """

    __tablename__ = "auth_throttle"

    # e.g. "login:ip:203.0.113.7", "login:email:a@b.com", "register:ip:..."
    bucket = Column(String(255), nullable=False, unique=True, index=True)
    window_start = Column(DateTime, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)


Index("ix_auth_throttle_window_start", AuthThrottle.window_start)
