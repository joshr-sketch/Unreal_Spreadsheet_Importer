# Project Context

An Unreal Editor Utility Widget that:
- Accepts pasted TSV data from a spreadsheet
- Imports that data directly into a DataTable
- Patches ItemDefinition assets using special spreadsheet columns prefixed with `%`

# Column Prefixes

| Prefix | Behavior |
|--------|----------|
| `#` | Column is skipped (not imported to DataTable) |
| `%` | Patch column - contains JSON to patch the asset in the matching target column |

The `%` column targets a column with the same name (minus the `%`). Example:
- `%Item Definition` targets `Item Definition`
- `%#CollectionWID` targets `#CollectionWID` (the `#` column is skipped but still used as patch target)

# JSON Patch Format

Patches use JSON with friendly block names:

```json
{"ItemName": "Lucky Stone", "ItemDescription": "A stone that brings good fortune."}
{"MaxStackSize": {"MaxStackSize": 25}}
{"Pickup": {"bCanBeDroppedFromInventory": false}}
{"Tags": {"Tags": ["Husky.Item.Resource", "Husky.Item.Favorite"]}}
{"CampStructure": {"CampStructureId": "Husky.Structure.BurrBaby-Essence"}}
```

## Base Properties (ItemDefinitionBase)

These properties are set directly on the ItemDefinition, not in the DataList:

| Friendly Name | Property |
|---------------|----------|
| `ItemName` / `Item Name` / `Name` | ItemName (FText) |
| `ItemDescription` / `Item Description` / `Description` | ItemDescription (FText) |
| `ItemShortDescription` / `Item Short Description` / `Short Description` / `ShortDescription` | ItemShortDescription (FText) |
| `ReleaseVersion` / `Release Version` / `FortReleaseVersion` | ReleaseVersion (FortReleaseVersion struct) |

Base properties take simple string values (not nested objects):
```json
{"ItemName": "Lucky Stone", "ItemDescription": "A lucky stone.", "ReleaseVersion": "Future"}
```

ReleaseVersion accepts either a simple string or a dict:
```json
{"ReleaseVersion": "Future"}
{"ReleaseVersion": {"VersionName": "Future"}}
```

## Supported Friendly Names (Component Data Blocks)

| Friendly Name | Full Script Path |
|---------------|------------------|
| `MaxStackSize` | `/Script/ItemizationCoreRuntime.ItemComponentData_MaxStackSize` |
| `Pickup` | `/Script/FortniteGame.FortItemComponentData_Pickup` |
| `Traits` | `/Script/ItemizationCoreRuntime.ItemComponentData_Traits` |
| `Tags` / `OwnedGameplayTags` | `/Script/ItemizationCoreRuntime.ItemComponentData_OwnedGameplayTags` |
| `Icon` | `/Script/ItemizationCoreRuntime.ItemComponentData_Icon` |
| `OwnerPose` | `/Script/ItemizationCoreRuntime.ItemComponentData_OwnerPose` |
| `CampStructure` | `/Script/HuskyGameplayRuntime.HuskyItemComponentData_CampStructure` |

Partial matching also works - any name that ends a component path (e.g., `_MaxStackSize`) will resolve automatically.

Full script paths still work for backwards compatibility.

## Property Types

The script auto-detects property types and handles:
- **scalable_float**: `{"MaxStackSize": {"MaxStackSize": 25}}`
- **float**: `{"Pickup": {"MiniMapViewableDistance": 12.7}}`
- **bool**: `{"Pickup": {"bCanBeDroppedFromInventory": false}}`
- **gameplay_tag_container**: `{"Tags": {"Tags": ["Tag.One", "Tag.Two"]}}`
- **generic_text**: strings, enums, asset paths

# Change Detection

The script only checks out and saves assets when values actually change. If the new value matches the current value, no modification occurs - avoiding unnecessary source control operations.

# Files

| File | Purpose |
|------|---------|
| `importer_script_python.py` | Main script copied into Unreal Format node |
| `example_api_usage.py` | Example of the ItemDefinitionEditorSubsystem API |
| `example_input.tsv` | Sample input data |

# Constraints

## CRITICAL: Curly Brace Restrictions

This script runs inside an Unreal Blueprint Format node. The Format node interprets curly braces `{}` as format placeholders and **DELETES anything inside them**.

**FORBIDDEN patterns (will silently break the script):**
- Dict literals: `my_dict = {"key": "value"}` - BROKEN, becomes `my_dict = {}`
- F-strings: `f"Row {index}"` - BROKEN, becomes `f"Row "`
- Any `{content}`: `print("{hello}")` - BROKEN

**ALLOWED patterns:**
- Empty braces: `my_dict = {}` - OK
- Format variables: `{TSV}` and `{DTPath}` - OK (intentional placeholders)
- `dict()` constructor: `dict([("k", "v")])` - OK
- Tuples: `(("k", "v"),)` - OK

**Always use tuples or `dict()` constructor instead of dict literals!**

The only permitted curly brace placeholders are:
- `{TSV}` - the pasted TSV data
- `{DTPath}` - the DataTable asset path

### AI Agent Reminder

**When editing `importer_script_python.py`, NEVER use curly braces with content inside - not even in comments!**

Comments like `# Convert {"TagName": "foo"}` will have content stripped, becoming `# Convert {}`.

Use `dict()` notation in comments instead:
- BAD: `# Example: {"Key": "Value"}`
- GOOD: `# Example: dict(Key="Value")`

This applies to ALL text in the file, including strings, comments, and docstrings.
