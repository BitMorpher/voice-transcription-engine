"""Fixed family prerequisite reasons, distinct from provider failures."""

GUIDANCE = {
    'raw_prerequisite': 'This family needs matching complete raw and intact saved artifacts. Preserve caches; resolve raw failures before retrying this family. Other ready families can continue.',
    'review_prerequisite': 'This family needs a matching complete, intact review bundle before chapters. Complete its review and inspect the exact bundle first.',
    'high_findings': 'This family has high review findings; chapters remain blocked. Inspect the exact report and source; do not edit machine reports to bypass the gate.',
    'human_review_required': 'Chapters require explicit selected IDs and actual human review of each requested family; only then supply --human-reviewed.',
    'staging_unverified': 'Staging integrity could not be verified. Preserve the batch and investigate local files before any provider work.',
}


class FamilyBlocked(ValueError):
    """Carry an allowlisted reason without paths, source text or provider data."""

    def __init__(self, reason):
        if reason not in GUIDANCE:
            raise ValueError('Unknown prerequisite reason.')
        self.reason = reason
        super().__init__(GUIDANCE[reason])
