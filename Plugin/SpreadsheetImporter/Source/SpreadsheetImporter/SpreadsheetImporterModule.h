#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"

class FSpreadsheetImporterModule : public IModuleInterface
{
public:
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;

private:
	void InstallForgePanel();
	FString GetPluginContentDir() const;
};
