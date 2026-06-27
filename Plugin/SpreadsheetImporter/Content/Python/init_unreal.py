"""
SpreadsheetImporter Plugin - Python initialization

This file is automatically executed when Unreal loads Python content from this plugin.
It registers the TSV import toolset with the MCP toolset registry.
"""

import unreal

def init():
    """Initialize the SpreadsheetImporter Python components."""
    try:
        # Import and register the toolset
        from . import tsv_import_toolset
        unreal.log("SpreadsheetImporter: Python toolset registered")
    except Exception as e:
        unreal.log_warning(f"SpreadsheetImporter: Failed to register toolset: {e}")

# Run initialization
init()
