"""
TSV Import Toolset for MCP

This toolset exposes the spreadsheet import functionality to Claude via MCP.
Place this file in your Unreal project's Python directory and it will be
auto-discovered by the ToolsetRegistry.

Usage via MCP:
    1. Write TSV data to /Saved/TSVImport/<filename>.tsv
    2. Call import_tsv_file(file_path, dt_path)
"""

from __future__ import annotations

import unreal
import toolset_registry
import io
import csv
import re
import json


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
    def import_tsv_file(file_path: str, dt_path: str) -> dict:
        """Import a TSV file into a DataTable and apply ItemDefinition patches.

        Args:
            file_path: Path to the TSV file. Use Unreal virtual paths like
                       '/Saved/TSVImport/data.tsv' or absolute disk paths.
            dt_path: The asset path of the target DataTable.

        Returns:
            A dict with keys:
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
                result["errors"].append(f"Could not read file: {file_path}")
                return result

            # Parse TSV
            csv_text, skipped_columns, patch_rows = _parse_tsv(tsv_content)
            result["skipped_columns"] = skipped_columns

            # Import into DataTable
            dt_asset, import_success = _import_into_datatable(dt_path, csv_text)
            if not import_success:
                result["errors"].append("DataTable import failed - check Output Log")
                return result

            # Save DataTable
            try:
                unreal.EditorAssetLibrary.save_asset(dt_asset.get_path_name())
            except Exception:
                pass

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
            result["errors"].append(f"Import failed: {str(ex)}")

        return result

    @toolset_registry.tool_call
    @staticmethod
    def import_tsv_string(tsv_content: str, dt_path: str) -> dict:
        """Import TSV content directly (as a string) into a DataTable.

        Use this when you have the TSV content in memory rather than a file.
        For large TSV data, prefer import_tsv_file to avoid MCP message size limits.

        Args:
            tsv_content: The TSV data as a string.
            dt_path: The asset path of the target DataTable.

        Returns:
            Same as import_tsv_file.
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
            # Parse TSV
            csv_text, skipped_columns, patch_rows = _parse_tsv(tsv_content)
            result["skipped_columns"] = skipped_columns

            # Import into DataTable
            dt_asset, import_success = _import_into_datatable(dt_path, csv_text)
            if not import_success:
                result["errors"].append("DataTable import failed - check Output Log")
                return result

            # Save DataTable
            try:
                unreal.EditorAssetLibrary.save_asset(dt_asset.get_path_name())
            except Exception:
                pass

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
            result["errors"].append(f"Import failed: {str(ex)}")

        return result

    @toolset_registry.tool_call
    @staticmethod
    def list_available_datatables(folder_path: str = "/Game/") -> list:
        """List DataTable assets in a folder.

        Args:
            folder_path: The content folder to search (default: /Game/)

        Returns:
            List of DataTable asset paths.
        """
        ar = unreal.AssetRegistryHelpers.get_asset_registry()

        filter = unreal.ARFilter()
        filter.class_names = ["DataTable"]
        filter.package_paths = [folder_path]
        filter.recursive_paths = True

        assets = ar.get_assets(filter)
        return [str(a.package_name) + "." + str(a.asset_name) for a in assets]


# ============================================================================
# Helper Functions (from importer_script_python.py)
# ============================================================================

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


def _parse_tsv(tsv_string: str):
    """Parse TSV into CSV for DataTable and extract patch info."""
    normalized = tsv_string.replace("\r\n", "\n").replace("\r", "\n")
    reader = csv.reader(io.StringIO(normalized), delimiter="\t")
    rows = list(reader)

    if not rows:
        raise Exception("No TSV rows provided.")

    headers = [h.replace("\ufeff", "") for h in rows[0]]

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
        keep_idx.append(i)

    if not keep_idx:
        raise Exception("After skipping # and % columns, no columns remain.")

    skipped = []
    for i in range(len(headers)):
        if i not in keep_idx:
            skipped.append(headers[i])

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
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


def _import_into_datatable(dt_asset_path: str, csv_text: str):
    """Import CSV text into a DataTable."""
    dt = unreal.load_asset(dt_asset_path)
    if not dt:
        raise Exception(f"DataTable not found: {dt_asset_path}")

    ok = unreal.DataTableFunctionLibrary.fill_data_table_from_csv_string(dt, csv_text)
    return dt, ok


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
# ItemDefinition Patching (condensed from importer_script_python.py)
# ============================================================================

BLOCK_NAME_ALIASES = {
    "MaxStackSize": "/Script/ItemizationCoreRuntime.ItemComponentData_MaxStackSize",
    "Max Stack Size": "/Script/ItemizationCoreRuntime.ItemComponentData_MaxStackSize",
    "Pickup": "/Script/FortniteGame.FortItemComponentData_Pickup",
    "Traits": "/Script/ItemizationCoreRuntime.ItemComponentData_Traits",
    "Tags": "/Script/ItemizationCoreRuntime.ItemComponentData_OwnedGameplayTags",
    "OwnedGameplayTags": "/Script/ItemizationCoreRuntime.ItemComponentData_OwnedGameplayTags",
    "Icon": "/Script/ItemizationCoreRuntime.ItemComponentData_Icon",
    "OwnerPose": "/Script/ItemizationCoreRuntime.ItemComponentData_OwnerPose",
    "CampStructure": "/Script/HuskyGameplayRuntime.HuskyItemComponentData_CampStructure",
    "Rarity": "/Script/FortniteGame.FortItemComponentData_Rarity",
}

BASE_PROPERTY_ALIASES = {
    "ItemName": "ItemName",
    "Item Name": "ItemName",
    "Name": "ItemName",
    "ItemDescription": "ItemDescription",
    "Item Description": "ItemDescription",
    "Description": "ItemDescription",
    "ItemShortDescription": "ItemShortDescription",
    "Short Description": "ItemShortDescription",
    "ReleaseVersion": "ReleaseVersion",
    "Release Version": "ReleaseVersion",
}


def _resolve_base_property(name: str):
    """Check if name is a base property alias."""
    name_lower = name.lower()
    for alias, prop_name in BASE_PROPERTY_ALIASES.items():
        if alias.lower() == name_lower:
            return prop_name
    return None


def _resolve_block_name(name: str, available_types: list) -> str:
    """Resolve friendly block name to full script path."""
    if name in available_types:
        return name

    name_lower = name.lower()
    for alias, full_path in BLOCK_NAME_ALIASES.items():
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


def _apply_itemdefinition_patches(patch_rows: list):
    """Apply JSON patches to ItemDefinition assets."""
    ides = unreal.get_editor_subsystem(unreal.ItemDefinitionEditorSubsystem)
    if ides is None:
        return ["ItemDefinitionEditorSubsystem not found."], []

    errors = []
    modified_assets = set()

    for row_index, row_patch_info in enumerate(patch_rows):
        for asset_path, patch_text in row_patch_info:
            if not patch_text or not asset_path:
                continue

            try:
                patch_data = json.loads(patch_text)
            except Exception as ex:
                errors.append(f"Row {row_index + 1}: Invalid JSON - {ex}")
                continue

            asset = unreal.load_asset(asset_path)
            if not asset:
                errors.append(f"Row {row_index + 1}: Could not load {asset_path}")
                continue

            item_def = _cast(asset, unreal.ItemDefinitionBase)
            if item_def is None:
                errors.append(f"Row {row_index + 1}: Not an ItemDefinitionBase: {asset_path}")
                continue

            # Apply base properties
            remaining_data = {}
            for key, value in patch_data.items():
                prop_name = _resolve_base_property(key)
                if prop_name:
                    try:
                        item_def.set_editor_property(prop_name, str(value) if value else "")
                        modified_assets.add(asset.get_path_name())
                    except Exception as ex:
                        errors.append(f"Row {row_index + 1}: Failed setting {key} - {ex}")
                else:
                    remaining_data[key] = value

            if not remaining_data:
                continue

            # Get component types
            component_types_raw = list(ides.get_all_component_data_type(item_def))
            component_types = [c.get_path_name() for c in component_types_raw]

            # Apply component data patches
            for block_name_input, properties in remaining_data.items():
                block_name = _resolve_block_name(block_name_input, component_types)

                if block_name not in component_types:
                    errors.append(f"Row {row_index + 1}: Block not found: {block_name_input}")
                    continue

                if not isinstance(properties, dict):
                    errors.append(f"Row {row_index + 1}: Block {block_name} must be a dict")
                    continue

                # Ensure component entry exists
                try:
                    ides.add_component_data_entry(item_def, block_name)
                except Exception:
                    pass

                for prop_name, value in properties.items():
                    prop_type, current_val = _probe_property(ides, item_def, block_name, prop_name)

                    # Compute new value
                    if prop_type == "scalable_float":
                        new_val = float(value.get("value", value) if isinstance(value, dict) else value)
                    elif prop_type == "gameplay_tag_container":
                        new_val = value  # Pass through for tag handling
                    elif prop_type == "generic_text":
                        if isinstance(value, dict) and "TagName" in value:
                            new_val = f'(TagName="{value["TagName"]}")'
                        elif isinstance(value, dict):
                            parts = [f'{k}="{v}"' if isinstance(v, str) else f'{k}={v}' for k, v in value.items()]
                            new_val = "(" + ",".join(parts) + ")"
                        else:
                            new_val = str(value)
                    else:
                        new_val = value

                    success, error_msg = _apply_property_change(
                        ides, item_def, block_name, prop_name,
                        prop_type, new_val, current_val, value
                    )

                    if success:
                        modified_assets.add(asset.get_path_name())
                        unreal.log(f"Row {row_index + 1}: Set {block_name}.{prop_name}")
                    else:
                        errors.append(f"Row {row_index + 1}: Failed {block_name}.{prop_name} - {error_msg}")

    return errors, list(modified_assets)
