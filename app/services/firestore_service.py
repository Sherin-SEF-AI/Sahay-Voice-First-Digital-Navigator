"""Firestore service — CRUD operations for task logs and action journals.

Manages persistent storage of SAHAY task records in Google Cloud Firestore.
Handles unavailability gracefully — logs warnings but never crashes the app.
"""

import logging
import time
import uuid
from typing import Any, Optional

from google.cloud import firestore

from ..config import settings

logger = logging.getLogger(__name__)

_client: Optional[firestore.AsyncClient] = None


def _get_client() -> firestore.AsyncClient:
    """Get or create the async Firestore client."""
    global _client
    if _client is None:
        try:
            _client = firestore.AsyncClient(
                project=settings.google_cloud_project,
            )
            logger.info("Firestore client initialized for project: %s", settings.google_cloud_project)
        except Exception as e:
            logger.warning("Failed to initialize Firestore client: %s", e)
            raise
    return _client


def _collection_ref() -> Any:
    """Get a reference to the tasks collection."""
    return _get_client().collection(settings.firestore_collection)


async def create_task(
    user_session_id: str,
    task_description: str,
    language: str = "en",
) -> Optional[str]:
    """Create a new task document in Firestore.

    Args:
        user_session_id: The user's session identifier.
        task_description: Natural language description of the task.
        language: Language code the user spoke in.

    Returns:
        The generated task ID, or None if Firestore is unavailable.
    """
    task_id = str(uuid.uuid4())
    doc = {
        "id": task_id,
        "user_session_id": user_session_id,
        "timestamp": time.time(),
        "task_description": task_description,
        "language": language,
        "status": "in_progress",
        "steps": [],
        "outcome": "",
        "screenshots_count": 0,
    }
    try:
        await _collection_ref().document(task_id).set(doc)
        logger.info("Task created: %s", task_id)
        return task_id
    except Exception as e:
        logger.warning("Failed to create task in Firestore: %s", e)
        return None


async def update_task(
    task_id: str,
    updates: dict[str, Any],
) -> bool:
    """Update fields on an existing task document.

    Args:
        task_id: The task document ID.
        updates: Dictionary of fields to update.

    Returns:
        True if update succeeded, False otherwise.
    """
    try:
        await _collection_ref().document(task_id).update(updates)
        logger.info("Task updated: %s", task_id)
        return True
    except Exception as e:
        logger.warning("Failed to update task %s: %s", task_id, e)
        return False


async def add_step(
    task_id: str,
    step: dict[str, Any],
) -> bool:
    """Append a step entry to a task's steps array.

    Args:
        task_id: The task document ID.
        step: Step data dict (action, description, timestamp, success).

    Returns:
        True if update succeeded.
    """
    try:
        # Strip screenshots/large binary data to stay under 1MB doc limit
        clean_step = {k: v for k, v in step.items() if k not in ("screenshot", "screenshot_after", "screenshot_before") and not (isinstance(v, str) and len(v) > 5000)}
        await _collection_ref().document(task_id).update(
            {"steps": firestore.ArrayUnion([clean_step])}
        )
        return True
    except Exception as e:
        logger.warning("Failed to add step to task %s: %s", task_id, e)
        return False


async def complete_task(
    task_id: str,
    outcome: str,
    screenshots_count: int = 0,
) -> bool:
    """Mark a task as completed with its outcome.

    Args:
        task_id: The task document ID.
        outcome: Description of the final result.
        screenshots_count: Number of screenshots captured.

    Returns:
        True if update succeeded.
    """
    return await update_task(
        task_id,
        {
            "status": "completed",
            "outcome": outcome,
            "screenshots_count": screenshots_count,
            "completed_at": time.time(),
        },
    )


async def fail_task(task_id: str, reason: str) -> bool:
    """Mark a task as failed.

    Args:
        task_id: The task document ID.
        reason: Why the task failed.

    Returns:
        True if update succeeded.
    """
    return await update_task(
        task_id,
        {
            "status": "failed",
            "outcome": f"FAILED: {reason}",
            "completed_at": time.time(),
        },
    )


async def get_recent_tasks(
    n: int = 5,
    user_session_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Retrieve the most recent tasks.

    Args:
        n: Number of tasks to retrieve.
        user_session_id: Optional filter by session.

    Returns:
        List of task documents ordered by timestamp descending.
    """
    try:
        query = _collection_ref().order_by(
            "timestamp", direction=firestore.Query.DESCENDING
        ).limit(n)

        if user_session_id:
            query = query.where("user_session_id", "==", user_session_id)

        docs = []
        async for doc in query.stream():
            docs.append(doc.to_dict())

        logger.info("Retrieved %d recent tasks", len(docs))
        return docs
    except Exception as e:
        logger.warning("Failed to retrieve tasks from Firestore: %s", e)
        return []


async def get_task(task_id: str) -> Optional[dict[str, Any]]:
    """Retrieve a single task by ID.

    Args:
        task_id: The task document ID.

    Returns:
        Task document dict, or None if not found.
    """
    try:
        doc = await _collection_ref().document(task_id).get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        logger.warning("Failed to retrieve task %s: %s", task_id, e)
        return None
