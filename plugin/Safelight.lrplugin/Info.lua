-- Safelight for Lightroom Classic. Not affiliated with Adobe.
-- VERSION is written by scripts/sync_version.py from the repository's VERSION file.
return {
    LrSdkVersion = 14.0,
    LrSdkMinimumVersion = 14.0,

    LrToolkitIdentifier = "io.github.chatelp.safelight",
    LrPluginName = "Safelight",
    LrPluginInfoUrl = "https://github.com/chatelp/safelight",

    LrInitPlugin = "PluginInit.lua",
    LrForceInitPlugin = true,
    LrShutdownPlugin = "PluginShutdown.lua",
    LrShutdownApp = "PluginShutdown.lua",

    -- Safelight's fields on each photo (validated, geometry, reject reason).
    LrMetadataProvider = "MetadataDefinition.lua",
    LrMetadataTagsetFactory = "Tagset.lua",

    LrExportMenuItems = {
        {
            title = "Safelight Status",
            file = "MenuStatus.lua",
        },
    },

    VERSION = { major = 0, minor = 1, revision = 0, build = 0, display = "0.1.0.dev0" },
}
