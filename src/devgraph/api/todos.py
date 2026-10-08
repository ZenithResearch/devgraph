"""Additive authenticated Todo reads; existing Work and signed profiles stay pinned."""

from typing import Literal

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse

from devgraph.api.routes import _credential
from devgraph.storage.base import StorageUnavailable
from devgraph.storage.todos import STATUSES


def register_todos(app: FastAPI, services):
    graph = services.authorized_graph

    def unavailable():
        return JSONResponse(
            status_code=503,
            content={
                "title": "Todo read unavailable",
                "status": 503,
                "detail": "Retry the Todo view.",
            },
            headers={"Cache-Control": "no-store"},
        )

    parameters = [
        {"name": key, "in": "query", "required": False, "schema": schema}
        for key, schema in (
            ("limit", {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}),
            ("after_id", {"type": "string", "maxLength": 256}),
            ("status", {"type": "string", "enum": list(STATUSES)}),
            (
                "archived",
                {"type": "string", "enum": ["exclude", "include", "only"], "default": "exclude"},
            ),
            ("q", {"type": "string", "maxLength": 200}),
            ("queue", {"type": "string", "enum": ["not_started"]}),
            ("order", {"type": "string", "enum": ["id", "priority"], "default": "id"}),
            ("after_priority", {"type": "string", "maxLength": 20}),
        )
    ]

    @app.get("/todos/v2")
    def list_v2(
        request: Request, response: Response, authorization: str | None = Header(default=None)
    ):
        response.headers["Cache-Control"] = "no-store"
        return graph.todos_v2(_credential(authorization), request.url.query)

    @app.get("/todos/v2/classification-report")
    def report(response: Response, authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        return graph.progress_report(_credential(authorization))

    @app.get("/todos/v2/{kind}/{todo_id}")
    def detail_v2(
        kind: Literal["Todo", "Proposal", "Initiative", "Project", "Issue", "Task"],
        todo_id: str,
        response: Response,
        authorization: str | None = Header(default=None),
    ):
        response.headers["Cache-Control"] = "no-store"
        return graph.todos_v2(_credential(authorization), kind=kind, work_id=todo_id)

    @app.get("/monitor/todos/v1", openapi_extra={"parameters": parameters})
    def todos(
        request: Request, response: Response, authorization: str | None = Header(default=None)
    ):
        response.headers["Cache-Control"] = "no-store"
        try:
            return graph.monitor_todos(_credential(authorization), request.url.query)
        except StorageUnavailable:
            return unavailable()

    @app.get("/monitor/todos/v1/{todo_id}")
    def todo(todo_id: str, response: Response, authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        try:
            return graph.monitor_todo(_credential(authorization), todo_id)
        except StorageUnavailable:
            return unavailable()
