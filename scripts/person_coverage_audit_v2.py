#!/usr/bin/env python3
"""Registered exact-plan person-presence audit; legacy batch count entry is disabled."""

from __future__ import annotations

def registered_vision(plan, sources, detector, *, paths, allow_vfr_approximate=False):
    from adaptive_vision import audit
    return audit(paths, plan, sources, detector, allow_vfr_approximate=allow_vfr_approximate)


def registered_prescreen(plan, sources, detector, *, paths, allow_vfr_approximate=False):
    from adaptive_vision import audit
    return audit(paths, plan, sources, detector, allow_vfr_approximate=allow_vfr_approximate,
                 audit_mode='selection_prescreen')


def main() -> None:
    raise SystemExit('legacy_person_counts_not_person_subject_evidence: use registered scheduler exact-plan audit')


if __name__ == "__main__":
    main()
