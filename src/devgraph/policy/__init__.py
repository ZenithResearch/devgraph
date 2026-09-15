"""Partition-aware export and redaction policy (Issue #16).

Owns export-mode vocabulary, private-resource classification, redaction
helpers, and scope-gated export filtering. Consumes the Issue #7
authority context; it never validates credentials itself.
"""
