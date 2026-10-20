"""Resource-owned membership eligibility, independent from holder authentication.

Only runtime v8 can opt out of requiring community membership. The caller still
has to verify the exact holder signature, approved issuer/origin and live grant.
An optional resource accepts an empty proof; a supplied proof must really verify.
"""

import re


class ResourceMembershipAdmission:
    def __init__(self, verify_membership, *, required):
        self.verify_membership, self.required = verify_membership, required

    def __call__(self, proof, holder):
        if not isinstance(holder, str) or not re.fullmatch(r"[0-9a-f]{64}", holder):
            return False
        if not self.required and type(proof) is dict and not proof:
            return True
        return self.verify_membership(proof, holder)
