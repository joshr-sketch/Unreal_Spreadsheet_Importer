#include "SpreadsheetImporterModule.h"
#include "Interfaces/IPluginManager.h"
#include "HAL/PlatformFileManager.h"
#include "Misc/Paths.h"

#define LOCTEXT_NAMESPACE "FSpreadsheetImporterModule"

void FSpreadsheetImporterModule::StartupModule()
{
	// Install Forge panel files on editor startup
	InstallForgePanel();

	UE_LOG(LogTemp, Log, TEXT("SpreadsheetImporter: Module loaded"));
}

void FSpreadsheetImporterModule::ShutdownModule()
{
}

FString FSpreadsheetImporterModule::GetPluginContentDir() const
{
	// Get the plugin's Content directory
	TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("SpreadsheetImporter"));
	if (Plugin.IsValid())
	{
		return FPaths::Combine(Plugin->GetContentDir());
	}
	return FString();
}

void FSpreadsheetImporterModule::InstallForgePanel()
{
	// Source: Plugin's Content/Forge/Spreadsheet_Importer/
	FString PluginContentDir = GetPluginContentDir();
	if (PluginContentDir.IsEmpty())
	{
		UE_LOG(LogTemp, Warning, TEXT("SpreadsheetImporter: Could not find plugin content directory"));
		return;
	}

	FString SourceDir = FPaths::Combine(PluginContentDir, TEXT("Forge"), TEXT("Spreadsheet_Importer"));

	// Destination: Project's Saved/Forge/tools/Spreadsheet_Importer/
	FString DestDir = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("Forge"), TEXT("tools"), TEXT("Spreadsheet_Importer"));

	// Create destination directory if it doesn't exist
	IPlatformFile& PlatformFile = FPlatformFileManager::Get().GetPlatformFile();
	if (!PlatformFile.DirectoryExists(*DestDir))
	{
		PlatformFile.CreateDirectoryTree(*DestDir);
	}

	// Files to copy
	TArray<FString> FilesToCopy = { TEXT("tool.js"), TEXT("tool.css"), TEXT("tool.json") };

	for (const FString& FileName : FilesToCopy)
	{
		FString SourceFile = FPaths::Combine(SourceDir, FileName);
		FString DestFile = FPaths::Combine(DestDir, FileName);

		// Only copy if source exists and destination doesn't (or source is newer)
		if (PlatformFile.FileExists(*SourceFile))
		{
			FDateTime SourceTime = PlatformFile.GetTimeStamp(*SourceFile);
			FDateTime DestTime = PlatformFile.GetTimeStamp(*DestFile);

			if (!PlatformFile.FileExists(*DestFile) || SourceTime > DestTime)
			{
				if (PlatformFile.CopyFile(*DestFile, *SourceFile))
				{
					UE_LOG(LogTemp, Log, TEXT("SpreadsheetImporter: Installed %s"), *FileName);
				}
				else
				{
					UE_LOG(LogTemp, Warning, TEXT("SpreadsheetImporter: Failed to copy %s"), *FileName);
				}
			}
		}
		else
		{
			UE_LOG(LogTemp, Warning, TEXT("SpreadsheetImporter: Source file not found: %s"), *SourceFile);
		}
	}
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FSpreadsheetImporterModule, SpreadsheetImporter)
