"""Shared data models."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class ErrorEntry:
    """A single error extracted from logs."""
    timestamp: str
    service: str
    environment: str
    level: str  # ERROR, CRITICAL
    message: str
    stack_trace: str = ""
    logger: str = ""
    trace_id: str = ""
    source: str = ""  # log group or source identifier

    @property
    def fingerprint(self) -> str:
        """Generate a unique fingerprint for deduplication.

        Based on: service + error class + first meaningful stack frame.
        """
        # Extract error class from message (e.g., "NullPointerException")
        error_class = self.message.split(":")[0].strip() if self.message else "unknown"

        # Extract first app stack frame (skip framework frames)
        first_frame = ""
        if self.stack_trace:
            for line in self.stack_trace.split("\n"):
                line = line.strip()
                if line.startswith("at ") and ("drcall" in line or "spherecorp" in line):
                    first_frame = line
                    break

        raw = f"{self.service}|{error_class}|{first_frame}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fingerprint"] = self.fingerprint
        return d


@dataclass
class ErrorAnalysis:
    """AI analysis result for an error."""
    fingerprint: str
    category: str  # NullPointer, Timeout, AuthFailed, ExternalAPI, DB, Kafka, Config, Unknown
    severity: str  # critical, high, medium, low
    is_actionable: bool
    summary: str
    root_cause: str = ""
    affected_files: list[str] = field(default_factory=list)
    suggested_fix: str = ""
    confidence: float = 0.0
    issue_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TargetConfig:
    """A monitoring target (project/service group)."""
    name: str
    type: str  # eks, ec2-docker, tomcat, cloudwatch, loki
    region: str
    log_groups: list[dict]  # [{"pattern": "/drcall/prod/*"}]
    github_owner: str
    github_repo: str
    services: list[str] = field(default_factory=list)
    branch: str = "main"
    # Loki-specific
    loki_url: str = ""
    loki_queries: dict = field(default_factory=dict)  # {"backend": "...", "frontend": "..."}
    # Multi-repo: source_type -> repo name (e.g., {"backend": "repo-backend", "frontend": "repo-frontend"})
    github_repos: dict = field(default_factory=dict)

    def get_repo_for_source(self, source: str) -> str:
        """Get GitHub repo name based on error source type (backend/frontend)."""
        if self.github_repos:
            if "frontend" in source:
                return self.github_repos.get("frontend", self.github_repo)
            return self.github_repos.get("backend", self.github_repo)
        return self.github_repo

    @classmethod
    def from_dict(cls, data: dict) -> TargetConfig:
        github = data.get("github", {})
        return cls(
            name=data["name"],
            type=data.get("type", "cloudwatch"),
            region=data.get("region", "ap-southeast-7"),
            log_groups=data.get("log_groups", []),
            github_owner=github.get("owner", ""),
            github_repo=github.get("repo", github.get("repos", {}).get("backend", "")),
            services=data.get("services", []),
            branch=data.get("branch", "main"),
            loki_url=data.get("loki_url", ""),
            loki_queries=data.get("loki_queries", {}),
            github_repos=github.get("repos", {}),
        )
