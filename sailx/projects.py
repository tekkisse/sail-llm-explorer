"""Project registry: maps users to approved projects and their data scope."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Project:
    id: str
    title: str
    schemas: tuple[str, ...]
    default_schema: str
    members: frozenset[str]
    db_role: str | None = None
    db_credentials_secret: str | None = None
    allow_slack_queries: bool = False


@dataclass
class ProjectRegistry:
    projects: dict[str, Project] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "ProjectRegistry":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        projects = {}
        for pid, p in (raw.get("projects") or {}).items():
            schemas = tuple(s.lower() for s in p["schemas"])
            projects[pid] = Project(
                id=pid,
                title=p.get("title", pid),
                schemas=schemas,
                default_schema=(p.get("default_schema") or schemas[0]).lower(),
                members=frozenset(m.lower() for m in p.get("members", [])),
                db_role=p.get("db_role"),
                db_credentials_secret=p.get("db_credentials_secret"),
                allow_slack_queries=bool(p.get("allow_slack_queries", False)),
            )
        return cls(projects)

    def for_user(self, user: str) -> list[Project]:
        user = user.lower()
        return [p for p in self.projects.values() if user in p.members]

    def authorise(self, user: str, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None or user.lower() not in project.members:
            raise PermissionError(f"{user} is not a member of project {project_id!r}")
        return project
