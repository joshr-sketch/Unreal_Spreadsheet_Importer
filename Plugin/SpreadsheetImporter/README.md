# Spreadsheet Importer Plugin

Import Google Sheets data into Unreal DataTables via Hodor MCP.

## Features

- **Forge Panel UI** - Visual interface for selecting spreadsheets and tabs
- **Batch Import** - Queue multiple tabs and import them all at once
- **Presets** - Save frequently-used import configurations
- **Auto-detect DataTable** - Reads cell A1 to determine target DataTable
- **Column Matching** - Case-insensitive, space-tolerant column name matching
- **Team-friendly** - Uses Hodor MCP for Google API access (no personal credentials needed)

## Prerequisites

1. **MCP Client Toolset** configured with Hodor:
   - Name: `Hodor`
   - ServerUrl: `https://hodor.on.epicgames.com/mcp`
   - Transport: `Streamable HTTP`
   - Auth: `OAuth 2.0 (Authorization Code + PKCE)`
   - OAuthScope: `mcp:tools`

2. **Python Script Plugin** enabled

## Installation

1. Copy the `SpreadsheetImporter` folder to your project's `Plugins/` directory
2. Restart the Unreal Editor
3. Enable the plugin in Edit → Plugins → "Spreadsheet Importer"
4. The Forge panel will be auto-installed on next editor startup

## Usage

1. Open the Forge panel: **Spreadsheet Importer** (under Tools|Data)
2. First time only: Click **"Authorize Google"** and complete OAuth in browser
3. Select a spreadsheet from the dropdown
4. Add tabs to the import queue
5. Click **"Import Queue"**

## Spreadsheet Format

- **Cell A1**: DataTable asset name (e.g., `DT_MyDataTable`)
- **Row 1**: Column headers (matched to DataTable properties)
- **Row 2+**: Data rows

Column names are matched case-insensitively with spaces removed, so "Collection Id" matches "CollectionId".

## Troubleshooting

**"0 spreadsheets found"**
- Click "Authorize Google" to connect your Google account
- Make sure your spreadsheet name contains "TSV" (or modify the filter)

**"Could not find DataTable matching 'X'"**
- Verify the DataTable exists in your project
- Check that cell A1 contains the correct DataTable name

**Import errors**
- Check that column headers match DataTable property names
- Verify data types match (numbers, strings, etc.)

## Files

- `Content/Forge/Spreadsheet_Importer/` - Forge panel UI
- `Content/Python/tsv_import_toolset.py` - MCP toolset for DataTable operations
