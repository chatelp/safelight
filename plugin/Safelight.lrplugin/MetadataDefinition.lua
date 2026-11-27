-- Safelight's own fields on each photo, kept in the catalog (Metadata panel, "Safelight"
-- set). They hold facts about one photo that every AI client must respect:
--   geometry   who corrected the perspective last: Safelight, or the user; or that
--              Safelight put it back and the user straightens it by hand;
--   validated  the user approved the photo's edits: Safelight never changes them;
--   rejectReason  why the photo was rejected (duplicate, passer-by, flare...);
--   safelightLabel  (hidden) the color label Safelight gave the photo, or "user" once
--              the user changed it, so that Safelight only ever changes its own colors.
-- Changing these definitions needs a new schemaVersion; Lightroom then asks once to
-- update the catalog.
return {
    metadataFieldsForPhotos = {
        {
            id = "geometry",
            title = "Geometry",
            dataType = "enum",
            values = {
                { value = nil, title = "Not set" },
                { value = "safelight", title = "Straightened by Safelight" },
                { value = "user", title = "Corrected by you" },
                { value = "by_hand", title = "To straighten by hand" },
            },
            browsable = true,
            searchable = true,
        },
        {
            id = "validated",
            title = "Validated",
            dataType = "enum",
            values = {
                { value = nil, title = "No" },
                { value = "yes", title = "Yes: Safelight leaves its edits alone" },
            },
            browsable = true,
            searchable = true,
        },
        {
            id = "rejectReason",
            title = "Reject reason",
            dataType = "string",
            browsable = true,
            searchable = true,
        },
        {
            -- No title: not shown in the Metadata panel.
            id = "safelightLabel",
            dataType = "string",
        },
    },
    schemaVersion = 3,
}
