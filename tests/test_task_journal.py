"""Tests for the Task Journal system."""

import time
import pytest
from unittest.mock import AsyncMock, patch

from app.services.task_journal import JournalEntry, TaskJournal


class TestJournalEntry:
    """Tests for JournalEntry dataclass."""

    def test_create_entry(self):
        """JournalEntry should store all fields."""
        entry = JournalEntry(
            step_number=1,
            timestamp=time.time(),
            action_type="click_at",
            action_description="Clicking the login button",
            url="https://example.com",
            success=True,
        )
        assert entry.step_number == 1
        assert entry.action_type == "click_at"
        assert entry.success is True
        assert entry.error is None

    def test_entry_with_error(self):
        """JournalEntry should store error information."""
        entry = JournalEntry(
            step_number=2,
            timestamp=time.time(),
            action_type="navigate",
            action_description="Navigating to portal",
            success=False,
            error="Page timeout",
        )
        assert entry.success is False
        assert entry.error == "Page timeout"

    def test_entry_with_screenshots(self):
        """JournalEntry should store screenshot bytes."""
        entry = JournalEntry(
            step_number=1,
            timestamp=time.time(),
            action_type="click_at",
            action_description="Click",
            screenshot_before=b"before_png",
            screenshot_after=b"after_png",
        )
        assert entry.screenshot_before == b"before_png"
        assert entry.screenshot_after == b"after_png"


class TestTaskJournal:
    """Tests for TaskJournal management."""

    def test_empty_journal(self):
        """New journal should have no entries."""
        journal = TaskJournal("test-id", "Test task")
        assert journal.entry_count == 0

    def test_add_entry(self):
        """Adding entries should increment count."""
        journal = TaskJournal("test-id", "Test task")
        entry = JournalEntry(
            step_number=1,
            timestamp=time.time(),
            action_type="navigate",
            action_description="Opening website",
        )
        journal.add_entry(entry)
        assert journal.entry_count == 1

    def test_create_entry_auto_numbering(self):
        """create_entry should auto-number steps."""
        journal = TaskJournal("test-id", "Test task")
        e1 = journal.create_entry("navigate", "Step one")
        e2 = journal.create_entry("click_at", "Step two")
        assert e1.step_number == 1
        assert e2.step_number == 2

    def test_get_summary_empty(self):
        """Empty journal summary should say no actions."""
        journal = TaskJournal("test-id", "Test task")
        summary = journal.get_summary()
        assert "No actions" in summary

    def test_get_summary_with_entries(self):
        """Summary should include all step descriptions."""
        journal = TaskJournal("test-id", "Book train ticket")
        journal.create_entry("navigate", "Opened IRCTC", success=True)
        journal.create_entry("click_at", "Clicked login", success=True)
        journal.create_entry("type_text_at", "Typed username", success=False, error="Timeout")

        summary = journal.get_summary()
        assert "Book train ticket" in summary
        assert "Opened IRCTC" in summary
        assert "Clicked login" in summary
        assert "Typed username" in summary
        assert "2 successful" in summary
        assert "1 failed" in summary

    def test_get_full_journal(self):
        """Full journal should return all entries in order."""
        journal = TaskJournal("test-id", "Test task")
        journal.create_entry("step1", "First")
        journal.create_entry("step2", "Second")
        entries = journal.get_full_journal()
        assert len(entries) == 2
        assert entries[0].action_type == "step1"
        assert entries[1].action_type == "step2"

    def test_get_entries_for_display(self):
        """Display entries should be JSON-serializable dicts."""
        journal = TaskJournal("test-id", "Test task")
        journal.create_entry(
            "click_at",
            "Clicked button",
            url="https://example.com",
            success=True,
        )
        display = journal.get_entries_for_display()
        assert len(display) == 1
        assert display[0]["step"] == 1
        assert display[0]["action"] == "click_at"
        assert display[0]["description"] == "Clicked button"
        assert display[0]["url"] == "https://example.com"
        assert display[0]["success"] is True

    def test_task_id_property(self):
        """task_id property should return the ID."""
        journal = TaskJournal("my-task-id", "Test")
        assert journal.task_id == "my-task-id"

    @patch("app.services.task_journal.firestore_service")
    def test_save_to_firestore(self, mock_fs):
        """save_to_firestore should call update_task."""
        mock_fs.update_task = AsyncMock(return_value=True)
        journal = TaskJournal("test-id", "Test task")
        journal.create_entry("click", "Clicked", success=True)

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            journal.save_to_firestore()
        )
        assert result is True
        mock_fs.update_task.assert_called_once()
