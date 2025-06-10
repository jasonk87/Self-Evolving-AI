import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import sys
import json
from datetime import datetime, timezone, timedelta

# Ensure ai_assistant module can be imported
try:
    from ai_assistant.core import project_manager
except ImportError: # pragma: no cover
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.core import project_manager

class TestProjectManager(unittest.TestCase):
    def setUp(self):
        # Mock _load_projects and _save_projects to control data in memory for tests
        self.load_patcher = patch('ai_assistant.core.project_manager._load_projects')
        self.save_patcher = patch('ai_assistant.core.project_manager._save_projects')

        self.mock_load_projects = self.load_patcher.start()
        self.mock_save_projects = self.save_patcher.start()

        self.initial_projects = [] # Start with an empty list for most tests
        self.mock_load_projects.return_value = self.initial_projects
        self.mock_save_projects.return_value = True # Assume save is successful

        # Helper to capture what's passed to save_projects
        self.saved_projects_capture = None
        def capture_save(projects_data):
            self.saved_projects_capture = projects_data
            return True
        self.mock_save_projects.side_effect = capture_save

    def tearDown(self):
        self.load_patcher.stop()
        self.save_patcher.stop()

    # --- Tests for create_project ---
    def test_create_project_success(self):
        project_name = "Test Project 1"
        project_desc = "A test project."
        created_project = project_manager.create_project(project_name, project_desc)

        self.assertIsNotNone(created_project)
        self.assertEqual(created_project['name'], project_name)
        self.assertEqual(created_project['description'], project_desc)
        self.assertIn('project_id', created_project)
        self.mock_save_projects.assert_called_once()
        self.assertEqual(len(self.saved_projects_capture), 1)
        self.assertEqual(self.saved_projects_capture[0]['name'], project_name)

    def test_create_project_name_conflict(self):
        project_name = "Existing Project"
        self.initial_projects.append({"project_id": "id1", "name": project_name, "description": "", "status": "active", "created_at": "", "updated_at": "", "tasks": []})
        self.mock_load_projects.return_value = self.initial_projects # Update mock

        created_project = project_manager.create_project(project_name, "New desc")
        self.assertIsNone(created_project)
        self.mock_save_projects.assert_not_called() # Save should not be called

    # --- Tests for find_project ---
    def test_find_project_by_id_success(self):
        proj1 = {"project_id": "proj_id_123", "name": "Project Alpha", "description": "Alpha desc"}
        self.initial_projects.extend([proj1])
        self.mock_load_projects.return_value = self.initial_projects

        found = project_manager.find_project("proj_id_123")
        self.assertEqual(found, proj1)

    def test_find_project_by_name_success(self):
        proj1 = {"project_id": "proj_id_123", "name": "Project Alpha", "description": "Alpha desc"}
        self.initial_projects.extend([proj1])
        self.mock_load_projects.return_value = self.initial_projects

        found = project_manager.find_project("Project Alpha")
        self.assertEqual(found, proj1)
        found_case_insensitive = project_manager.find_project("project alpha")
        self.assertEqual(found_case_insensitive, proj1)

    def test_find_project_not_found(self):
        self.mock_load_projects.return_value = []
        found = project_manager.find_project("non_existent_id_or_name")
        self.assertIsNone(found)

    # --- Tests for update_project ---
    def test_update_project_name_and_description_success(self):
        proj_id = "update_id_1"
        original_name = "Original Name"
        original_updated_at = datetime.now(timezone.utc) - timedelta(days=1)
        self.initial_projects.append({"project_id": proj_id, "name": original_name, "description": "Old Desc", "updated_at": original_updated_at.isoformat()})
        self.mock_load_projects.return_value = self.initial_projects

        updated_project = project_manager.update_project(proj_id, "New Updated Name", "New Desc")
        self.assertIsNotNone(updated_project)
        self.assertEqual(updated_project['name'], "New Updated Name")
        self.assertEqual(updated_project['description'], "New Desc")
        self.assertNotEqual(updated_project['updated_at'], original_updated_at.isoformat()) # Timestamp should change
        self.mock_save_projects.assert_called_once()
        self.assertEqual(self.saved_projects_capture[0]['name'], "New Updated Name")

    def test_update_project_only_name(self):
        proj_id = "update_id_only_name"
        original_desc = "Description that stays"
        original_updated_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.initial_projects.append({"project_id": proj_id, "name": "NameBefore", "description": original_desc, "updated_at": original_updated_at})
        self.mock_load_projects.return_value = self.initial_projects

        updated_project = project_manager.update_project(proj_id, new_name="NameAfter")
        self.assertIsNotNone(updated_project)
        self.assertEqual(updated_project['name'], "NameAfter")
        self.assertEqual(updated_project['description'], original_desc) # Description should be unchanged
        self.assertNotEqual(updated_project['updated_at'], original_updated_at)
        self.mock_save_projects.assert_called_once()

    def test_update_project_only_description(self):
        proj_id = "update_id_only_desc"
        original_name = "NameThatStays"
        original_updated_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.initial_projects.append({"project_id": proj_id, "name": original_name, "description": "DescBefore", "updated_at": original_updated_at})
        self.mock_load_projects.return_value = self.initial_projects

        updated_project = project_manager.update_project(proj_id, new_description="DescAfter")
        self.assertIsNotNone(updated_project)
        self.assertEqual(updated_project['name'], original_name) # Name should be unchanged
        self.assertEqual(updated_project['description'], "DescAfter")
        self.assertNotEqual(updated_project['updated_at'], original_updated_at)
        self.mock_save_projects.assert_called_once()

    def test_update_project_no_changes_provided(self):
        proj_id = "update_id_no_change_args"
        self.initial_projects.append({"project_id": proj_id, "name": "NoChangeProj", "description": "Desc"})
        self.mock_load_projects.return_value = self.initial_projects

        updated_project = project_manager.update_project(proj_id) # No new_name or new_description
        self.assertIsNone(updated_project)
        self.mock_save_projects.assert_not_called()

    def test_update_project_no_actual_changes_made(self):
        proj_id = "update_id_no_actual_change"
        name = "Same Name"
        desc = "Same Desc"
        self.initial_projects.append({"project_id": proj_id, "name": name, "description": desc})
        self.mock_load_projects.return_value = self.initial_projects

        updated_project = project_manager.update_project(proj_id, name, desc)
        self.assertIsNotNone(updated_project)
        self.assertEqual(updated_project['name'], name)
        self.mock_save_projects.assert_not_called()

    def test_update_project_name_conflict(self):
        proj1_id = "id_proj1_conflict"
        proj2_id = "id_proj2_conflict"
        self.initial_projects.extend([
            {"project_id": proj1_id, "name": "Project One Conflict", "description": ""},
            {"project_id": proj2_id, "name": "Project Two Conflict", "description": ""}
        ])
        self.mock_load_projects.return_value = self.initial_projects

        updated_project = project_manager.update_project(proj1_id, new_name="Project Two Conflict")
        self.assertIsNone(updated_project)
        self.mock_save_projects.assert_not_called()

    def test_update_project_not_found(self):
        self.mock_load_projects.return_value = []
        updated_project = project_manager.update_project("non_existent_for_update", new_name="New Name")
        self.assertIsNone(updated_project)

    # --- Tests for remove_project ---
    def test_remove_project_success_by_id(self):
        proj_id_to_remove = "remove_me_id_test"
        self.initial_projects.append({"project_id": proj_id_to_remove, "name": "ToRemove", "description": ""})
        self.initial_projects.append({"project_id": "keep_me_id_test", "name": "ToKeep", "description": ""})
        self.mock_load_projects.return_value = self.initial_projects

        removed = project_manager.remove_project(proj_id_to_remove)
        self.assertTrue(removed)
        self.mock_save_projects.assert_called_once()
        self.assertEqual(len(self.saved_projects_capture), 1)
        self.assertEqual(self.saved_projects_capture[0]['name'], "ToKeep")

    def test_remove_project_not_found(self):
        self.mock_load_projects.return_value = []
        removed = project_manager.remove_project("non_existent_for_remove")
        self.assertFalse(removed)
        self.mock_save_projects.assert_not_called()

    # --- Tests for _load_projects and _save_projects (mocked, but test their interaction) ---
    @patch('ai_assistant.core.project_manager.os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_load_projects_file_not_found(self, mock_file, mock_exists):
        mock_exists.return_value = False
        self.load_patcher.stop()
        try:
            projects = project_manager.list_projects()
            self.assertEqual(projects, [])
        finally:
            self.load_patcher.start()

    @patch('ai_assistant.core.project_manager.os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data='[{"name": "Loaded Project"}]')
    def test_load_projects_success(self, mock_file, mock_exists):
        mock_exists.return_value = True
        self.load_patcher.stop()
        try:
            projects = project_manager.list_projects()
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]['name'], "Loaded Project")
        finally:
            self.load_patcher.start()

    @patch('ai_assistant.core.project_manager.os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data='') # Empty file
    def test_load_projects_empty_file(self, mock_file, mock_exists):
        mock_exists.return_value = True
        self.load_patcher.stop()
        try:
            projects = project_manager.list_projects()
            self.assertEqual(projects, [])
        finally:
            self.load_patcher.start()

    @patch('ai_assistant.core.project_manager.os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data='invalid json') # Malformed JSON
    def test_load_projects_json_decode_error(self, mock_file, mock_exists):
        mock_exists.return_value = True
        self.load_patcher.stop()
        try:
            projects = project_manager.list_projects()
            self.assertEqual(projects, []) # Should return empty list on error
        finally:
            self.load_patcher.start()

if __name__ == '__main__': # pragma: no cover
    unittest.main()
