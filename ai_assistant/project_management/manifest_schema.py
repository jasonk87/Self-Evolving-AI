# Self-Evolving-Agent-feat-chat-history-context/ai_assistant/project_management/manifest_schema.py
from dataclasses import dataclass, field, asdict as dataclass_asdict # Import asdict
from typing import List, Dict, Any, Optional
import json # For the __main__ example

# Using Pydantic for actual validation would be better in a production system,
# but dataclasses are good for schema definition and type hinting.

@dataclass
class Dependency:
    name: str
    version: Optional[str] = None
    type: Optional[str] = None # e.g., "pip", "npm", "maven"
    notes: Optional[str] = None

@dataclass
class BuildConfig:
    build_command: Optional[str] = None
    output_directory: Optional[str] = None # Relative to project root
    source_directories: List[str] = field(default_factory=lambda: ["src"])

@dataclass
class TestConfig:
    test_command: Optional[str] = None
    test_directory: Optional[str] = field(default_factory=lambda: "tests") # Relative to project root

@dataclass
class DevelopmentTask:
    task_id: str
    task_type: str # e.g., "CREATE_FILE", "MODIFY_FILE", "ADD_FUNCTION", "INSTALL_DEPENDENCY"
    description: str
    details: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending" # "pending", "in_progress", "completed", "failed"
    dependencies: List[str] = field(default_factory=list) # List of task_ids
    last_attempt_timestamp: Optional[str] = None
    error_message: Optional[str] = None
    notes: Optional[str] = None

@dataclass
class ProjectManifest:
    project_name: str
    sanitized_project_name: str # Filesystem-friendly
    project_directory: str # Relative to ai_generated_projects/
    project_description: str
    creation_timestamp: str # ISO format string
    last_modified_timestamp: str # ISO format string
    
    version: str = "0.1.0" # Project's own version
    manifest_version: str = "1.1.0" # Schema version (fixed for this definition)
    
    project_type: Optional[str] = None # e.g., "python", "javascript", "java", "general"
    entry_points: Dict[str, str] = field(default_factory=dict) # e.g., {"cli": "python src/main.py"}
    
    dependencies: List[Dependency] = field(default_factory=list)
    build_config: Optional[BuildConfig] = None
    test_config: Optional[TestConfig] = None
    
    project_goals: List[str] = field(default_factory=list) # More detailed objectives
    development_tasks: List[DevelopmentTask] = field(default_factory=list)
    
    project_notes: Optional[str] = None

    def to_json_dict(self) -> Dict[str, Any]:
        """Converts the manifest to a dictionary suitable for JSON serialization using dataclasses.asdict.
        This handles nested dataclasses correctly.
        """
        return dataclass_asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProjectManifest':
        """Creates a ProjectManifest instance from a dictionary.
        This needs to handle reconstruction of nested dataclasses.
        """
        # Basic reconstruction; more robust parsing might be needed for production
        # (e.g., handling date strings to datetime objects if they were stored as such,
        # or ensuring nested dicts are converted to their respective dataclasses).
        
        # Convert lists of dicts back to lists of dataclass instances
        dependencies_data = data.get('dependencies', [])
        data['dependencies'] = [Dependency(**dep_data) for dep_data in dependencies_data]

        build_config_data = data.get('build_config')
        if build_config_data:
            data['build_config'] = BuildConfig(**build_config_data)

        test_config_data = data.get('test_config')
        if test_config_data:
            data['test_config'] = TestConfig(**test_config_data)
            
        dev_tasks_data = data.get('development_tasks', [])
        data['development_tasks'] = [DevelopmentTask(**task_data) for task_data in dev_tasks_data]

        # Ensure manifest_version is correctly set if it was missing in data (though unlikely for new files)
        if 'manifest_version' not in data:
            data['manifest_version'] = "1.1.0"

        return cls(**data)

if __name__ == '__main__':
    # Example Usage:
    build_conf = BuildConfig(build_command="python setup.py build", source_directories=["app"])
    test_conf = TestConfig(test_command="pytest")
    
    dep1 = Dependency(name="requests", version="2.25.1", type="pip")
    
    task1 = DevelopmentTask(
        task_id="TASK001",
        task_type="CREATE_FILE",
        description="Create the main application file.",
        details={"filename": "app/main.py", "initial_code_prompt": "Create a basic Flask app."}
    )
    task2 = DevelopmentTask(
        task_id="TASK002",
        task_type="INSTALL_DEPENDENCY",
        description="Install Flask.",
        details={"name": "Flask", "version": "2.0.1"},
        dependencies=["TASK001"] # Example dependency
    )

    example_manifest = ProjectManifest(
        project_name="My Web App",
        sanitized_project_name="my_web_app",
        project_directory="ai_generated_projects/my_web_app",
        project_description="A simple web application.",
        creation_timestamp="2024-05-17T10:00:00Z",
        last_modified_timestamp="2024-05-17T10:00:00Z",
        project_type="python",
        entry_points={"web": "gunicorn app.main:app"},
        dependencies=[dep1],
        build_config=build_conf,
        test_config=test_conf,
        project_goals=["Provide a user-friendly interface.", "Ensure data privacy."],
        development_tasks=[task1, task2],
        project_notes="This is an example manifest."
    )

    # Convert to dictionary for JSON serialization
    manifest_dict_for_json = example_manifest.to_json_dict()
    print("--- Serialized Manifest (for JSON) ---")
    print(json.dumps(manifest_dict_for_json, indent=4))

    # Example of reconstructing from a dictionary (e.g., after json.load)
    # This assumes manifest_dict_for_json is what you'd get from json.loads(file_content)
    try:
        reloaded_manifest = ProjectManifest.from_dict(manifest_dict_for_json)
        print("\n--- Reloaded Manifest ---")
        print(f"Project Name: {reloaded_manifest.project_name}")
        print(f"Manifest Version: {reloaded_manifest.manifest_version}")
        if reloaded_manifest.dependencies:
            print(f"First Dependency Name: {reloaded_manifest.dependencies[0].name}")
        if reloaded_manifest.build_config:
            print(f"Build Command: {reloaded_manifest.build_config.build_command}")
        if reloaded_manifest.development_tasks:
            print(f"First Dev Task ID: {reloaded_manifest.development_tasks[0].task_id}")
    except Exception as e:
        print(f"Error during reconstruction: {e}")
