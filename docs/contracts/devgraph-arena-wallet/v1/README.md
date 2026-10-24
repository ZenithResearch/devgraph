# Arena Wallet access

The additive application contract uses access draft v6, member/operator policy v5, read/write operator action v8/v9, enrollment policy v4, read scope/result v5, and signed read runtime v11. Earlier decoders and stored assignments retain their original shape. Wallet authority-policy, credential, and holder-presentation cryptographic v1 formats remain unchanged.

Arena metadata uses the canonical grant `{"scope":"devgraph.arena.read","arena_ids":["research"]}`. IDs are explicit, sorted, unique, valid Arena identifiers, at most 256 per grant. No wildcard or implicit future-Arena permission exists. Work `arena_ids` and resource paths select Work only. They never grant Arena metadata. The owner-reviewed plan v4 adds required `arena_read_ids`; v1–v3 plans reject that field.

The authority projection includes `devgraph.arena.list` and `devgraph.arena.get` on the existing fixed DevGraph resource. Browser Wallet calls retain `/monitor/wallet-access/v1/requests`. Native v3 adds `/devgraph/native-read/v3/{challenges,enrollments,requests}` and uses path parameters: `/arenas`, `/arenas/{id}`, or `/arenas/{id}/members`. Native v2 Work parameter shapes remain closed. List and member pages allow bounded `limit` 1–100 plus `after_id` or `after_resource`; no other query fields are accepted.

Arena members require Arena get and independently granted Work list permissions from the same verified credential. The projection intersects these constraints at storage level. A Work get permission whose constraint mentions list does not supply a list permission. Work views exclude Arena grants before projecting counts and relationships.

The operator browser presents separate unchecked Arena-record choices and reviews exact IDs. Work scope does not preselect them. Selecting Arena metadata makes the selection an exact custom grant rather than implying membership in a Work preset.

Existing grants, enrollments and signed owner ceilings are not expanded on upgrade. Revocation and assignment revisions continue invalidating outstanding credentials. Credentials last at most the approved policy lifetime (the owner compiler retains 900 seconds) and never beyond a dated grant. An until-revoked grant does not make its issued credential permanent. Published v11 endpoints may copy only a signed v11 parent's exact ceiling with a different receiver. An old v9 owner ceiling cannot gain Arena authority from an endpoint publication or an edited file; an explicit reviewed owner upgrade is required.

No new Funnel, Wallet identity, membership, administrator exception, or bearer fallback is introduced. The example contains synthetic configuration only and grants no live authority.
