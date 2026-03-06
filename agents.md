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
{"MaxStackSize": {"MaxStackSize": 25}}
{"Pickup": {"bCanBeDroppedFromInventory": false}}
{"Tags": {"Tags": ["Husky.Item.Resource", "Husky.Item.Favorite"]}}
{"CampStructure": {"CampStructureId": "Husky.Structure.BurrBaby-Essence"}}
```

## Supported Friendly Names

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

Curly brackets `{}` in Unreal blueprints are interpreted as script inputs. Only use them for:
- `{TSV}` - the pasted TSV data
- `{DTPath}` - the DataTable asset path
