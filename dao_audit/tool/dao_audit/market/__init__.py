"""Real-data market/event module.

Hard integrity rule: results come ONLY from real data. When a source is
unreachable or stale, the record is marked UNAVAILABLE/STALE and downstream
decisions become NO TRADE / INSUFFICIENT_DATA. Simulated market data is NEVER
substituted for real quotes, and no fixed return is ever promised.

This module is kept separate from the VULN-1..5 security detectors: security
findings and trading signals are managed independently.
"""
