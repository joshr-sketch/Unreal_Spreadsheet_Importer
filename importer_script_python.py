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


def _check_base_property_change(item_def, prop_name, value):
    """
    Check if a base property needs to change.
    Returns (needs_change, new_value, current_value)
    """
    str_value = str(value) if value is not None else ""
    try:
        current_value = item_def.get_editor_property(prop_name)
        current_str = str(current_value) if current_value else ""
        if current_str == str_value:
            return False, str_value, current_str
        return True, str_value, current_str
    except Exception:
        # If we can't read, assume we need to set
        return True, str_value, None


def _check_release_version_change(item_def, value):
    """
    Check if ReleaseVersion needs to change.
    Returns (needs_change, version_name, current_version_name, prop_name_to_use)
    """
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
                # Can't read current, assume we need to set
                return True, version_name, None, prop_name
        except Exception:
            continue

    # No valid property found, will need to try setting
    return True, version_name, None, "ReleaseVersion"


def _apply_base_properties(item_def, patch_data, row_index, errors, modified_assets):
    """
    Apply base properties (ItemName, ItemDescription, ItemShortDescription, ReleaseVersion)
    to an ItemDefinitionBase.
    Returns the patch_data dict with base property keys removed (so they aren't processed as component blocks).

    Uses collect-then-apply pattern: first checks all properties for changes,
    then only modifies the asset if there are actual changes.
    """
    remaining_patch_data = {}
    asset_path = item_def.get_path_name()

    # Phase 1: Collect changes needed (read-only)
    changes_needed = []  # List of (prop_name, new_value, is_release_version)

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
                _set_release_version(item_def, new_value, row_index, errors, modified_assets, asset_path)
            else:
                item_def.set_editor_property(prop_name, new_value)
                modified_assets.add(asset_path)
                unreal.log(
                    "Row "
                    + str(row_index + 1)
                    + ": Set "
                    + str(prop_name)
                    + " = "
                    + str(new_value)[:50]
                    + ("..." if len(str(new_value)) > 50 else "")
                )
        except Exception as ex:
            errors.append(
                "Row "
                + str(row_index + 1)
                + ": Failed setting base property "
                + str(original_key)
                + " ("
                + str(prop_name)
                + ") - "
                + str(ex)
            )

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


def _compute_new_value(prop_type_label, value, current_value):
    """
    Compute what the new value should be for a given property type and input value.
    Returns (new_value, comparable_new) where:
      - new_value is the value to pass to the setter
      - comparable_new is a value that can be compared with current_value
    For some types these are the same; for others they differ.
    """
    if prop_type_label == "bool":
        new_val = bool(value)
        return new_val, new_val

    elif prop_type_label == "float":
        new_val = float(value)
        return new_val, new_val

    elif prop_type_label == "scalable_float":
        if isinstance(value, dict):
            if "value" in value:
                new_val = float(value["value"])
            else:
                new_val = float(value.get("value", 0))
        else:
            new_val = float(value)
        # For scalable_float, we compare against current_value.value
        return new_val, new_val

    elif prop_type_label == "gameplay_tag_container":
        # Build new tag names set for comparison
        if isinstance(value, list):
            new_tag_names = set(value)
        else:
            new_tag_names = set([str(value)]) if value else set()
        # Return the original value for setter, tag names for comparison
        return value, new_tag_names

    elif prop_type_label == "generic_text":
        # Handle single FGameplayTag structs passed as dict
        # JSON dict with TagName key -> Unreal struct text
        if isinstance(value, dict) and "TagName" in value:
            tag_name = value["TagName"]
            new_val = '(TagName="' + str(tag_name) + '")'
        elif isinstance(value, dict):
            # For other dict values, convert to Unreal struct format
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
        return new_val, new_val

    else:
        # Unknown type - stringify
        if isinstance(value, list):
            new_val = json.dumps(value)
        else:
            new_val = str(value)
        return new_val, new_val


def _get_comparable_current(prop_type_label, current_value):
    """
    Get a comparable representation of the current value.
    """
    if prop_type_label == "scalable_float":
        # Compare against the .value attribute
        try:
            return current_value.value
        except Exception:
            return None

    elif prop_type_label == "gameplay_tag_container":
        # Get current tag names as a set
        current_tag_names = set()
        try:
            for t in current_value.gameplay_tags:
                tag_str = None

                # Method 1: Use get_editor_property("TagName") - standard Unreal Python API
                try:
                    tn = t.get_editor_property("TagName")
                    if tn is not None:
                        tag_str = str(tn)
                        if tag_str and not tag_str.startswith("<"):
                            current_tag_names.add(tag_str)
                            continue
                except Exception:
                    pass

                # Method 2: Try export_text() which might give us a parseable format
                if tag_str is None:
                    try:
                        exported = t.export_text()
                        if exported and not exported.startswith("<"):
                            # export_text might give us (TagName="...") format
                            # Extract the tag name from it
                            if 'TagName="' in exported:
                                start = exported.find('TagName="') + 9
                                end = exported.find('"', start)
                                if end > start:
                                    tag_str = exported[start:end]
                            else:
                                tag_str = exported
                    except Exception:
                        pass

                # Method 3: Try to_tuple() which might give us the tag name
                if tag_str is None:
                    try:
                        tup = t.to_tuple()
                        if tup and len(tup) > 0:
                            # First element might be the tag name
                            tag_str = str(tup[0])
                    except Exception:
                        pass

                if tag_str and not tag_str.startswith("<"):
                    current_tag_names.add(tag_str)
        except Exception as ex:
            unreal.log("DEBUG tag container error: " + str(ex))
        return current_tag_names

    else:
        # For bool, float, generic_text - direct comparison
        return current_value


def _normalize_asset_path(path_str):
    """
    Normalize asset paths for comparison.
    - Strips /Script/ClassName'...' wrapper and returns just the inner path
    - Normalizes Blueprint class path separators (colon vs period before class suffix)
    """
    if not isinstance(path_str, str):
        return path_str

    s = path_str.strip()

    # Handle /Script/ClassName'/Path/To/Asset.Asset' format
    # Extract the inner path between the quotes
    if s.startswith("/Script/") and "'" in s:
        q1 = s.find("'")
        q2 = s.rfind("'")
        if q1 != -1 and q2 > q1:
            s = s[q1 + 1:q2]

    # Normalize Blueprint class path separators
    # Unreal sometimes stores paths with colon (BP_Name:1_N1) vs period (BP_Name.1_N1)
    # Pattern: word boundary followed by colon and then a digit or letter
    # Replace :X with .X where X starts a class/object suffix
    s = re.sub(r':(\d)', r'.\1', s)

    return s


def _normalize_struct_text(text):
    """
    Normalize Unreal struct text for comparison.
    Handles case differences in property names like TagName vs Tagname.
    """
    if not isinstance(text, str):
        return text

    # For struct format like (TagName="value"), normalize to lowercase property names
    # This is a simple approach - lowercase the part before = in each property
    # Note: re is already imported at module level

    # Match property assignments like PropName="value" or PropName=value
    def normalize_prop(match):
        prop_name = match.group(1).lower()
        rest = match.group(2)
        return prop_name + rest

    # Pattern: word followed by = and either quoted string or simple value
    normalized = re.sub(r'(\w+)(="[^"]*"|=[^,)]+)', normalize_prop, text)
    return normalized


def _needs_change(prop_type_label, current_value, value):
    """
    Determine if a property needs to be changed.
    Returns (needs_change, new_value, error_message)
    where new_value is the value to set, or None if no change needed.
    """
    try:
        new_val, comparable_new = _compute_new_value(prop_type_label, value, current_value)
        comparable_current = _get_comparable_current(prop_type_label, current_value)

        # For generic_text, normalize asset paths and struct text before comparing
        if prop_type_label == "generic_text":
            comparable_new = _normalize_asset_path(comparable_new)
            comparable_current = _normalize_asset_path(comparable_current)
            # Also normalize struct text for case differences
            comparable_new = _normalize_struct_text(comparable_new)
            comparable_current = _normalize_struct_text(comparable_current)

        # For gameplay_tag_container, normalize tag names (strip any whitespace, etc)
        if prop_type_label == "gameplay_tag_container":
            # Both should be sets of strings - normalize them
            if isinstance(comparable_new, set):
                comparable_new = set(str(t).strip() for t in comparable_new)
            if isinstance(comparable_current, set):
                comparable_current = set(str(t).strip() for t in comparable_current)

        # Debug: log the actual values being compared
        if comparable_current != comparable_new:
            unreal.log(
                "DEBUG compare: current="
                + repr(comparable_current)[:100]
                + " vs new="
                + repr(comparable_new)[:100]
            )

        if comparable_current == comparable_new:
            return False, None, None

        return True, new_val, None
    except Exception as ex:
        return False, None, str(ex)


def _apply_property_change(ides, item_def, block_name, prop_name, prop_type_label, new_val, original_value, value):
    """
    Apply a single property change. Returns (success, error_message).
    For scalable_float and gameplay_tag_container, we need special handling.
    """
    try:
        if prop_type_label == "bool":
            ides.set_property_value_bool(item_def, block_name, prop_name, new_val)
            return True, None

        elif prop_type_label == "float":
            ides.set_property_value_float(item_def, block_name, prop_name, new_val)
            return True, None

        elif prop_type_label == "scalable_float":
            # For scalable_float, we modify the current value object
            original_value.value = new_val
            ides.set_property_value_scalable_float(item_def, block_name, prop_name, original_value)
            return True, None

        elif prop_type_label == "gameplay_tag_container":
            # For tag container, clear and rebuild
            container = original_value
            while len(container.gameplay_tags) > 0:
                container.gameplay_tags.pop()

            if isinstance(value, list):
                new_tag_names = value
            else:
                new_tag_names = [str(value)] if value else []

            for tag_name in new_tag_names:
                tag = ides.find_existing_tag_by_name(tag_name)
                if tag:
                    container.gameplay_tags.append(tag)

            ides.set_property_value_gameplay_tag_container(item_def, block_name, prop_name, container)
            return True, None

        elif prop_type_label == "generic_text":
            ides.set_property_value_generic_text(item_def, block_name, prop_name, new_val)
            return True, None

        else:
            # Unknown type - try generic text
            ides.set_property_value_generic_text(item_def, block_name, prop_name, new_val)
            return True, None

    except Exception as ex:
        return False, str(ex)


def apply_itemdefinition_patches(patch_rows):
    """
    Apply patches to ItemDefinition assets.

    Uses collect-then-apply pattern to avoid unnecessary source control checkouts:
    1. First, probe all properties to detect which ones need changes (read-only)
    2. Only if changes are needed, call add_component_data_entry and apply changes
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

            # Apply base properties first (uses collect-then-apply internally)
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

            # ============================================================
            # PHASE 1: Collect all changes needed (read-only probing)
            # ============================================================
            # Structure: dict of block_name -> list of (prop_name, prop_type, new_val, current_val, original_value)
            changes_by_block = {}

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

                # Probe each property to check if it needs changing
                for prop_name, value in properties.items():
                    try:
                        # Probe actual property type (read-only)
                        prop_type_label, current_value = _probe_property(
                            ides, item_def, block_name, prop_name
                        )

                        # Check if change is needed
                        needs_change, new_val, error_msg = _needs_change(
                            prop_type_label, current_value, value
                        )

                        if error_msg:
                            errors.append(
                                "Row "
                                + str(row_index + 1)
                                + ": Error checking "
                                + str(block_name)
                                + "."
                                + str(prop_name)
                                + " - "
                                + str(error_msg)
                            )
                            continue

                        if needs_change:
                            if block_name not in changes_by_block:
                                changes_by_block[block_name] = []
                            changes_by_block[block_name].append(
                                (prop_name, prop_type_label, new_val, current_value, value)
                            )

                            # Debug logging
                            unreal.log(
                                "Row "
                                + str(row_index + 1)
                                + ": Change detected - "
                                + str(block_name)
                                + "."
                                + str(prop_name)
                                + " (type="
                                + str(prop_type_label)
                                + ")"
                            )

                    except Exception as ex:
                        errors.append(
                            "Row "
                            + str(row_index + 1)
                            + ": Error probing "
                            + str(block_name)
                            + "."
                            + str(prop_name)
                            + " - "
                            + str(ex)
                        )

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
                    # ignore; add_component_data_entry might fail if already present
                    pass

                for prop_name, prop_type_label, new_val, current_value, original_input in prop_changes:
                    success, error_msg = _apply_property_change(
                        ides, item_def, block_name, prop_name,
                        prop_type_label, new_val, current_value, original_input
                    )

                    if success:
                        modified_assets.add(asset.get_path_name())
                        unreal.log(
                            "Row "
                            + str(row_index + 1)
                            + ": Set "
                            + str(block_name)
                            + "."
                            + str(prop_name)
                            + " = "
                            + str(new_val)[:50]
                            + ("..." if len(str(new_val)) > 50 else "")
                        )
                    else:
                        errors.append(
                            "Row "
                            + str(row_index + 1)
                            + ": Failed setting "
                            + str(block_name)
                            + "."
                            + str(prop_name)
                            + " - "
                            + str(error_msg)
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