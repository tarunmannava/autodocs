from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "queued"
    CLONING = "cloning"
    DIFFING = "diffing"
    PLANNING = "planning"
    RUNNING_AGENTS = "running_agents"
    BUILDING = "building"
    PUBLISHED = "published"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PublishedDocStatus(StrEnum):
    GENERATED = "generated"
    COMMITTED = "committed"
    PUBLISHED = "published"
    FAILED = "failed"
