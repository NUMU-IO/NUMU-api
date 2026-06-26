"""Read/write tool executors for the NUMU Agent.

Each executor calls an EXISTING NUMU-api repository/service (Constitution IV —
reuse, don't reinvent) and re-checks permission before doing work (fail closed).
"""
