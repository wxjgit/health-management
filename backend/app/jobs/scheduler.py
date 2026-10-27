"""Compatibility entry point for a synchronous planning/drain cycle."""


def tick(engine, reference=None):
    engine.jobs.plan(reference)
    engine.jobs.drain(reference)
