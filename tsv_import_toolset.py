"""
TSV Import Toolset for MCP

This toolset exposes the spreadsheet import functionality to Claude via MCP.
Place this file in your Unreal project's Python directory and it will be
auto-discovered by the ToolsetRegistry.

Usage via MCP:
    1. Write TSV data to /Saved/TSVImport/<filename>.tsv
    2. Call import_tsv_file(file_path, dt_path)

    OR use Google Sheets integration:
    1. Call list_sheet_tabs() to get available sheets
    2. Call fetch_sheet_tsv(sheet_name) to get TSV data
    3. Call import_tsv_string(tsv_content, dt_path) to import

Version: 1.1 - Added reload_toolset for hot-reloading
Version: 1.3 - Added Google Sheets API integration
"""
from __future__ import annotations

TOOLSET_VERSION = "1.7"  # Bidirectional column validation (TSV↔DataTable)

from typing import List

import unreal
import toolset_registry
import io
import csv
import re
import json
import urllib.request
import urllib.parse
import ssl
import time
import os
import shutil


# ============================================================================
# Auto-install Forge Panel
# ============================================================================

def _install_forge_panel():
    """Copy Forge panel files from plugin Content to Saved/Forge/tools on startup."""
    try:
        # Source: Plugin's Content/Forge/Spreadsheet_Importer/
        plugin_content = os.path.dirname(os.path.abspath(__file__))
        source_dir = os.path.join(plugin_content, "Forge", "Spreadsheet_Importer")

        # Destination: Project's Saved/Forge/tools/Spreadsheet_Importer/
        project_dir = str(unreal.Paths.project_dir())
        dest_dir = os.path.join(project_dir, "Saved", "Forge", "tools", "Spreadsheet_Importer")

        if not os.path.exists(source_dir):
            return  # Source doesn't exist, skip silently

        # Create destination directory
        os.makedirs(dest_dir, exist_ok=True)

        # Files to copy
        files = ["tool.js", "tool.css", "tool.json"]
        installed = []

        for fname in files:
            src = os.path.join(source_dir, fname)
            dst = os.path.join(dest_dir, fname)

            if not os.path.exists(src):
                continue

            # Only copy if source is newer or dest doesn't exist
            if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
                shutil.copy2(src, dst)
                installed.append(fname)

        if installed:
            unreal.log(f"SpreadsheetImporter: Installed Forge panel ({', '.join(installed)})")
    except Exception as ex:
        unreal.log_warning(f"SpreadsheetImporter: Failed to install Forge panel: {ex}")

# Run auto-install on module load
_install_forge_panel()


# ============================================================================
# Google Sheets API Configuration (LEGACY - use Hodor MCP instead)
# ============================================================================
# These settings are only used for direct Google API access, which is deprecated.
# The Forge panel now uses Hodor MCP for Google Sheets access.
# To use direct API access, set these environment variables:
#   SPREADSHEET_IMPORTER_SHEETS_API - Apps Script deployment URL
#   SPREADSHEET_IMPORTER_TOKEN_PATH - Path to oauth-tokens.json
#   SPREADSHEET_IMPORTER_CREDENTIALS_PATH - Path to client_secret.json

SHEETS_API_BASE = os.environ.get("SPREADSHEET_IMPORTER_SHEETS_API", "")
TOKEN_PATH = os.environ.get("SPREADSHEET_IMPORTER_TOKEN_PATH", "")
CREDENTIALS_PATH = os.environ.get("SPREADSHEET_IMPORTER_CREDENTIALS_PATH", "")


def _load_oauth_tokens():
    """Load OAuth tokens from the token file."""
    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as ex:
        unreal.log_error(f"Failed to load OAuth tokens: {ex}")
        return None


def _save_oauth_tokens(tokens):
    """Save OAuth tokens back to the token file."""
    try:
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
    except Exception as ex:
        unreal.log_error(f"Failed to save OAuth tokens: {ex}")


def _refresh_oauth_token(tokens):
    """Refresh the OAuth access token using the refresh token."""
    try:
        # Load credentials
        with open(CREDENTIALS_PATH, "r", encoding="utf-8") as f:
            creds = json.load(f)

        client = creds.get("installed") or creds.get("web")
        if not client:
            return False

        # Prepare refresh request
        post_data = urllib.parse.urlencode({
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "refresh_token": tokens["refresh_token"],
            "grant_type": "refresh_token"
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=post_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))

        if "access_token" in result:
            tokens["access_token"] = result["access_token"]
            tokens["expiry_date"] = int(time.time() * 1000) + (result.get("expires_in", 3600) * 1000)
            _save_oauth_tokens(tokens)
            unreal.log("OAuth token refreshed successfully")
            return True

        return False
    except Exception as ex:
        unreal.log_error(f"Failed to refresh OAuth token: {ex}")
        return False


def _get_access_token():
    """Get a valid OAuth access token, refreshing if expired."""
    tokens = _load_oauth_tokens()
    if not tokens:
        raise Exception("OAuth tokens not found. Check TOKEN_PATH configuration.")

    # Check if token is expired (with 5 min buffer)
    expiry = tokens.get("expiry_date", 0)
    now_ms = int(time.time() * 1000)
    if now_ms > expiry - 300000:
        unreal.log("OAuth token expired or expiring soon, refreshing...")
        if not _refresh_oauth_token(tokens):
            raise Exception("Failed to refresh OAuth token")
        tokens = _load_oauth_tokens()
        if not tokens:
            raise Exception("Failed to reload tokens after refresh")

    return tokens["access_token"]


def _sheets_api_request(endpoint, params=None, max_redirects=5):
    """Make a request to the Google Apps Script API with redirect handling."""
    token = _get_access_token()

    # Build URL with parameters
    query_params = {"path": endpoint}
    if params:
        query_params.update(params)

    url = f"{SHEETS_API_BASE}?{urllib.parse.urlencode(query_params)}"

    ctx = ssl.create_default_context()

    for redirect_count in range(max_redirects + 1):
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
        )

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=60) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                url = e.headers.get("Location")
                if not url:
                    raise Exception(f"Redirect without Location header")
                continue
            raise

    raise Exception(f"Too many redirects")


def _google_api_request(url, max_redirects=5):
    """Make a request to Google APIs with OAuth token and redirect handling."""
    token = _get_access_token()
    ctx = ssl.create_default_context()

    for redirect_count in range(max_redirects + 1):
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json"
            }
        )

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=60) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                url = e.headers.get("Location")
                if not url:
                    raise Exception("Redirect without Location header")
                continue
            # Read error body for details
            error_body = e.read().decode("utf-8") if e.fp else str(e)
            raise Exception(f"HTTP {e.code}: {error_body[:500]}")

    raise Exception("Too many redirects")


def _list_drive_spreadsheets(max_results=50):
    """List Google Spreadsheets from Drive using the Drive API."""
    query = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
    params = urllib.parse.urlencode({
        "q": query,
        "pageSize": max_results,
        "fields": "files(id,name,modifiedTime)",
        "orderBy": "modifiedTime desc"
    })
    url = f"https://www.googleapis.com/drive/v3/files?{params}"
    response = _google_api_request(url)
    return json.loads(response)


def _get_spreadsheet_metadata(spreadsheet_id):
    """Get spreadsheet metadata including sheet names using the Sheets API."""
    params = urllib.parse.urlencode({
        "fields": "spreadsheetId,properties.title,sheets.properties"
    })
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}?{params}"
    response = _google_api_request(url)
    return json.loads(response)


def _get_sheet_values(spreadsheet_id, range_notation):
    """Get values from a spreadsheet range using the Sheets API."""
    encoded_range = urllib.parse.quote(range_notation)
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded_range}"
    response = _google_api_request(url)
    return json.loads(response)


def _get_cell_value(spreadsheet_id, sheet_name, cell="A1"):
    """Get a single cell value from a sheet."""
    try:
        range_notation = f"'{sheet_name}'!{cell}"
        data = _get_sheet_values(spreadsheet_id, range_notation)
        values = data.get("values", [])
        if values and len(values) > 0 and len(values[0]) > 0:
            return values[0][0]
        return None
    except Exception:
        return None


def _values_to_tsv(values):
    """Convert a 2D array of values to TSV string."""
    if not values:
        return ""
    lines = []
    for row in values:
        # Escape tabs and newlines in cell values
        cells = []
        for cell in row:
            cell_str = str(cell) if cell is not None else ""
            # Replace tabs with spaces and newlines with escaped version
            cell_str = cell_str.replace("\t", " ").replace("\n", "\\n").replace("\r", "")
            cells.append(cell_str)
        lines.append("\t".join(cells))
    return "\n".join(lines)


@unreal.uclass()
class TSVImportToolset(unreal.ToolsetDefinition):
    """Import TSV data into DataTables and patch ItemDefinition assets.

    Workflow:
    1. Write TSV content to a file in /Saved/ using AssetTools.write_file
    2. Call import_tsv_file with the file path and target DataTable path

    The TSV format supports:
    - Column prefix '#' to skip columns from DataTable import
    - Column prefix '%' for JSON patch columns targeting ItemDefinition assets
    - First column is the row key
    """

    @toolset_registry.tool_call
    @staticmethod
    def reload_toolset() -> str:
        """Reload this toolset module to pick up code changes.

        Call this after making changes to the toolset source file.
        This uses importlib to reload the module and re-register it with
        the toolset registry.

        Returns:
            JSON string with reload status and any errors.
        """
        import importlib
        import sys
        result = {"success": False, "message": "", "errors": []}
        try:
            module_name = "tsv_import_toolset"
            if module_name in sys.modules:
                old_module = sys.modules[module_name]
                # Reload the module
                reloaded = importlib.reload(old_module)
                # Re-register with toolset registry
                toolset_registry.reload_module(reloaded)
                result["success"] = True
                result["message"] = "Toolset reloaded successfully"
                unreal.log("TSVImportToolset: Module reloaded successfully")
            else:
                result["errors"].append("Module not found in sys.modules")
        except Exception as ex:
            result["errors"].append("Reload failed: " + str(ex))
            unreal.log_error("TSVImportToolset reload failed: " + str(ex))
        return json.dumps(result)

    @toolset_registry.tool_call
    @staticmethod
    def import_tsv_file(file_path: str, dt_path: str) -> str:
        """Import a TSV file into a DataTable and apply ItemDefinition patches.

        Args:
            file_path: Path to the TSV file. Use Unreal virtual paths like
                       '/Saved/TSVImport/data.tsv' or absolute disk paths.
            dt_path: The asset path of the target DataTable.

        Returns:
            JSON string with keys:
            - success: bool
            - rows_imported: int
            - assets_modified: list of asset paths
            - assets_created: list of newly created asset paths
            - errors: list of error messages
            - skipped_columns: list of column names that were skipped
        """
        result = {
            "success": False,
            "rows_imported": 0,
            "assets_modified": [],
            "assets_created": [],
            "errors": [],
            "skipped_columns": []
        }

        try:
            # Read the TSV file
            tsv_content = _read_file(file_path)
            if tsv_content is None:
                result["errors"].append("Could not read file: " + str(file_path))
                return json.dumps(result)

            # Get DataTable schema for case-insensitive column matching
            dt_schema = _get_datatable_schema(dt_path)

            # Parse TSV with schema for column name mapping
            csv_text, skipped_columns, patch_rows = _parse_tsv(tsv_content, dt_schema)
            result["skipped_columns"] = skipped_columns

            # Capture column headers for error context
            csv_lines = csv_text.split('\n')
            csv_headers = csv_lines[0].split(',') if csv_lines else []

            # Import into DataTable (with pre-validation to avoid Unreal's modal)
            dt_asset, import_success, validation_err = _import_into_datatable(dt_path, csv_text, dt_schema)
            if not import_success:
                if validation_err:
                    result["errors"].append(validation_err)
                else:
                    result["errors"].append(_build_import_error(csv_headers, dt_schema))
                return json.dumps(result)

            # Save DataTable
            try:
                unreal.EditorAssetLibrary.save_asset(dt_asset.get_path_name())
            except Exception:
                pass

            # Close and reopen the DataTable editor to refresh UI
            try:
                aes = unreal.get_editor_subsystem(unreal.AssetEditorSubsystem)
                if aes:
                    # Close existing editor first to force refresh
                    try:
                        aes.close_all_editors_for_asset(dt_asset)
                    except Exception:
                        pass
                    # Open fresh editor
                    aes.open_editor_for_assets([dt_asset])
            except Exception as ex:
                unreal.log_warning("Could not refresh DataTable editor: " + str(ex))

            # Find and optionally create missing assets
            missing_assets = _find_missing_assets(patch_rows)
            if missing_assets:
                created, creation_errors = _create_missing_assets(missing_assets)
                result["assets_created"] = created
                result["errors"].extend(creation_errors)
                if created:
                    _save_assets(created)

            # Apply ItemDefinition patches
            patch_errors, modified_assets = _apply_itemdefinition_patches(patch_rows)
            result["errors"].extend(patch_errors)
            result["assets_modified"] = modified_assets

            if modified_assets:
                _save_assets(modified_assets)

            # Get row count
            try:
                row_names = unreal.DataTableFunctionLibrary.get_data_table_row_names(dt_asset)
                result["rows_imported"] = len(row_names)
            except Exception:
                result["rows_imported"] = 0

            result["success"] = True

        except Exception as ex:
            result["errors"].append("Import failed: " + str(ex))

        return json.dumps(result)

    @toolset_registry.tool_call
    @staticmethod
    def import_tsv_string(tsv_content: str, dt_path: str) -> str:
        """Import TSV content directly (as a string) into a DataTable.

        Use this when you have the TSV content in memory rather than a file.
        For large TSV data, prefer import_tsv_file to avoid MCP message size limits.

        Args:
            tsv_content: The TSV data as a string.
            dt_path: The asset path of the target DataTable.

        Returns:
            JSON string (same format as import_tsv_file).
        """
        result = {
            "success": False,
            "rows_imported": 0,
            "datatable_used": "",
            "assets_modified": [],
            "assets_created": [],
            "errors": [],
            "skipped_columns": []
        }

        try:
            # Resolve DataTable path if just a name was provided (e.g. "DT_MyTable")
            original_dt_path = dt_path
            if dt_path and "/" not in dt_path and "." not in dt_path:
                # Search for matching DataTable by name
                ar = unreal.AssetRegistryHelpers.get_asset_registry()
                all_dts = ar.get_assets_by_class(unreal.TopLevelAssetPath("/Script/Engine", "DataTable"))

                cell_clean = dt_path.strip()
                resolved = None

                # Try exact match first (case-sensitive)
                for dt in all_dts:
                    if str(dt.asset_name) == cell_clean:
                        resolved = str(dt.package_name) + "." + str(dt.asset_name)
                        break

                # Try case-insensitive exact match only (no substring matching)
                if not resolved:
                    cell_lower = cell_clean.lower()
                    for dt in all_dts:
                        asset_name = str(dt.asset_name).lower()
                        if cell_lower == asset_name:
                            resolved = str(dt.package_name) + "." + str(dt.asset_name)
                            break

                if resolved:
                    dt_path = resolved
                    unreal.log(f"import_tsv_string: Resolved '{original_dt_path}' -> '{dt_path}'")
                else:
                    result["errors"].append(f"Could not find DataTable matching '{original_dt_path}'")
                    return json.dumps(result)

            result["datatable_used"] = dt_path

            # Get DataTable schema for case-insensitive column matching
            dt_schema = _get_datatable_schema(dt_path)

            # Parse TSV with schema for column name mapping
            csv_text, skipped_columns, patch_rows = _parse_tsv(tsv_content, dt_schema)
            result["skipped_columns"] = skipped_columns

            # Capture column headers for error context
            csv_lines = csv_text.split('\n')
            csv_headers = csv_lines[0].split(',') if csv_lines else []

            # Import into DataTable (with pre-validation to avoid Unreal's modal)
            dt_asset, import_success, validation_err = _import_into_datatable(dt_path, csv_text, dt_schema)
            if not import_success:
                if validation_err:
                    result["errors"].append(validation_err)
                else:
                    result["errors"].append(_build_import_error(csv_headers, dt_schema))
                return json.dumps(result)

            # Save DataTable
            try:
                unreal.EditorAssetLibrary.save_asset(dt_asset.get_path_name())
            except Exception:
                pass

            # Close and reopen the DataTable editor to refresh UI
            try:
                aes = unreal.get_editor_subsystem(unreal.AssetEditorSubsystem)
                if aes:
                    # Close existing editor first to force refresh
                    try:
                        aes.close_all_editors_for_asset(dt_asset)
                    except Exception:
                        pass
                    # Open fresh editor
                    aes.open_editor_for_assets([dt_asset])
            except Exception as ex:
                unreal.log_warning("Could not refresh DataTable editor: " + str(ex))

            # Find and create missing assets
            missing_assets = _find_missing_assets(patch_rows)
            if missing_assets:
                created, creation_errors = _create_missing_assets(missing_assets)
                result["assets_created"] = created
                result["errors"].extend(creation_errors)
                if created:
                    _save_assets(created)

            # Apply ItemDefinition patches
            patch_errors, modified_assets = _apply_itemdefinition_patches(patch_rows)
            result["errors"].extend(patch_errors)
            result["assets_modified"] = modified_assets

            if modified_assets:
                _save_assets(modified_assets)

            # Get row count
            try:
                row_names = unreal.DataTableFunctionLibrary.get_data_table_row_names(dt_asset)
                result["rows_imported"] = len(row_names)
            except Exception:
                result["rows_imported"] = 0

            result["success"] = True

        except Exception as ex:
            result["errors"].append("Import failed: " + str(ex))

        return json.dumps(result)

    @toolset_registry.tool_call
    @staticmethod
    def list_available_datatables(folder_path: str = "/Game/") -> str:
        """List DataTable assets in a folder.

        Args:
            folder_path: The content folder to search (default: /Game/)

        Returns:
            JSON array of DataTable asset paths.
        """
        ar = unreal.AssetRegistryHelpers.get_asset_registry()

        # Get all DataTable assets
        all_assets = ar.get_assets_by_class(unreal.TopLevelAssetPath("/Script/Engine", "DataTable"))

        # Filter by folder path
        results = []
        for a in all_assets:
            package_name = str(a.package_name)
            if package_name.startswith(folder_path):
                results.append(package_name + "." + str(a.asset_name))

        return json.dumps(results)

    # ========================================================================
    # Google Sheets Integration Methods
    # ========================================================================

    @toolset_registry.tool_call
    @staticmethod
    def list_sheet_tabs() -> str:
        """List all available sheet tabs from the master Husky spreadsheet.

        This calls the Google Apps Script API to get the list of tabs (sheets)
        available in the connected spreadsheet.

        Returns:
            JSON object with:
            - success: bool
            - tabs: list of tab names
            - error: error message if failed
        """
        result = {"success": False, "tabs": [], "error": ""}
        try:
            response = _sheets_api_request("/tabs")
            data = json.loads(response)

            if "error" in data:
                result["error"] = data["error"]
                return json.dumps(result)

            result["tabs"] = data.get("tabs", [])
            result["success"] = True
            unreal.log(f"Loaded {len(result['tabs'])} sheet tabs")

        except Exception as ex:
            result["error"] = str(ex)
            unreal.log_error(f"Failed to list sheet tabs: {ex}")

        return json.dumps(result)

    @toolset_registry.tool_call
    @staticmethod
    def fetch_sheet_tsv(sheet_name: str) -> str:
        """Fetch a sheet's data as TSV from the master Husky spreadsheet.

        This calls the Google Apps Script API to dump the sheet contents
        as tab-separated values (TSV).

        Args:
            sheet_name: The name of the sheet/tab to fetch (e.g., "Husky_Currencies")

        Returns:
            JSON object with:
            - success: bool
            - tsv: the TSV content string
            - row_count: number of data rows (excluding header)
            - error: error message if failed
        """
        result = {"success": False, "tsv": "", "row_count": 0, "error": ""}
        try:
            response = _sheets_api_request("/dump", {"sheet": sheet_name})

            # Check for error response
            if response.startswith("Error:"):
                result["error"] = response
                return json.dumps(result)

            # Successful response is raw TSV
            result["tsv"] = response
            # Count rows (subtract 1 for header)
            lines = response.strip().split("\n")
            result["row_count"] = max(0, len(lines) - 1)
            result["success"] = True
            unreal.log(f"Fetched {result['row_count']} rows from sheet '{sheet_name}'")

        except Exception as ex:
            result["error"] = str(ex)
            unreal.log_error(f"Failed to fetch sheet '{sheet_name}': {ex}")

        return json.dumps(result)

    @toolset_registry.tool_call
    @staticmethod
    def import_sheet_to_datatable(sheet_name: str, dt_path: str) -> str:
        """Fetch a Google Sheet and import it directly into a DataTable.

        This is a convenience method that combines fetch_sheet_tsv and
        import_tsv_string into a single call.

        Args:
            sheet_name: The name of the sheet/tab to import (e.g., "Husky_Currencies")
            dt_path: The asset path of the target DataTable

        Returns:
            JSON string with keys:
            - success: bool
            - rows_imported: int
            - assets_modified: list of asset paths
            - assets_created: list of newly created asset paths
            - errors: list of error messages
            - skipped_columns: list of column names that were skipped
        """
        result = {
            "success": False,
            "rows_imported": 0,
            "assets_modified": [],
            "assets_created": [],
            "errors": [],
            "skipped_columns": []
        }

        try:
            # Step 1: Fetch the sheet TSV
            unreal.log(f"Fetching sheet '{sheet_name}'...")
            response = _sheets_api_request("/dump", {"sheet": sheet_name})

            if response.startswith("Error:"):
                result["errors"].append(f"Failed to fetch sheet: {response}")
                return json.dumps(result)

            tsv_content = response
            lines = tsv_content.strip().split("\n")
            unreal.log(f"Fetched {len(lines) - 1} rows from Google Sheets")

            # Step 2: Import the TSV content
            # Get DataTable schema for case-insensitive column matching
            dt_schema = _get_datatable_schema(dt_path)

            # Parse TSV with schema for column name mapping
            csv_text, skipped_columns, patch_rows = _parse_tsv(tsv_content, dt_schema)
            result["skipped_columns"] = skipped_columns

            # Capture column headers for error context
            csv_lines = csv_text.split('\n')
            csv_headers = csv_lines[0].split(',') if csv_lines else []

            # Import into DataTable (with pre-validation to avoid Unreal's modal)
            dt_asset, import_success, validation_err = _import_into_datatable(dt_path, csv_text, dt_schema)
            if not import_success:
                if validation_err:
                    result["errors"].append(validation_err)
                else:
                    result["errors"].append(_build_import_error(csv_headers, dt_schema))
                return json.dumps(result)

            # Save DataTable
            try:
                unreal.EditorAssetLibrary.save_asset(dt_asset.get_path_name())
            except Exception:
                pass

            # Close and reopen the DataTable editor to refresh UI
            try:
                aes = unreal.get_editor_subsystem(unreal.AssetEditorSubsystem)
                if aes:
                    try:
                        aes.close_all_editors_for_asset(dt_asset)
                    except Exception:
                        pass
                    aes.open_editor_for_assets([dt_asset])
            except Exception as ex:
                unreal.log_warning(f"Could not refresh DataTable editor: {ex}")

            # Find and optionally create missing assets
            missing_assets = _find_missing_assets(patch_rows)
            if missing_assets:
                created, creation_errors = _create_missing_assets(missing_assets)
                result["assets_created"] = created
                result["errors"].extend(creation_errors)
                if created:
                    _save_assets(created)

            # Apply ItemDefinition patches
            patch_errors, modified_assets = _apply_itemdefinition_patches(patch_rows)
            result["errors"].extend(patch_errors)
            result["assets_modified"] = modified_assets

            if modified_assets:
                _save_assets(modified_assets)

            # Get row count
            try:
                row_names = unreal.DataTableFunctionLibrary.get_data_table_row_names(dt_asset)
                result["rows_imported"] = len(row_names)
            except Exception:
                result["rows_imported"] = 0

            result["success"] = True

        except Exception as ex:
            result["errors"].append(f"Import failed: {ex}")

        return json.dumps(result)

    # ========================================================================
    # Multi-Spreadsheet Support (Google Drive + Sheets APIs)
    # ========================================================================

    @toolset_registry.tool_call
    @staticmethod
    def list_spreadsheets(name_filter: str = "TSV") -> str:
        """List available Google Spreadsheets from Drive.

        Returns spreadsheets sorted by most recently modified, filtered by name.

        Args:
            name_filter: Only return spreadsheets containing this text in the name.
                         Default is "TSV". Pass empty string for no filter.

        Returns:
            JSON object with:
            - success: bool
            - spreadsheets: list of {id, name, modifiedTime}
            - error: error message if failed
        """
        result = {"success": False, "spreadsheets": [], "error": ""}
        try:
            data = _list_drive_spreadsheets(max_results=100)
            files = data.get("files", [])

            # Filter by name if specified
            if name_filter:
                filter_lower = name_filter.lower()
                files = [f for f in files if filter_lower in f.get("name", "").lower()]

            result["spreadsheets"] = [
                {"id": f["id"], "name": f["name"], "modifiedTime": f.get("modifiedTime", "")}
                for f in files
            ]
            result["success"] = True
            unreal.log(f"Found {len(result['spreadsheets'])} spreadsheets (filter: '{name_filter}')")

        except Exception as ex:
            result["error"] = str(ex)
            unreal.log_error(f"Failed to list spreadsheets: {ex}")

        return json.dumps(result)

    @toolset_registry.tool_call
    @staticmethod
    def get_spreadsheet_tabs(spreadsheet_id: str) -> str:
        """Get the list of tabs (sheets) in a spreadsheet.

        Args:
            spreadsheet_id: The Google Spreadsheet ID

        Returns:
            JSON object with:
            - success: bool
            - spreadsheet_name: name of the spreadsheet
            - tabs: list of tab names
            - error: error message if failed
        """
        result = {"success": False, "spreadsheet_name": "", "tabs": [], "error": ""}
        try:
            data = _get_spreadsheet_metadata(spreadsheet_id)

            result["spreadsheet_name"] = data.get("properties", {}).get("title", "")
            sheets = data.get("sheets", [])
            result["tabs"] = [
                s.get("properties", {}).get("title", f"Sheet{i}")
                for i, s in enumerate(sheets)
            ]
            result["success"] = True
            unreal.log(f"Found {len(result['tabs'])} tabs in '{result['spreadsheet_name']}'")

        except Exception as ex:
            result["error"] = str(ex)
            unreal.log_error(f"Failed to get spreadsheet tabs: {ex}")

        return json.dumps(result)

    @toolset_registry.tool_call
    @staticmethod
    def detect_datatable_for_tab(spreadsheet_id: str, tab_name: str) -> str:
        """Detect the target DataTable for a sheet tab by reading cell A1.

        The top-left cell (A1) should contain the DataTable asset path or name.
        This method will search for a matching DataTable in the project.

        Args:
            spreadsheet_id: The Google Spreadsheet ID
            tab_name: The name of the tab/sheet

        Returns:
            JSON object with:
            - success: bool
            - cell_value: the raw value from A1
            - datatable_path: the matched DataTable path (or empty if not found)
            - error: error message if failed
        """
        result = {"success": False, "cell_value": "", "datatable_path": "", "error": ""}
        try:
            # Get A1 cell value
            cell_value = _get_cell_value(spreadsheet_id, tab_name, "A1")
            result["cell_value"] = cell_value or ""

            if not cell_value:
                result["error"] = "Cell A1 is empty"
                return json.dumps(result)

            # Search for matching DataTable
            ar = unreal.AssetRegistryHelpers.get_asset_registry()
            all_dts = ar.get_assets_by_class(unreal.TopLevelAssetPath("/Script/Engine", "DataTable"))

            # Try exact match first
            cell_clean = cell_value.strip()
            for dt in all_dts:
                dt_path = str(dt.package_name) + "." + str(dt.asset_name)
                if dt_path == cell_clean or str(dt.asset_name) == cell_clean:
                    result["datatable_path"] = dt_path
                    result["success"] = True
                    return json.dumps(result)

            # Try case-insensitive exact match only (no substring matching)
            cell_lower = cell_clean.lower()
            for dt in all_dts:
                asset_name = str(dt.asset_name).lower()
                if cell_lower == asset_name:
                    result["datatable_path"] = str(dt.package_name) + "." + str(dt.asset_name)
                    result["success"] = True
                    return json.dumps(result)

            result["error"] = f"No DataTable found matching '{cell_value}'"

        except Exception as ex:
            result["error"] = str(ex)
            unreal.log_error(f"Failed to detect DataTable: {ex}")

        return json.dumps(result)

    @toolset_registry.tool_call
    @staticmethod
    def import_tab_to_datatable(spreadsheet_id: str, tab_name: str, dt_path: str = "") -> str:
        """Import a single tab from any spreadsheet into a DataTable.

        If dt_path is not provided, attempts to auto-detect by reading cell A1.
        The actual data import starts from row 2 (row 1 is headers, A1 may contain
        the DataTable reference).

        Args:
            spreadsheet_id: The Google Spreadsheet ID
            tab_name: The name of the tab/sheet to import
            dt_path: Optional DataTable path. If empty, auto-detects from A1.

        Returns:
            JSON string with import results.
        """
        result = {
            "success": False,
            "rows_imported": 0,
            "datatable_used": "",
            "assets_modified": [],
            "assets_created": [],
            "errors": [],
            "skipped_columns": []
        }

        try:
            # Auto-detect DataTable if not provided
            if not dt_path:
                cell_value = _get_cell_value(spreadsheet_id, tab_name, "A1")
                if not cell_value:
                    result["errors"].append("No DataTable path provided and A1 is empty")
                    return json.dumps(result)

                # Search for matching DataTable
                ar = unreal.AssetRegistryHelpers.get_asset_registry()
                all_dts = ar.get_assets_by_class(unreal.TopLevelAssetPath("/Script/Engine", "DataTable"))

                cell_clean = cell_value.strip()
                for dt in all_dts:
                    dt_full = str(dt.package_name) + "." + str(dt.asset_name)
                    if dt_full == cell_clean or str(dt.asset_name) == cell_clean:
                        dt_path = dt_full
                        break

                if not dt_path:
                    # Try case-insensitive exact match only (no substring matching)
                    cell_lower = cell_clean.lower()
                    for dt in all_dts:
                        asset_name = str(dt.asset_name).lower()
                        if cell_lower == asset_name:
                            dt_path = str(dt.package_name) + "." + str(dt.asset_name)
                            break

                if not dt_path:
                    result["errors"].append(f"Could not find DataTable matching '{cell_value}'")
                    return json.dumps(result)

            result["datatable_used"] = dt_path
            unreal.log(f"Importing '{tab_name}' to {dt_path}")

            # Fetch all values from the sheet
            range_notation = f"'{tab_name}'!A:ZZ"
            data = _get_sheet_values(spreadsheet_id, range_notation)
            values = data.get("values", [])

            if len(values) < 2:
                result["errors"].append("Sheet has no data rows (need header + at least 1 data row)")
                return json.dumps(result)

            # Replace A1 cell with "---" - A1 contains the DataTable reference (used above),
            # but Unreal expects "---" as the row key column header.
            if values[0] and len(values[0]) > 0:
                values[0][0] = "---"

            tsv_content = _values_to_tsv(values)
            unreal.log(f"Fetched {len(values) - 1} data rows")

            # Get DataTable schema for case-insensitive column matching
            dt_schema = _get_datatable_schema(dt_path)

            # Parse TSV with schema for column name mapping
            csv_text, skipped_columns, patch_rows = _parse_tsv(tsv_content, dt_schema)
            result["skipped_columns"] = skipped_columns

            # Capture column headers for error context
            csv_lines = csv_text.split('\n')
            csv_headers = csv_lines[0].split(',') if csv_lines else []

            # Import into DataTable (with pre-validation to avoid Unreal's modal)
            dt_asset, import_success, validation_err = _import_into_datatable(dt_path, csv_text, dt_schema)
            if not import_success:
                if validation_err:
                    result["errors"].append(validation_err)
                else:
                    result["errors"].append(_build_import_error(csv_headers, dt_schema))
                return json.dumps(result)

            # Save DataTable
            try:
                unreal.EditorAssetLibrary.save_asset(dt_asset.get_path_name())
            except Exception:
                pass

            # Refresh DataTable editor
            try:
                aes = unreal.get_editor_subsystem(unreal.AssetEditorSubsystem)
                if aes:
                    try:
                        aes.close_all_editors_for_asset(dt_asset)
                    except Exception:
                        pass
                    aes.open_editor_for_assets([dt_asset])
            except Exception:
                pass

            # Handle asset creation and patching
            missing_assets = _find_missing_assets(patch_rows)
            if missing_assets:
                created, creation_errors = _create_missing_assets(missing_assets)
                result["assets_created"] = created
                result["errors"].extend(creation_errors)
                if created:
                    _save_assets(created)

            patch_errors, modified_assets = _apply_itemdefinition_patches(patch_rows)
            result["errors"].extend(patch_errors)
            result["assets_modified"] = modified_assets

            if modified_assets:
                _save_assets(modified_assets)

            # Get row count
            try:
                row_names = unreal.DataTableFunctionLibrary.get_data_table_row_names(dt_asset)
                result["rows_imported"] = len(row_names)
            except Exception:
                result["rows_imported"] = 0

            result["success"] = True

        except Exception as ex:
            result["errors"].append(f"Import failed: {ex}")

        # Add tab context to all errors for clarity
        if tab_name:
            result["errors"] = [f"[{tab_name}] {err}" if not err.startswith(f"[{tab_name}]") else err
                               for err in result["errors"]]

        return json.dumps(result)

    @toolset_registry.tool_call
    @staticmethod
    def batch_import_tabs(import_items: str) -> str:
        """Import multiple tabs from spreadsheets in batch.

        Args:
            import_items: JSON string containing list of items to import.
                Each item should have:
                - spreadsheet_id: The Google Spreadsheet ID
                - tab_name: The name of the tab/sheet
                - dt_path: Optional DataTable path (auto-detects if empty)

        Returns:
            JSON object with:
            - success: bool (true if all imports succeeded)
            - results: list of individual import results
            - total_rows: total rows imported across all tabs
            - total_errors: count of errors across all imports
        """
        result = {
            "success": False,
            "results": [],
            "total_rows": 0,
            "total_errors": 0
        }

        try:
            items = json.loads(import_items) if isinstance(import_items, str) else import_items

            if not isinstance(items, list):
                result["results"].append({"error": "import_items must be a list"})
                return json.dumps(result)

            all_success = True
            for item in items:
                spreadsheet_id = item.get("spreadsheet_id", "")
                tab_name = item.get("tab_name", "")
                dt_path = item.get("dt_path", "")

                if not spreadsheet_id or not tab_name:
                    result["results"].append({
                        "tab_name": tab_name,
                        "success": False,
                        "error": "Missing spreadsheet_id or tab_name"
                    })
                    result["total_errors"] += 1
                    all_success = False
                    continue

                # Call the single import method
                import_result_str = TSVImportToolset.import_tab_to_datatable(
                    spreadsheet_id, tab_name, dt_path
                )
                import_result = json.loads(import_result_str)

                # Prefix errors with tab name for context
                tab_errors = [f"[{tab_name}] {err}" for err in import_result.get("errors", [])]
                result["results"].append({
                    "tab_name": tab_name,
                    "datatable": import_result.get("datatable_used", ""),
                    "success": import_result.get("success", False),
                    "rows_imported": import_result.get("rows_imported", 0),
                    "errors": tab_errors
                })

                result["total_rows"] += import_result.get("rows_imported", 0)
                result["total_errors"] += len(import_result.get("errors", []))

                if not import_result.get("success", False):
                    all_success = False

            result["success"] = all_success

        except Exception as ex:
            result["results"].append({"error": f"Batch import failed: {ex}"})
            result["total_errors"] += 1

        return json.dumps(result)


# ============================================================================
# Helper Functions (from importer_script_python.py)
# ============================================================================

def _build_import_error(csv_headers: list, dt_schema: dict) -> str:
    """Build a detailed error message for DataTable import failures."""
    col_info = ", ".join(f"{i}:{h}" for i, h in enumerate(csv_headers))
    expected_cols = list(dt_schema.keys()) if dt_schema else []
    expected_info = ", ".join(expected_cols[:15])
    if len(expected_cols) > 15:
        expected_info += f"... (+{len(expected_cols)-15} more)"

    lines = [
        "DataTable import failed. Check Output Log for 'Name not found for Column X' errors.",
        f"  TSV columns: [{col_info}]",
        f"  DataTable expects: [{expected_info}]",
        "  Fix: Ensure TSV headers match DataTable property names exactly."
    ]
    return "\n".join(lines)


def _read_file(file_path: str) -> str:
    """Read a file from disk or Unreal virtual path."""
    # Try Unreal virtual path first
    if file_path.startswith("/"):
        try:
            # Convert virtual path to disk path
            disk_path = unreal.Paths.convert_relative_path_to_full(
                unreal.Paths.project_saved_dir() + file_path.replace("/Saved/", "")
            )
            with open(disk_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass

    # Try as absolute disk path
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        pass

    return None


def _maybe_prefix_asset_ref(val):
    """Ensure asset references have proper /Script/ prefix."""
    if not isinstance(val, str):
        return val

    s = val.strip()
    if s == "":
        return val

    if s.startswith("/Script/") and "'" in s:
        q1 = s.find("'")
        q2 = s.rfind("'")
        if q1 != -1 and q2 > q1:
            inner = s[q1 + 1:q2]
            inner_sanitized = re.sub(r"/+", "/", inner)
            if inner_sanitized != inner:
                s = s[: q1 + 1] + inner_sanitized + s[q2:]
        return s

    if s.startswith("/"):
        path = s.strip("'\"")
        path_sanitized = re.sub(r"/+", "/", path)
        path = path_sanitized

        obj = unreal.load_asset(path)
        if obj:
            cls_path = obj.get_class().get_path_name()
            full = cls_path + "'" + obj.get_path_name() + "'"
            return full

        return path

    return val


def _cast(obj, cls):
    """Safely cast an object to a class."""
    try:
        return cls.cast(obj)
    except Exception:
        return None


def _normalize_column_name(name: str) -> str:
    """Normalize column name for matching: lowercase, no spaces.
    E.g., 'Collection Id' -> 'collectionid' to match 'CollectionId'."""
    return name.strip().lower().replace(" ", "")


def _parse_tsv(tsv_string: str, dt_schema: dict = None):
    """Parse TSV into CSV for DataTable and extract patch info.

    If dt_schema is provided (dict of property names), column names will be
    mapped to match the schema using case-insensitive matching.
    """
    normalized = tsv_string.replace("\r\n", "\n").replace("\r", "\n")
    reader = csv.reader(io.StringIO(normalized), delimiter="\t")
    rows = list(reader)

    if not rows:
        raise Exception("No TSV rows provided.")

    headers = [h.replace("\ufeff", "") for h in rows[0]]

    # Build column name mapping for case-insensitive schema matching
    column_name_map = {}  # maps original header index to mapped schema name
    if dt_schema:
        # Build normalized lookup from schema
        schema_normalized = {}
        for prop_name in dt_schema.keys():
            schema_normalized[_normalize_column_name(prop_name)] = prop_name

        unreal.log(f"_parse_tsv: Schema normalized keys: {list(schema_normalized.keys())[:10]}")

        # Map each header to its schema equivalent
        for i, h in enumerate(headers):
            h_stripped = h.strip()
            if h_stripped.startswith("#") or h_stripped.startswith("%"):
                continue
            h_normalized = _normalize_column_name(h_stripped)
            if h_normalized in schema_normalized:
                mapped_name = schema_normalized[h_normalized]
                column_name_map[i] = mapped_name
                if h_stripped != mapped_name:
                    unreal.log(f"_parse_tsv: Mapped column {i} '{h_stripped}' -> '{mapped_name}'")
            else:
                # No mapping found, use original - THIS WILL LIKELY FAIL IN UNREAL
                column_name_map[i] = h_stripped
                unreal.log_warning(f"_parse_tsv: Column {i} '{h_stripped}' (normalized: '{h_normalized}') has NO schema match - will likely fail")

    header_index = {}
    for i, h in enumerate(headers):
        header_index[h.strip()] = i

    patch_columns = {}
    for i, h in enumerate(headers):
        stripped = h.strip()
        if stripped.startswith("%"):
            target = stripped[1:].strip()
            if target in header_index:
                patch_columns[i] = header_index[target]

    keep_idx = []
    for i, h in enumerate(headers):
        stripped = h.lstrip()
        if stripped.startswith("#"):
            continue
        if h.strip().startswith("%"):
            continue
        # Skip empty column headers (trailing empty columns in sheets)
        if not h.strip():
            continue
        keep_idx.append(i)

    if not keep_idx:
        raise Exception("After skipping #, %, and empty columns, no columns remain.")

    skipped = []
    for i in range(len(headers)):
        if i not in keep_idx:
            skipped.append(headers[i])

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")

    # Write header row with mapped names if available
    if column_name_map:
        mapped_headers = [column_name_map.get(i, headers[i].strip()) for i in keep_idx]
        writer.writerow(mapped_headers)
    else:
        writer.writerow([headers[i] for i in keep_idx])

    patch_rows = []

    for r in rows[1:]:
        row_vals = []
        for i in keep_idx:
            if i < len(r):
                row_vals.append(r[i])
            else:
                row_vals.append("")
        row_vals = [_maybe_prefix_asset_ref(v) for v in row_vals]
        writer.writerow(row_vals)

        row_patch_info = []
        for patch_col_index, target_col_index in patch_columns.items():
            patch_value = ""
            asset_value = ""

            if patch_col_index < len(r):
                patch_value = r[patch_col_index]

            if target_col_index < len(r):
                asset_value = r[target_col_index]

            row_patch_info.append((asset_value, patch_value))

        patch_rows.append(row_patch_info)

    return out.getvalue(), skipped, patch_rows


def _get_datatable_schema(dt_path: str) -> dict:
    """Get the schema (property names) from a DataTable using CSV export."""
    dt = unreal.load_asset(dt_path)
    if not dt:
        unreal.log_warning(f"_get_datatable_schema: DataTable not found: {dt_path}")
        return None

    try:
        # Export to CSV to get column names - this works even for empty DataTables
        csv_string = unreal.DataTableFunctionLibrary.export_data_table_to_csv_string(dt)
        if csv_string:
            # Parse just the header row
            lines = csv_string.split('\n')
            if lines:
                header_line = lines[0]
                reader = csv.reader(io.StringIO(header_line))
                headers = next(reader, [])
                schema = {}
                for h in headers:
                    if h:  # Skip empty headers
                        schema[h] = True
                if schema:
                    unreal.log(f"_get_datatable_schema: Found {len(schema)} columns for {dt_path}")
                    return schema

        # If export failed or returned empty, log it
        unreal.log_warning(f"_get_datatable_schema: CSV export returned no headers for {dt_path}")
        return None
    except Exception as ex:
        unreal.log_warning(f"_get_datatable_schema: Failed for {dt_path}: {ex}")
        return None


def _normalize_column_name_for_match(name: str) -> str:
    """Normalize column name for matching: lowercase, no spaces."""
    return name.lower().replace(" ", "").strip()


def _validate_csv_columns(csv_text: str, dt_schema: dict) -> tuple:
    """Pre-validate CSV columns against DataTable schema.

    Returns (is_valid, error_message, mismatched_columns).
    This prevents Unreal's modal error dialog by catching issues early.
    Matching is case-insensitive and ignores spaces (e.g., "Collection Id" matches "CollectionId").
    """
    if not dt_schema:
        # Can't validate without schema - log warning but allow to proceed
        # This may trigger Unreal's modal if there's a mismatch
        unreal.log_warning("No DataTable schema available for pre-validation - column mismatches may show Unreal's modal")
        return True, None, []

    # Parse CSV header
    lines = csv_text.split('\n')
    if not lines:
        return False, "CSV is empty", []

    reader = csv.reader(io.StringIO(lines[0]))
    csv_headers = next(reader, [])

    # Build normalized schema lookup (lowercase, no spaces)
    schema_normalized = {_normalize_column_name_for_match(h): h for h in dt_schema.keys()}

    # Log what we're comparing
    unreal.log(f"_validate_csv_columns: CSV has {len(csv_headers)} columns, schema has {len(schema_normalized)} columns")
    unreal.log(f"_validate_csv_columns: CSV headers: {csv_headers[:10]}...")
    unreal.log(f"_validate_csv_columns: Schema keys (normalized): {list(schema_normalized.keys())[:10]}...")

    # Check for duplicate columns in CSV (after normalization they might collide)
    seen_normalized = {}
    duplicates = []
    for i, header in enumerate(csv_headers):
        header_clean = header.strip()
        if not header_clean:
            continue
        header_normalized = _normalize_column_name_for_match(header_clean)
        if header_normalized in seen_normalized:
            prev_idx, prev_name = seen_normalized[header_normalized]
            duplicates.append(f"Column {i} '{header_clean}' duplicates Column {prev_idx} '{prev_name}'")
        else:
            seen_normalized[header_normalized] = (i, header_clean)

    if duplicates:
        err = f"Duplicate columns detected: {'; '.join(duplicates)}\n  Fix: Remove duplicate column headers from the sheet."
        return False, err, []

    # After _parse_tsv mapping, CSV headers SHOULD exactly match schema names.
    # Check with EXACT matching (not normalized) to catch mapping failures.
    schema_exact = set(dt_schema.keys())
    csv_header_set = set()

    csv_mismatched = []
    for i, header in enumerate(csv_headers):
        header_clean = header.strip()
        if not header_clean:
            continue
        csv_header_set.add(header_clean)
        if header_clean not in schema_exact:
            unreal.log(f"_validate_csv_columns: Column {i} '{header_clean}' NOT in schema (exact match)")
            csv_mismatched.append((i, header_clean))

    # Check each schema column exists in CSV (Unreal requires ALL columns)
    schema_missing = []
    for schema_col in dt_schema.keys():
        if schema_col not in csv_header_set:
            unreal.log(f"_validate_csv_columns: Schema column '{schema_col}' NOT in CSV (exact match)")
            schema_missing.append(schema_col)

    # Build error message if any issues
    errors = []
    if csv_mismatched:
        details = ", ".join(f"Column {i}: '{name}'" for i, name in csv_mismatched)
        errors.append(f"TSV columns not in DataTable: {details}")

    if schema_missing:
        details = ", ".join(schema_missing)
        errors.append(f"DataTable columns missing from TSV: {details}")

    if errors:
        expected = ", ".join(list(dt_schema.keys())[:15])
        if len(dt_schema) > 15:
            expected += f"... (+{len(dt_schema)-15} more)"
        err = " | ".join(errors) + f"\n  DataTable columns: [{expected}]\n  Fix: Ensure TSV has all required columns."
        return False, err, csv_mismatched + [(None, m) for m in schema_missing]

    return True, None, []


def _import_into_datatable(dt_asset_path: str, csv_text: str, dt_schema: dict = None):
    """Import CSV text into a DataTable.

    If dt_schema is provided, pre-validates columns to avoid Unreal's modal error dialog.
    """
    dt = unreal.load_asset(dt_asset_path)
    if not dt:
        raise Exception(f"DataTable not found: {dt_asset_path}")

    # Pre-validate columns if schema available (avoids Unreal's modal error)
    if dt_schema:
        is_valid, err_msg, _ = _validate_csv_columns(csv_text, dt_schema)
        if not is_valid:
            unreal.log_error(f"CSV validation failed: {err_msg}")
            return dt, False, err_msg

    ok = unreal.DataTableFunctionLibrary.fill_data_table_from_csv_string(dt, csv_text)
    return dt, ok, None


def _find_missing_assets(patch_rows: list) -> list:
    """Find assets referenced in patches that don't exist."""
    missing = []
    last_valid_asset_by_column = {}

    for row_index, row_patch_info in enumerate(patch_rows):
        for col_index, (asset_path, patch_text) in enumerate(row_patch_info):
            if not asset_path:
                continue

            asset = unreal.load_asset(asset_path)
            if asset:
                last_valid_asset_by_column[col_index] = asset_path
            else:
                template_path = last_valid_asset_by_column.get(col_index, None)
                missing.append((row_index, asset_path, template_path))

    return missing


def _create_missing_assets(missing_assets: list):
    """Create missing assets by duplicating templates."""
    created = []
    errors = []
    eal = unreal.EditorAssetLibrary

    for row_idx, new_asset_path, template_path in missing_assets:
        if not template_path:
            errors.append(f"Row {row_idx + 1}: Cannot create {new_asset_path} - no template")
            continue

        if not eal.does_asset_exist(template_path):
            errors.append(f"Row {row_idx + 1}: Template not found: {template_path}")
            continue

        if eal.does_asset_exist(new_asset_path):
            continue  # Already exists

        try:
            success = eal.duplicate_asset(template_path, new_asset_path)
            if success:
                created.append(new_asset_path)
                unreal.log(f"Created asset: {new_asset_path}")
            else:
                errors.append(f"Row {row_idx + 1}: Failed to duplicate {template_path}")
        except Exception as ex:
            errors.append(f"Row {row_idx + 1}: Error creating {new_asset_path} - {ex}")

    return created, errors


def _save_assets(asset_paths: list):
    """Save a list of assets."""
    for path in asset_paths:
        try:
            unreal.EditorAssetLibrary.save_asset(path)
        except Exception:
            pass


# ============================================================================
# ItemDefinition Patching (from importer_script_python.py with collect-then-apply)
# ============================================================================

# Using tuples instead of dicts for compatibility with Unreal Format node
BLOCK_NAME_ALIASES = (
    ("MaxStackSize", "/Script/ItemizationCoreRuntime.ItemComponentData_MaxStackSize"),
    ("Max Stack Size", "/Script/ItemizationCoreRuntime.ItemComponentData_MaxStackSize"),
    ("Pickup", "/Script/FortniteGame.FortItemComponentData_Pickup"),
    ("Traits", "/Script/ItemizationCoreRuntime.ItemComponentData_Traits"),
    ("Tags", "/Script/ItemizationCoreRuntime.ItemComponentData_OwnedGameplayTags"),
    ("OwnedGameplayTags", "/Script/ItemizationCoreRuntime.ItemComponentData_OwnedGameplayTags"),
    ("Icon", "/Script/ItemizationCoreRuntime.ItemComponentData_Icon"),
    ("OwnerPose", "/Script/ItemizationCoreRuntime.ItemComponentData_OwnerPose"),
    ("CampStructure", "/Script/HuskyGameplayRuntime.HuskyItemComponentData_CampStructure"),
    ("Rarity", "/Script/FortniteGame.FortItemComponentData_Rarity"),
)

BASE_PROPERTY_ALIASES = (
    ("ItemName", "ItemName"),
    ("Item Name", "ItemName"),
    ("Name", "ItemName"),
    ("ItemDescription", "ItemDescription"),
    ("Item Description", "ItemDescription"),
    ("Description", "ItemDescription"),
    ("ItemShortDescription", "ItemShortDescription"),
    ("Item Short Description", "ItemShortDescription"),
    ("Short Description", "ItemShortDescription"),
    ("ShortDescription", "ItemShortDescription"),
    ("ReleaseVersion", "ReleaseVersion"),
    ("Release Version", "ReleaseVersion"),
    ("FortReleaseVersion", "ReleaseVersion"),
)


def _resolve_base_property(name: str):
    """Check if name is a base property alias."""
    name_lower = name.lower()
    for alias, prop_name in BASE_PROPERTY_ALIASES:
        if alias.lower() == name_lower:
            return prop_name
    return None


def _check_base_property_change(item_def, prop_name, value):
    """Check if a base property needs to change. Returns (needs_change, new_value, current_value)"""
    str_value = str(value) if value is not None else ""
    try:
        current_value = item_def.get_editor_property(prop_name)
        # For FText, str() gives the localized text content directly
        current_str = str(current_value) if current_value else ""
        # Debug logging to understand comparison
        if current_str != str_value:
            unreal.log("DEBUG base prop " + str(prop_name) + ": current=" + repr(current_str)[:80] + " vs new=" + repr(str_value)[:80])
        if current_str == str_value:
            return False, str_value, current_str
        return True, str_value, current_str
    except Exception as ex:
        unreal.log("DEBUG base prop " + str(prop_name) + " read error: " + str(ex))
        return True, str_value, None


def _check_release_version_change(item_def, value):
    """Check if ReleaseVersion needs to change."""
    if isinstance(value, dict):
        version_name = value.get("VersionName", value.get("versionName", ""))
    else:
        version_name = str(value) if value is not None else ""

    property_names_to_try = ("ReleaseVersion", "FortReleaseVersion", "releaseVersion")
    for prop_name in property_names_to_try:
        try:
            release_version = item_def.get_editor_property(prop_name)
            try:
                current_version_name = release_version.get_editor_property("VersionName")
                if str(current_version_name) == version_name:
                    return False, version_name, str(current_version_name), prop_name
                return True, version_name, str(current_version_name), prop_name
            except Exception:
                return True, version_name, None, prop_name
        except Exception:
            continue
    return True, version_name, None, "ReleaseVersion"


def _normalize_asset_path(path_str):
    """Normalize asset paths for comparison."""
    if not isinstance(path_str, str):
        return path_str
    s = path_str.strip()
    if s.startswith("/Script/") and "'" in s:
        q1 = s.find("'")
        q2 = s.rfind("'")
        if q1 != -1 and q2 > q1:
            s = s[q1 + 1:q2]
    s = re.sub(r':(\d)', r'.\1', s)
    return s


def _normalize_struct_text(text):
    """Normalize Unreal struct text for comparison."""
    if not isinstance(text, str):
        return text
    def normalize_prop(match):
        prop_name = match.group(1).lower()
        rest = match.group(2)
        return prop_name + rest
    normalized = re.sub(r'(\w+)(="[^"]*"|=[^,)]+)', normalize_prop, text)
    return normalized


def _compute_new_value(prop_type_label, value, current_value):
    """Compute what the new value should be for a given property type and input value."""
    if prop_type_label == "bool":
        new_val = bool(value)
        return new_val, new_val
    elif prop_type_label == "float":
        new_val = float(value)
        return new_val, new_val
    elif prop_type_label == "scalable_float":
        if isinstance(value, dict):
            new_val = float(value.get("value", 0))
        else:
            new_val = float(value)
        return new_val, new_val
    elif prop_type_label == "gameplay_tag_container":
        if isinstance(value, list):
            new_tag_names = set(value)
        else:
            new_tag_names = set([str(value)]) if value else set()
        return value, new_tag_names
    elif prop_type_label == "generic_text":
        if isinstance(value, dict) and "TagName" in value:
            new_val = '(TagName="' + str(value["TagName"]) + '")'
        elif isinstance(value, dict):
            parts = []
            for k, v in value.items():
                if isinstance(v, str):
                    parts.append(str(k) + '="' + str(v) + '"')
                else:
                    parts.append(str(k) + "=" + str(v))
            new_val = "(" + ",".join(parts) + ")"
        else:
            new_val = str(value)
        return new_val, new_val
    else:
        if isinstance(value, list):
            new_val = json.dumps(value)
        else:
            new_val = str(value)
        return new_val, new_val


def _get_comparable_current(prop_type_label, current_value):
    """Get a comparable representation of the current value."""
    if prop_type_label == "scalable_float":
        try:
            return current_value.value
        except Exception:
            return None
    elif prop_type_label == "gameplay_tag_container":
        current_tag_names = set()
        try:
            for t in current_value.gameplay_tags:
                try:
                    tn = t.get_editor_property("TagName")
                    if tn is not None:
                        tag_str = str(tn)
                        if tag_str and not tag_str.startswith("<"):
                            current_tag_names.add(tag_str)
                except Exception:
                    pass
        except Exception:
            pass
        return current_tag_names
    else:
        return current_value


def _needs_change(prop_type_label, current_value, value):
    """Determine if a property needs to be changed. Returns (needs_change, new_value, error_message)"""
    try:
        new_val, comparable_new = _compute_new_value(prop_type_label, value, current_value)
        comparable_current = _get_comparable_current(prop_type_label, current_value)

        if prop_type_label == "generic_text":
            comparable_new = _normalize_asset_path(comparable_new)
            comparable_current = _normalize_asset_path(comparable_current)
            comparable_new = _normalize_struct_text(comparable_new)
            comparable_current = _normalize_struct_text(comparable_current)

        if prop_type_label == "gameplay_tag_container":
            if isinstance(comparable_new, set):
                comparable_new = set(str(t).strip() for t in comparable_new)
            if isinstance(comparable_current, set):
                comparable_current = set(str(t).strip() for t in comparable_current)

        if comparable_current == comparable_new:
            return False, None, None
        # Debug logging for component block changes
        unreal.log("DEBUG compare " + str(prop_type_label) + ": current=" + repr(comparable_current)[:100] + " vs new=" + repr(comparable_new)[:100])
        return True, new_val, None
    except Exception as ex:
        return False, None, str(ex)


def _resolve_block_name(name: str, available_types: list) -> str:
    """Resolve friendly block name to full script path."""
    if name in available_types:
        return name

    name_lower = name.lower()
    for alias, full_path in BLOCK_NAME_ALIASES:
        if alias.lower() == name_lower:
            if full_path in available_types:
                return full_path

    for avail in available_types:
        if avail.endswith("_" + name) or avail.endswith("." + name):
            return avail

    return name


def _probe_property(ides, item_def, block_name: str, prop_name: str):
    """Probe property type using ItemDefinitionEditorSubsystem."""
    # ScalableFloat
    try:
        v = ides.get_property_value_scalable_float(item_def, block_name, prop_name)
        if v is not None:
            return "scalable_float", v
    except Exception:
        pass

    # Float
    try:
        v = ides.get_property_value_float(item_def, block_name, prop_name)
        if v is not None:
            return "float", v
    except Exception:
        pass

    # GameplayTagContainer
    try:
        v = ides.get_property_value_gameplay_tag_container(item_def, block_name, prop_name)
        if v is not None and hasattr(v, "gameplay_tags"):
            return "gameplay_tag_container", v
    except Exception:
        pass

    # Generic text
    try:
        v = ides.get_property_value_generic_text(item_def, block_name, prop_name)
        if v is not None:
            return "generic_text", v
    except Exception:
        pass

    # Bool
    try:
        v = ides.get_property_value_bool(item_def, block_name, prop_name)
        if v is not None:
            return "bool", v
    except Exception:
        pass

    return "unknown", None


def _apply_property_change(ides, item_def, block_name, prop_name, prop_type, new_val, current_val, original_val):
    """Apply a single property change."""
    try:
        if prop_type == "bool":
            ides.set_property_value_bool(item_def, block_name, prop_name, bool(new_val))
        elif prop_type == "float":
            ides.set_property_value_float(item_def, block_name, prop_name, float(new_val))
        elif prop_type == "scalable_float":
            current_val.value = float(new_val)
            ides.set_property_value_scalable_float(item_def, block_name, prop_name, current_val)
        elif prop_type == "gameplay_tag_container":
            container = current_val
            while len(container.gameplay_tags) > 0:
                container.gameplay_tags.pop()

            tag_names = original_val if isinstance(original_val, list) else [str(original_val)] if original_val else []
            for tag_name in tag_names:
                tag = ides.find_existing_tag_by_name(tag_name)
                if tag:
                    container.gameplay_tags.append(tag)

            ides.set_property_value_gameplay_tag_container(item_def, block_name, prop_name, container)
        else:
            # generic_text or unknown
            if isinstance(new_val, dict) and "TagName" in new_val:
                text_val = f'(TagName="{new_val["TagName"]}")'
            elif isinstance(new_val, dict):
                parts = [f'{k}="{v}"' if isinstance(v, str) else f'{k}={v}' for k, v in new_val.items()]
                text_val = "(" + ",".join(parts) + ")"
            else:
                text_val = str(new_val)
            ides.set_property_value_generic_text(item_def, block_name, prop_name, text_val)

        return True, None
    except Exception as ex:
        return False, str(ex)


def _apply_base_properties(item_def, patch_data, row_index, errors, modified_assets):
    """Apply base properties using collect-then-apply pattern.
    Returns the patch_data dict with base property keys removed."""
    remaining_patch_data = {}
    asset_path = item_def.get_path_name()

    # Phase 1: Collect changes needed (read-only)
    changes_needed = []  # List of (prop_name, new_value, is_release_version, original_key)

    for key, value in patch_data.items():
        prop_name = _resolve_base_property(key)
        if prop_name is not None:
            if prop_name == "ReleaseVersion":
                needs_change, version_name, current, prop_to_use = _check_release_version_change(item_def, value)
                if needs_change:
                    changes_needed.append((prop_to_use, value, True, key))
            else:
                needs_change, new_val, current = _check_base_property_change(item_def, prop_name, value)
                if needs_change:
                    changes_needed.append((prop_name, new_val, False, key))
        else:
            remaining_patch_data[key] = value

    # Phase 2: Apply changes only if there are any
    if not changes_needed:
        return remaining_patch_data

    for prop_name, new_value, is_release_version, original_key in changes_needed:
        try:
            if is_release_version:
                # Handle ReleaseVersion specially
                if isinstance(new_value, dict):
                    version_name = new_value.get("VersionName", new_value.get("versionName", ""))
                else:
                    version_name = str(new_value) if new_value is not None else ""

                for pn in ("ReleaseVersion", "FortReleaseVersion", "releaseVersion"):
                    try:
                        release_version = item_def.get_editor_property(pn)
                        release_version.set_editor_property("VersionName", version_name)
                        item_def.set_editor_property(pn, release_version)
                        modified_assets.add(asset_path)
                        unreal.log("Row " + str(row_index + 1) + ": Set " + pn + ".VersionName = " + str(version_name))
                        break
                    except Exception:
                        continue
            else:
                item_def.set_editor_property(prop_name, new_value)
                modified_assets.add(asset_path)
                unreal.log("Row " + str(row_index + 1) + ": Set " + str(prop_name) + " = " + str(new_value)[:50])
        except Exception as ex:
            errors.append("Row " + str(row_index + 1) + ": Failed setting base property " + str(original_key) + " - " + str(ex))

    return remaining_patch_data


def _apply_itemdefinition_patches(patch_rows: list):
    """Apply JSON patches to ItemDefinition assets using collect-then-apply pattern.

    This avoids unnecessary source control checkouts by:
    1. First probing all properties to detect which ones need changes (read-only)
    2. Only if changes are needed, calling add_component_data_entry and applying changes
    """
    ides = unreal.get_editor_subsystem(unreal.ItemDefinitionEditorSubsystem)
    if ides is None:
        return ["ItemDefinitionEditorSubsystem not found."], []

    errors = []
    modified_assets = set()

    for row_index, row_patch_info in enumerate(patch_rows):
        for asset_path, patch_text in row_patch_info:
            if not patch_text:
                continue

            if not asset_path:
                errors.append("Row " + str(row_index + 1) + ": Patch JSON provided but target asset column empty.")
                continue

            try:
                patch_data = json.loads(patch_text)
            except Exception as ex:
                errors.append("Row " + str(row_index + 1) + ": Invalid JSON - " + str(ex))
                continue

            asset = unreal.load_asset(asset_path)
            if not asset:
                errors.append("Row " + str(row_index + 1) + ": Could not load asset " + str(asset_path))
                continue

            item_def = _cast(asset, unreal.ItemDefinitionBase)
            if item_def is None:
                errors.append("Row " + str(row_index + 1) + ": Asset is not an ItemDefinitionBase " + str(asset_path))
                continue

            # Apply base properties first (uses collect-then-apply internally)
            patch_data = _apply_base_properties(item_def, patch_data, row_index, errors, modified_assets)

            # If no remaining patch data, skip component block processing
            if not patch_data:
                continue

            # Get component types
            component_types_raw = list(ides.get_all_component_data_type(item_def))
            component_types = [c.get_path_name() for c in component_types_raw]

            # ============================================================
            # PHASE 1: Collect all changes needed (read-only probing)
            # ============================================================
            changes_by_block = {}  # dict of block_name -> list of (prop_name, prop_type, new_val, current_val, original_value)

            for block_name_input, properties in patch_data.items():
                block_name = _resolve_block_name(block_name_input, component_types)

                if block_name not in component_types:
                    errors.append("Row " + str(row_index + 1) + ": Block name not found: " + str(block_name_input))
                    continue

                if not isinstance(properties, dict):
                    errors.append("Row " + str(row_index + 1) + ": Block " + str(block_name) + " must contain property dictionary.")
                    continue

                # Probe each property to check if it needs changing
                for prop_name, value in properties.items():
                    try:
                        # Probe actual property type (read-only)
                        prop_type_label, current_value = _probe_property(ides, item_def, block_name, prop_name)

                        # Check if change is needed
                        needs_change, new_val, error_msg = _needs_change(prop_type_label, current_value, value)

                        if error_msg:
                            errors.append("Row " + str(row_index + 1) + ": Error checking " + str(block_name) + "." + str(prop_name) + " - " + str(error_msg))
                            continue

                        if needs_change:
                            if block_name not in changes_by_block:
                                changes_by_block[block_name] = []
                            changes_by_block[block_name].append((prop_name, prop_type_label, new_val, current_value, value))

                    except Exception as ex:
                        errors.append("Row " + str(row_index + 1) + ": Error probing " + str(block_name) + "." + str(prop_name) + " - " + str(ex))

            # ============================================================
            # PHASE 2: Apply changes only if there are any
            # ============================================================
            if not changes_by_block:
                # No component changes needed for this asset
                continue

            # Now we know we have changes - apply them
            for block_name, prop_changes in changes_by_block.items():
                # Ensure the component data entry exists (only now, when we know we have changes)
                try:
                    ides.add_component_data_entry(item_def, block_name)
                except Exception:
                    pass  # ignore if already present

                for prop_name, prop_type_label, new_val, current_value, original_input in prop_changes:
                    success, error_msg = _apply_property_change(
                        ides, item_def, block_name, prop_name,
                        prop_type_label, new_val, current_value, original_input
                    )

                    if success:
                        modified_assets.add(asset.get_path_name())
                        unreal.log("Row " + str(row_index + 1) + ": Set " + str(block_name) + "." + str(prop_name) + " = " + str(new_val)[:50])
                    else:
                        errors.append("Row " + str(row_index + 1) + ": Failed setting " + str(block_name) + "." + str(prop_name) + " - " + str(error_msg))

    return errors, list(modified_assets)
