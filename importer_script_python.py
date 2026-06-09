import io
import csv
import re
import json
import unreal


# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
# CRITICAL WARNING - DO NOT USE CURLY BRACES WITH CONTENT INSIDE
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
#
# This script runs inside an Unreal Blueprint Format node.
# The Format node interprets curly braces as format placeholders and DELETES
# anything inside them.
#
# FORBIDDEN patterns (will break the script):
#   - Dict literals:  my_dict = OPENBRACE"key": "value"CLOSEBRACE   <-- BROKEN
#   - F-strings:      f"Row OPENBRACEindexCLOSEBRACE"               <-- BROKEN
#   - Any content between braces gets deleted silently!
#
# ALLOWED patterns:
#   - Empty braces:   my_dict = {}                 <-- OK
#   - The two format placeholders below (TSV and DTPath) are intentional
#   - dict() calls:   dict([("k", "v")])           <-- OK
#   - Tuples:         (("k", "v"),)                <-- OK
#
# USE TUPLES OR dict() CONSTRUCTOR INSTEAD OF DICT LITERALS!
#
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!


# --- inputs from your Format node
tsv = r"""{TSV}"""
dt_path = r"""{DTPath}"""


# -------------------------
# Helpers
# -------------------------

def _maybe_prefix_asset_ref(val):
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
                unreal.log_warning(
                    "Sanitized double slashes in asset path: "
                    + str(inner)
                    + " -> "
                    + str(inner_sanitized)
                )
                s = s[: q1 + 1] + inner_sanitized + s[q2:]
        return s

    if s.startswith("/"):
        path = s.strip("'\"")
        path_sanitized = re.sub(r"/+", "/", path)
        if path_sanitized != path:
            unreal.log_warning(
                "Sanitized double slashes in asset path: "
                + str(path)
                + " -> "
                + str(path_sanitized)
            )
        path = path_sanitized

        obj = unreal.load_asset(path)
        if obj:
            cls_path = obj.get_class().get_path_name()
            full = cls_path + "'" + obj.get_path_name() + "'"
            return full

        return path

    return val


def _cast(obj, cls):
    try:
        return cls.cast(obj)
    except Exception:
        return None


# -------------------------
# Phase 1 — TSV Parsing
# -------------------------

def parse_tsv(tsv_string):
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
        raise Exception("After skipping # and ! columns, no columns remain.")

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


# -------------------------
# Phase 2 — DataTable Import
# -------------------------

def import_into_datatable(dt_asset_path, csv_text):
    dt = unreal.load_asset(dt_asset_path)
    if not dt:
        raise Exception("DataTable not found: " + str(dt_asset_path))

    ok = unreal.DataTableFunctionLibrary.fill_data_table_from_csv_string(
        dt, csv_text
    )

    return dt, ok


# -------------------------
# Phase 3a — Find Missing Assets
# -------------------------

def find_missing_assets(patch_rows):
    """
    Scan patch_rows for asset paths that don't exist.
    Returns a list of tuples: (row_index, missing_asset_path, template_asset_path)
    where template_asset_path is the asset from the row immediately above (or None if row 0).
    """
    missing = []
    last_valid_asset_by_column = {}

    for row_index, row_patch_info in enumerate(patch_rows):
        for col_index, (asset_path, patch_text) in enumerate(row_patch_info):
            if not asset_path:
                continue

            asset = unreal.load_asset(asset_path)
            if asset:
                # Track this as the last valid asset for this column
                last_valid_asset_by_column[col_index] = asset_path
            else:
                # Asset doesn't exist - record it with the template from above
                template_path = last_valid_asset_by_column.get(col_index, None)
                missing.append((row_index, asset_path, template_path))

    return missing


def prompt_user_create_missing_assets(missing_assets):
    """
    Show a dialog asking the user if they want to create missing assets.
    Returns True if user wants to create them, False otherwise.
    """
    if not missing_assets:
        return False

    msg = "The following " + str(len(missing_assets)) + " asset(s) were not found:\n\n"
    for row_idx, asset_path, template_path in missing_assets[:10]:  # Show first 10
        msg += "  Row " + str(row_idx + 1) + ": " + str(asset_path) + "\n"
        if template_path:
            msg += "    (will clone from: " + str(template_path) + ")\n"
        else:
            msg += "    (WARNING: No template available - first row)\n"

    if len(missing_assets) > 10:
        msg += "\n  ... and " + str(len(missing_assets) - 10) + " more.\n"

    msg += "\nDo you want to create the missing assets by cloning from the row above?"

    result = unreal.EditorDialog.show_message(
        "Missing Assets Detected",
        msg,
        unreal.AppMsgType.YES_NO
    )

    return result == unreal.AppReturnType.YES


def create_missing_assets(missing_assets):
    """
    Create missing assets by duplicating the template asset.
    Returns (created_paths, errors).
    """
    created = []
    errors = []
    eal = unreal.EditorAssetLibrary

    for row_idx, new_asset_path, template_path in missing_assets:
        if not template_path:
            errors.append(
                "Row " + str(row_idx + 1) + ": Cannot create " + str(new_asset_path)
                + " - no template asset available (this is the first row with an asset in this column)"
            )
            continue

        # Check if template still exists
        if not eal.does_asset_exist(template_path):
            errors.append(
                "Row " + str(row_idx + 1) + ": Template asset not found: " + str(template_path)
            )
            continue

        # Check if new path already exists (shouldn't happen, but be safe)
        if eal.does_asset_exist(new_asset_path):
            errors.append(
                "Row " + str(row_idx + 1) + ": Asset already exists: " + str(new_asset_path)
            )
            continue

        try:
            # Duplicate the asset
            success = eal.duplicate_asset(template_path, new_asset_path)
            if success:
                created.append(new_asset_path)
                unreal.log("Created asset: " + str(new_asset_path) + " (cloned from " + str(template_path) + ")")
            else:
                errors.append(
                    "Row " + str(row_idx + 1) + ": Failed to duplicate " + str(template_path)
                    + " to " + str(new_asset_path)
                )
        except Exception as ex:
            errors.append(
                "Row " + str(row_idx + 1) + ": Error creating " + str(new_asset_path)
                + " - " + str(ex)
            )

    return created, errors


# -------------------------
# Phase 3b — ItemDefinition Patch Phase (type-safe)
# -------------------------

# Friendly name mappings for component data blocks
# Keys are short/friendly names, values are the full script paths
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

# Base properties on ItemDefinitionBase (not component data)
# Maps friendly names to the actual property names
# Special handling is needed for struct properties like FortReleaseVersion
# NOTE: Using tuple of tuples (NOT dict literal) due to Format node constraints
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


def _resolve_base_property(name):
    """
    Check if name is a base property alias and return the actual property name.
    Returns None if not a base property.
    """
    name_lower = name.lower()
    for alias, prop_name in BASE_PROPERTY_ALIASES:
        if alias.lower() == name_lower:
            return prop_name
    return None


def _apply_base_properties(item_def, patch_data, row_index, errors, modified_assets):
    """
    Apply base properties (ItemName, ItemDescription, ItemShortDescription, ReleaseVersion)
    to an ItemDefinitionBase.
    Returns the patch_data dict with base property keys removed (so they aren't processed as component blocks).
    """
    remaining_patch_data = {}
    asset_path = item_def.get_path_name()

    for key, value in patch_data.items():
        prop_name = _resolve_base_property(key)
        if prop_name is not None:
            try:
                # Special handling for ReleaseVersion (struct property)
                if prop_name == "ReleaseVersion":
                    _set_release_version(item_def, value, row_index, errors, modified_assets, asset_path)
                    continue

                # For FText properties, Unreal Python accepts plain strings
                # and converts them appropriately
                str_value = str(value) if value is not None else ""

                # Try to get current value for change detection
                try:
                    current_value = item_def.get_editor_property(prop_name)
                    # FText comparison - convert to string for comparison
                    current_str = str(current_value) if current_value else ""
                    if current_str == str_value:
                        # No change needed
                        continue
                except Exception:
                    pass  # If we can't read, just try to set

                # Set the property
                item_def.set_editor_property(prop_name, str_value)
                modified_assets.add(asset_path)
                unreal.log(
                    "Row "
                    + str(row_index + 1)
                    + ": Set "
                    + str(prop_name)
                    + " = "
                    + str(str_value)[:50]
                    + ("..." if len(str_value) > 50 else "")
                )
            except Exception as ex:
                errors.append(
                    "Row "
                    + str(row_index + 1)
                    + ": Failed setting base property "
                    + str(key)
                    + " ("
                    + str(prop_name)
                    + ") - "
                    + str(ex)
                )
        else:
            # Not a base property, keep for component block processing
            remaining_patch_data[key] = value

    return remaining_patch_data


def _set_release_version(item_def, value, row_index, errors, modified_assets, asset_path):
    """
    Set the ReleaseVersion property on an ItemDefinitionBase.
    Accepts either a simple string like "Future" or a dict with key "VersionName".
    """
    # Determine the version name string
    if isinstance(value, dict):
        version_name = value.get("VersionName", value.get("versionName", ""))
    else:
        version_name = str(value) if value is not None else ""

    # Try multiple possible property names
    property_names_to_try = ("ReleaseVersion", "FortReleaseVersion", "releaseVersion")
    last_error = None

    for prop_name in property_names_to_try:
        try:
            # Try to get the current struct
            release_version = item_def.get_editor_property(prop_name)

            # Check if change is needed
            try:
                current_version_name = release_version.get_editor_property("VersionName")
                if str(current_version_name) == version_name:
                    # No change needed
                    unreal.log(
                        "Row "
                        + str(row_index + 1)
                        + ": "
                        + prop_name
                        + " already set to "
                        + str(version_name)
                    )
                    return
            except Exception:
                pass

            # Set the VersionName on the struct
            release_version.set_editor_property("VersionName", version_name)
            item_def.set_editor_property(prop_name, release_version)
            modified_assets.add(asset_path)
            unreal.log(
                "Row "
                + str(row_index + 1)
                + ": Set "
                + prop_name
                + ".VersionName = "
                + str(version_name)
            )
            return
        except Exception as ex:
            last_error = ex
            # Try next property name
            continue

    # If struct approach failed, try text format with each property name
    text_value = '(VersionName="' + version_name + '")'
    for prop_name in property_names_to_try:
        try:
            item_def.set_editor_property(prop_name, text_value)
            modified_assets.add(asset_path)
            unreal.log(
                "Row "
                + str(row_index + 1)
                + ": Set "
                + prop_name
                + " = "
                + str(text_value)
            )
            return
        except Exception as ex:
            last_error = ex
            continue

    # All attempts failed
    errors.append(
        "Row "
        + str(row_index + 1)
        + ": Failed setting ReleaseVersion (tried ReleaseVersion, FortReleaseVersion) - "
        + str(last_error)
    )


def _resolve_block_name(name, available_types):
    """
    Resolve a friendly block name to its full script path.
    Returns the resolved name, or the original if no alias matches.
    """
    # If already a full path that exists, use it directly
    if name in available_types:
        return name

    # Check aliases (case-insensitive)
    name_lower = name.lower()
    for alias, full_path in BLOCK_NAME_ALIASES:
        if alias.lower() == name_lower:
            if full_path in available_types:
                return full_path

    # Partial match: check if the name appears at the end of any available type
    for avail in available_types:
        # e.g., "MaxStackSize" matches "...ItemComponentData_MaxStackSize"
        if avail.endswith("_" + name) or avail.endswith("." + name):
            return avail

    return name


def _probe_property(ides, item_def, block_name, prop_name):
    """
    Probe property by calling the subsystem getters.
    Return a tuple (label, current_value) where label is a short
    string describing the detected type, and current_value is the
    object returned by the getter (or None).

    Note: The Unreal API may return None instead of raising exceptions
    on type mismatches, so we check for None to detect failures.
    """
    # ScalableFloat (FScalableFloat) - check this first as it's common for numeric values
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
        # verify shape: expect attribute gameplay_tags (list-like)
        if v is not None and hasattr(v, "gameplay_tags"):
            return "gameplay_tag_container", v
    except Exception:
        pass

    # Generic text (strings, TSoftObjectPtr representations, enums, etc)
    # Check this BEFORE bool because enums can false-positive as bool (non-zero = True)
    try:
        v = ides.get_property_value_generic_text(item_def, block_name, prop_name)
        if v is not None:
            return "generic_text", v
    except Exception:
        pass

    # Bool - check after generic_text since bool probe can false-positive on enums
    try:
        v = ides.get_property_value_bool(item_def, block_name, prop_name)
        if v is not None:
            return "bool", v
    except Exception:
        pass

    return "unknown", None


def apply_itemdefinition_patches(patch_rows):
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
                errors.append(
                    "Row "
                    + str(row_index + 1)
                    + ": Patch JSON provided but target asset column empty."
                )
                continue

            try:
                patch_data = json.loads(patch_text)
            except Exception as ex:
                errors.append(
                    "Row "
                    + str(row_index + 1)
                    + ": Invalid JSON - "
                    + str(ex)
                )
                continue

            asset = unreal.load_asset(asset_path)
            if not asset:
                errors.append(
                    "Row "
                    + str(row_index + 1)
                    + ": Could not load asset "
                    + str(asset_path)
                )
                continue

            item_def = _cast(asset, unreal.ItemDefinitionBase)
            if item_def is None:
                errors.append(
                    "Row "
                    + str(row_index + 1)
                    + ": Asset is not an ItemDefinitionBase "
                    + str(asset_path)
                )
                continue

            # Apply base properties first (ItemName, ItemDescription, ItemShortDescription)
            # This returns the remaining patch data (with base properties removed)
            patch_data = _apply_base_properties(
                item_def, patch_data, row_index, errors, modified_assets
            )

            # If no remaining patch data, skip component block processing
            if not patch_data:
                continue

            # Convert to Python list and extract path names for comparison
            component_types_raw = list(ides.get_all_component_data_type(item_def))
            component_types = [c.get_path_name() for c in component_types_raw]

            for block_name_input, properties in patch_data.items():

                # Resolve friendly name to full script path
                block_name = _resolve_block_name(block_name_input, component_types)

                if block_name not in component_types:
                    errors.append(
                        "Row "
                        + str(row_index + 1)
                        + ": Block name not found: "
                        + str(block_name_input)
                        + ". Available: "
                        + ", ".join(component_types)
                    )
                    continue

                if not isinstance(properties, dict):
                    errors.append(
                        "Row "
                        + str(row_index + 1)
                        + ": Block "
                        + str(block_name)
                        + " must contain property dictionary."
                    )
                    continue

                # Ensure the component data entry exists (best effort)
                try:
                    ides.add_component_data_entry(item_def, block_name)
                except Exception:
                    # ignore; add_component_data_entry might fail if already present
                    pass

                for prop_name, value in properties.items():
                    try:
                        # Probe actual property type
                        prop_type_label, current_value = _probe_property(
                            ides, item_def, block_name, prop_name
                        )

                        # Debug logging for troubleshooting
                        unreal.log(
                            "DEBUG: "
                            + str(block_name)
                            + "."
                            + str(prop_name)
                            + " -> probe_type="
                            + str(prop_type_label)
                            + ", current="
                            + repr(current_value)
                            + ", new="
                            + repr(value)
                        )

                        # Attempt to set based on the probed type
                        set_succeeded = False

                        if prop_type_label == "bool":
                            try:
                                new_val = bool(value)
                                if current_value == new_val:
                                    set_succeeded = True  # No change needed
                                else:
                                    ides.set_property_value_bool(
                                        item_def, block_name, prop_name, new_val
                                    )
                                    set_succeeded = True
                                    modified_assets.add(asset.get_path_name())
                            except Exception as ex:
                                errors.append(
                                    "Row "
                                    + str(row_index + 1)
                                    + ": Failed setting bool "
                                    + str(block_name)
                                    + "."
                                    + str(prop_name)
                                    + " - "
                                    + str(ex)
                                )

                        elif prop_type_label == "float":
                            try:
                                new_val = float(value)
                                if current_value == new_val:
                                    set_succeeded = True  # No change needed
                                else:
                                    ides.set_property_value_float(
                                        item_def, block_name, prop_name, new_val
                                    )
                                    set_succeeded = True
                                    modified_assets.add(asset.get_path_name())
                            except Exception as ex:
                                errors.append(
                                    "Row "
                                    + str(row_index + 1)
                                    + ": Failed setting float "
                                    + str(block_name)
                                    + "."
                                    + str(prop_name)
                                    + " - "
                                    + str(ex)
                                )

                        elif prop_type_label == "scalable_float":
                            try:
                                # current_value is expected to be an FScalableFloat-like structure
                                # set its .value if numeric was provided
                                if isinstance(value, dict):
                                    # If user provided structured scalable float, try to map
                                    if "value" in value:
                                        new_val = float(value["value"])
                                    else:
                                        # fallback: attempt convert whole dict to string
                                        new_val = float(value.get("value", 0))
                                else:
                                    new_val = float(value)

                                if current_value.value == new_val:
                                    set_succeeded = True  # No change needed
                                else:
                                    current_value.value = new_val
                                    ides.set_property_value_scalable_float(
                                        item_def, block_name, prop_name, current_value
                                    )
                                    set_succeeded = True
                                    modified_assets.add(asset.get_path_name())
                            except Exception as ex:
                                errors.append(
                                    "Row "
                                    + str(row_index + 1)
                                    + ": Failed setting scalable float "
                                    + str(block_name)
                                    + "."
                                    + str(prop_name)
                                    + " - "
                                    + str(ex)
                                )

                        elif prop_type_label == "gameplay_tag_container":
                            try:
                                # current_value should expose gameplay_tags
                                container = current_value

                                # Get current tag names for comparison
                                current_tag_names = set()
                                for t in container.gameplay_tags:
                                    try:
                                        current_tag_names.add(str(t.tag_name))
                                    except Exception:
                                        current_tag_names.add(str(t))

                                # Build new tag names set
                                if isinstance(value, list):
                                    new_tag_names = set(value)
                                else:
                                    new_tag_names = set([str(value)]) if value else set()

                                if current_tag_names == new_tag_names:
                                    set_succeeded = True  # No change needed
                                else:
                                    # Clear the array using native methods
                                    while len(container.gameplay_tags) > 0:
                                        container.gameplay_tags.pop()

                                    for tag_name in new_tag_names:
                                        tag = ides.find_existing_tag_by_name(tag_name)
                                        if tag:
                                            container.gameplay_tags.append(tag)

                                    ides.set_property_value_gameplay_tag_container(
                                        item_def, block_name, prop_name, container
                                    )
                                    set_succeeded = True
                                    modified_assets.add(asset.get_path_name())
                            except Exception as ex:
                                # If the property unexpectedly expects objects other than tags,
                                # fall back to a safe representation below.
                                errors.append(
                                    "Row "
                                    + str(row_index + 1)
                                    + ": Failed setting gameplay tag container "
                                    + str(block_name)
                                    + "."
                                    + str(prop_name)
                                    + " - "
                                    + str(ex)
                                )

                        elif prop_type_label == "generic_text":
                            try:
                                # Handle single FGameplayTag structs passed as dict
                                # JSON dict with TagName key -> Unreal struct text
                                # Example: dict(TagName="Some.Tag") becomes (TagName="Some.Tag")
                                if isinstance(value, dict) and "TagName" in value:
                                    tag_name = value["TagName"]
                                    new_val = '(TagName="' + str(tag_name) + '")'
                                elif isinstance(value, dict):
                                    # For other dict values, convert to Unreal struct format
                                    # Example: dict(Key="Value") becomes (Key="Value")
                                    parts = []
                                    for k, v in value.items():
                                        if isinstance(v, str):
                                            parts.append(str(k) + '="' + str(v) + '"')
                                        elif isinstance(v, bool):
                                            parts.append(str(k) + "=" + str(v))
                                        else:
                                            parts.append(str(k) + "=" + str(v))
                                    new_val = "(" + ",".join(parts) + ")"
                                else:
                                    new_val = str(value)

                                if current_value == new_val:
                                    set_succeeded = True  # No change needed
                                else:
                                    ides.set_property_value_generic_text(
                                        item_def, block_name, prop_name, new_val
                                    )
                                    set_succeeded = True
                                    modified_assets.add(asset.get_path_name())
                            except Exception as ex:
                                errors.append(
                                    "Row "
                                    + str(row_index + 1)
                                    + ": Failed setting generic text "
                                    + str(block_name)
                                    + "."
                                    + str(prop_name)
                                    + " - "
                                    + str(ex)
                                )

                        else:
                            # Unknown/unsupported probe result; try safe fallbacks.
                            # For unknown types, we can't easily detect changes, so always set
                            try:
                                # If value is a list, don't assume object array — stringify safely.
                                if isinstance(value, list):
                                    ides.set_property_value_generic_text(
                                        item_def, block_name, prop_name, json.dumps(value)
                                    )
                                    set_succeeded = True
                                    modified_assets.add(asset.get_path_name())
                                else:
                                    ides.set_property_value_generic_text(
                                        item_def, block_name, prop_name, str(value)
                                    )
                                    set_succeeded = True
                                    modified_assets.add(asset.get_path_name())
                            except Exception as ex:
                                errors.append(
                                    "Row "
                                    + str(row_index + 1)
                                    + ": Failed setting unknown-typed prop "
                                    + str(block_name)
                                    + "."
                                    + str(prop_name)
                                    + " - "
                                    + str(ex)
                                )

                        if not set_succeeded:
                            # final attempt: try to set as generic text stringified JSON
                            try:
                                ides.set_property_value_generic_text(
                                    item_def, block_name, prop_name, json.dumps(value)
                                )
                                set_succeeded = True
                                modified_assets.add(asset.get_path_name())
                            except Exception as final_ex:
                                # Collect detailed diagnostic: what the probe returned
                                errors.append(
                                    "Row "
                                    + str(row_index + 1)
                                    + ": Unable to set property "
                                    + str(block_name)
                                    + "."
                                    + str(prop_name)
                                    + ". Probe result: "
                                    + str(prop_type_label)
                                    + " "
                                    + repr(current_value)
                                    + ". Final error: "
                                    + str(final_ex)
                                )

                    except Exception as ex:
                        errors.append(
                            "Row "
                            + str(row_index + 1)
                            + ": Unexpected error setting "
                            + str(block_name)
                            + "."
                            + str(prop_name)
                            + " - "
                            + str(ex)
                        )

    return errors, list(modified_assets)


# -------------------------
# Phase 4 — Save Modified Assets
# -------------------------

def save_modified_assets(asset_paths):
    for path in asset_paths:
        try:
            unreal.EditorAssetLibrary.save_asset(path)
        except Exception:
            pass


# -------------------------
# Phase 5 — UI Refresh
# -------------------------

def refresh_datatable_editor_ui(dt):
    try:
        aes = unreal.get_editor_subsystem(unreal.AssetEditorSubsystem)
        if aes:
            try:
                aes.close_all_editors_for_asset(dt)
            except Exception:
                pass
            try:
                aes.open_editor_for_assets([dt])
            except Exception:
                pass
            try:
                unreal.EditorAssetLibrary.sync_browser_to_objects(
                    [dt.get_path_name()]
                )
            except Exception:
                pass
    except Exception:
        pass


# -------------------------
# Execution Flow
# -------------------------

csv_text, skipped_columns, patch_rows = parse_tsv(tsv)

dt_asset, success = import_into_datatable(dt_path, csv_text)

if not success:
    unreal.log_error("Import failed; check Output Log for row/column errors.")
else:
    # Attempt to save the datatable (may fail due to source control; that's fine)
    try:
        unreal.EditorAssetLibrary.save_asset(dt_asset.get_path_name())
    except Exception:
        pass

    # Phase 3a: Check for missing assets and offer to create them
    created_assets = []
    creation_errors = []

    missing_assets = find_missing_assets(patch_rows)
    if missing_assets:
        if prompt_user_create_missing_assets(missing_assets):
            created_assets, creation_errors = create_missing_assets(missing_assets)
            # Save newly created assets
            if created_assets:
                save_modified_assets(created_assets)

    # Phase 3b: Apply patches (now including newly created assets)
    errors, modified_assets = apply_itemdefinition_patches(patch_rows)

    # Combine creation errors with patch errors
    all_errors = creation_errors + errors

    if modified_assets:
        save_modified_assets(modified_assets)

    try:
        count = len(
            unreal.DataTableFunctionLibrary.get_data_table_row_names(dt_asset)
        )
    except Exception:
        count = 0

    msg = (
        "Imported "
        + str(count)
        + " rows into\n"
        + str(dt_asset.get_path_name())
    )

    if created_assets:
        msg += "\n\nCreated " + str(len(created_assets)) + " new asset(s):\n"
        for path in created_assets[:10]:
            msg += "  " + str(path) + "\n"
        if len(created_assets) > 10:
            msg += "  ... and " + str(len(created_assets) - 10) + " more.\n"

    if skipped_columns:
        msg += "\nSkipped columns: " + ", ".join(skipped_columns)

    if all_errors:
        msg += "\n\nErrors:\n"
        msg += "\n".join(all_errors)

    unreal.EditorDialog.show_message(
        "Data Table Import",
        msg,
        unreal.AppMsgType.OK
    )

    refresh_datatable_editor_ui(dt_asset)