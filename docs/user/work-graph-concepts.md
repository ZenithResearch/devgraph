# Work-graph concepts

Generic work progresses through Proposal, Initiative, Project, Issue, and Task
resources. Work objects carry title, description, status, version, priority,
and artifact/external-link references. Stored relationships express parentage,
blockers, dependencies, requirements, acceptance criteria, handoffs, review
packets, and decision/conversion provenance.

Statuses are draft, review, accepted, and archived. Proposal acceptance is a
special lifecycle operation requiring Decision provenance; archive is also a
dedicated operation. Priority can be inherited through parent relationships.

Artifacts hold evidence. `InitiativeObservation` is an append-only inferred,
unclaimed Artifact profile for observations of GitHub repositories or
organizations. It is not itself a canonical Initiative or proof of maintainer
ownership.
