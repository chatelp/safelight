-- A "Safelight" choice in the Metadata panel's menu: the usual fields and Safelight's.
return {
    id = "safelightTagset",
    title = "Safelight",
    items = {
        { "com.adobe.label", label = "Photo" },
        "com.adobe.filename",
        "com.adobe.title",
        "com.adobe.caption",
        "com.adobe.separator",
        { "com.adobe.label", label = "Safelight" },
        "io.github.chatelp.safelight.validated",
        "io.github.chatelp.safelight.geometry",
        "io.github.chatelp.safelight.rejectReason",
    },
}
