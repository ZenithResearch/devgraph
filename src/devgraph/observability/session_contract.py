"""Closed additive v2 Work operation for explicit session attribution.

The signed intent names the Work subject, native source identity and optional
source-witnessed link. Derived IDs participate in authorization like any other
mutated record; metadata observations do not supply authority.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from devgraph.observability.session_ref import SessionIdentity, SessionLink

UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)")


def observed_time(value: str) -> datetime:
    if not isinstance(value, str) or not UTC.fullmatch(value):
        raise ValueError("invalid_session_observation_time")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() != timezone.utc.utcoffset(result):
        raise ValueError("invalid_session_observation_time")
    return result


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class SessionStateSet(Strict):
    state: Literal["open", "done"]


class NativeIdentity(Strict):
    application: str
    source_instance_id: str
    external_session_id: str

    def identity(self):
        return SessionIdentity(**self.model_dump())

    @model_validator(mode="after")
    def valid(self):
        self.identity()
        return self


class LinkWitness(Strict):
    source_instance_id: str
    event_id: str
    attempt_id: str
    relation: Literal["delegated_to", "forked_from", "uses_runtime"]
    origin: NativeIdentity
    target: NativeIdentity
    observed_at: str
    basis: Literal["declared", "source_observed"]

    def link(self):
        return SessionLink(
            self.source_instance_id,
            self.event_id,
            self.attempt_id,
            self.relation,
            self.origin.identity(),
            self.target.identity(),
            observed_time(self.observed_at),
            self.basis,
        )

    @model_validator(mode="after")
    def valid(self):
        self.link()
        return self


class SessionAttach(Strict):
    session: NativeIdentity
    observed_at: str
    basis: Literal["inspected", "declared_scope", "source_observed"]
    link: LinkWitness | None = None

    _time = field_validator("observed_at")(lambda value: (observed_time(value), value)[1])

    @model_validator(mode="after")
    def related(self):
        if self.link and self.session.identity() not in (
            self.link.origin.identity(),
            self.link.target.identity(),
        ):
            raise ValueError("unrelated_session_link")
        return self

    def resources(self):
        identities = [self.session.identity()]
        result = set()
        if self.link:
            identities.extend((self.link.origin.identity(), self.link.target.identity()))
            result.add("SessionLink/" + self.link.link().id)
        result.update("SessionRef/" + identity.id for identity in identities)
        return result
