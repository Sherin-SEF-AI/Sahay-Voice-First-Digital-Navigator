"""SAHAY Services — Firestore, task journal, GPA, and templates."""

from .firestore_service import (
    create_task,
    update_task,
    add_step,
    complete_task,
    fail_task,
    get_recent_tasks,
    get_task,
)
from .task_journal import (
    JournalEntry,
    TaskJournal,
    ActionStatus,
    GPAStep,
    GPAActionLog,
)
from .task_templates import (
    ServiceTemplate,
    get_template,
    get_context_hint,
    find_service_by_keyword,
)
from .entity_extractor import EntityExtractor, ExtractedEntity
from .workflow_recorder import WorkflowRecorder, RecordedWorkflow
from .workflow_orchestrator import WorkflowOrchestrator, WorkflowStep
from .guardian_service import GuardianService, GuardianConfig, TaskNotification
from .upi_service import UPIService, UPIPayment, PaymentInfo
from .screenshot_diff import ScreenshotDiffEngine, DiffResult

# Simple form memory — stores extracted user info for future form fills
FormMemory = dict  # Type alias: keys are field names, values are extracted strings

__all__ = [
    "create_task",
    "update_task",
    "add_step",
    "complete_task",
    "fail_task",
    "get_recent_tasks",
    "get_task",
    "JournalEntry",
    "TaskJournal",
    "ActionStatus",
    "GPAStep",
    "GPAActionLog",
    "ServiceTemplate",
    "get_template",
    "get_context_hint",
    "find_service_by_keyword",
    "EntityExtractor",
    "ExtractedEntity",
    "WorkflowRecorder",
    "RecordedWorkflow",
    "WorkflowOrchestrator",
    "WorkflowStep",
    "GuardianService",
    "GuardianConfig",
    "TaskNotification",
    "UPIService",
    "UPIPayment",
    "PaymentInfo",
    "ScreenshotDiffEngine",
    "DiffResult",
    "FormMemory",
]
