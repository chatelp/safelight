# What Lightroom Classic lets a plugin control

This is Safelight's map of the Lightroom Classic plugin API: what a plugin can
read and change, what it cannot, and which Lightroom version each capability
needs. It drives the design of Safelight's tools.

- **Source:** Adobe Lightroom Classic SDK 16.0 (build 202611041403), API
  Reference and guides. The SDK itself is not redistributed here; download it
  from the Adobe Developer Console.
- **Not yet verified live.** Everything below comes from the documentation.
  Items marked *verify* need a check against a running Lightroom; the transport
  prototype (roadmap lot L1) includes that inspection.
- **Minimum version:** Safelight supports Lightroom Classic 14.0 and later.
  Capabilities introduced after 14.0 are listed with their version and must be
  feature-gated at runtime (`LrApplication.versionTable()`).

Legend: ✅ available · ⚠️ partial or constrained · ❌ not available

## Constraints that shape the design

1. **Two editing paths with different rules.**
   - *Catalog path* (`LrCatalog`, `LrPhoto`): works on any photo, in batches,
     without touching the UI. Writes must run inside a catalog write gate
     (`withWriteAccessDo` / `withProlongedWriteAccessDo`).
   - *Develop UI path* (`LrDevelopController`): masks, AI tools, Remove,
     ranges. It acts only on the **current photo**, and **only while the
     Develop module is active**. Safelight must switch module and select the
     photo first, and these operations are one photo at a time.
2. **No lookup by local ID.** There is no `getPhotoByLocalId`; the upstream code
   silently fell back to scanning the whole catalog. Safelight identifies
   photos by **UUID** (`getRawMetadata("uuid")`, `catalog:findPhotoByUuid`),
   or by path (`catalog:findPhotoByPath`).
3. **Batch reads exist.** `catalog:batchGetRawMetadata` and
   `batchGetFormattedMetadata` read many photos in one call: use them for large
   shoots instead of per-photo calls.
4. **Some operations act on the UI selection**, not on a list of photos
   (`createVirtualCopies`, `LrSelection.*`, `copySettingsOfActiveSelection`).
   Safelight sets the selection first with `catalog:setSelectedPhotos`, then
   restores the user's selection.
5. **Everything runs in Lightroom tasks.** Most calls must run inside
   `LrTasks` async tasks; HTTP calls inside
   `LrFunctionContext.postAsyncTaskWithContext`.
6. **Formatted metadata is localized display text** (a French Lightroom
   returns apertures as `ƒ / 8,0`). Safelight returns raw values
   (`getRawMetadata`) to AI clients and uses formatted values only for display.

## Transport (for the plugin ↔ hub link)

| Capability | API | Since | Status |
|---|---|---|---|
| Outgoing HTTP requests with per-phase timeout (long-poll) | `LrHttp.get`, `LrHttp.post` (any method, custom headers, timeout) | 1.3 | ✅ *verify latency* |
| Listening sockets | `LrSocket.bind` (one client per socket) | 6.0 | ⚠️ used upstream, replaced by HTTP |
| Plugin preferences (store hub token, port) | `LrPrefs` | — | ✅ |
| Read/write files | `io`, `LrFileUtils`, `LrPathUtils` | — | ✅ |
| Quit Lightroom | `LrApplication.shutdown` | 14.3 | ✅ |

## Catalog, search and selection

| Capability | API | Since | Status |
|---|---|---|---|
| Active photo, selection, "target" photos | `getTargetPhoto`, `getTargetPhotos`, `getMultipleSelectedOrAllPhotos` | 3.0 | ✅ |
| Change the selection | `catalog:setSelectedPhotos`, `LrSelection.*` | 3.0 | ✅ |
| Find by UUID or path | `findPhotoByUuid`, `findPhotoByPath` | 2.0 | ✅ |
| Search by criteria (same engine as smart collections) | `catalog:findPhotos` — rating, flag, label, folder, collection, any text, filename, format, title, caption, keywords, IPTC/EXIF text, capture/edit date, camera, serial, lens, ISO, GPS presence, location fields, creator, has adjustments, treatment, cropped, aspect ratio; combine with union / intersect / exclude | 2.0 | ✅ |
| Browse folders | `getFolders`, `getFolderByPath`, `LrFolder` | 3.0 | ✅ |
| Change what the grid shows | `setActiveSources`, `setViewFilter` | 3.0 | ✅ |
| Import files | `addPhoto`, `triggerImportFromPathWithPreviousSettings`, `triggerImportUI` | 2.0 / 4.0 | ✅ |
| Remove photos from the catalog (not from disk) | `LrSelection.removeFromCatalog` (acts on selection) | 14.3 | ✅ |
| Delete or move files on disk | none tracked by the catalog | — | ❌ |
| Create or edit stacks | read-only stack info in raw metadata | — | ❌ |
| Catalog path, backup at next shutdown | `getPath`, `LrApplication.backupAtNextShutdown` | 3.0 / 4.0 | ✅ |

## Metadata and keywords

| Capability | API | Since | Status |
|---|---|---|---|
| Read EXIF and file info (exposure, lens, dimensions, dates, GPS, format, bit depth, virtual copies, stacks, smart preview, edit count, exported flag…) | `getRawMetadata`, `getFormattedMetadata`, batch variants | 1.3+ | ✅ |
| Rating, flag, color label | `setRawMetadata` (`rating`, `pickStatus`, `label`) | 2.0 / 4.0 | ✅ |
| Title, caption, headline, copyright, creator, rights, full IPTC core and IPTC Extension / PLUS fields | `setRawMetadata` | 2.0 / 3.0 | ✅ |
| GPS coordinates, altitude, direction | `setRawMetadata` (`gps`, `gpsAltitude`, `gpsImgDirection`) | 4.0 / 6.0 | ✅ |
| Accessibility alt text and extended description | `setRawMetadata` (`altTextAccessibility`, `extDescrAccessibility`) | 13.2 | ✅ |
| Apply a metadata preset | `photo:applyMetadataPreset`, `LrApplication.metadataPresets` | 3.0 | ✅ |
| Keywords: create hierarchies, synonyms, export flag, add/remove on photos, browse tree | `catalog:createKeyword`, `LrKeyword.*`, `photo:addKeyword` / `removeKeyword` | 3.0 | ✅ |
| Move a keyword in the hierarchy | `keyword:setParent` | 13.0 | ✅ |
| Rename or delete a keyword | not in the API | — | ❌ *verify* |
| Face regions / people tagging | only the `personShown` text field | — | ❌ |
| Plugin-private metadata (Safelight's own fields, searchable) | `setPropertyForPlugin`, `findPhotosWithProperty` | 2.0 | ✅ |
| Reverse geocoding (GPS → place names) | not exposed (Map module internal) | — | ❌ needs an external service |

## Develop: global settings

| Capability | API | Since | Status |
|---|---|---|---|
| Read all develop settings of a photo | `photo:getDevelopSettings` | 3.0 | ✅ |
| Write develop settings on one photo, named history step | `photo:applyDevelopSettings` | 6.0 | ✅ |
| Write develop settings on many photos in one batch, with progress | `catalog:applyDevelopSettings` | 16.0 | ✅ gate |
| Read a slider's range and value; increment / decrement | `LrDevelopController.getRange`, `getValue`, `setValue`, `increment` | 6.0 | ✅ Develop UI path |
| Auto tone, auto white balance | `LrDevelopController.setAutoTone`, `setAutoWhiteBalance` (current photo); `optFlattenAutoNow` on apply | 7.4 / 6.0 | ✅ |
| Process version | `getProcessVersion`, `setProcessVersion` | 6.0 | ✅ |
| Reset all, crop, transforms, healing, red eye, masks, single slider | `resetAllDevelopAdjustments`, `resetCrop`, `resetTransforms`, `resetHealing`, `resetRedeye`, `resetMasking`, `resetToDefault` | 6.0–12.0 | ✅ Develop UI path |
| Copy / paste settings between photos (choose groups, merge or replace masks) | `photo:copySettings` / `pasteSettings` (10.3), `catalog:pasteSettings` (15.3), `catalog:copySettingsOfActiveSelection` (16.0), groups in `LrDevelopSettingsGroups` | 10.3+ | ✅ gate for 15.3 / 16.0 |
| Develop presets: list, read contents, apply with amount | `LrApplication.developPresetFolders`, `developPresetByUuid`, `photo:applyDevelopPreset` | 3.0 | ✅ |
| Create a develop preset | only hidden presets inside the plugin (`addDevelopPresetForPlugin`) | 3.0 | ⚠️ not visible in the Presets panel |
| Snapshots: create, update, list, apply, delete | `createDevelopSnapshot`, `getDevelopSnapshots`, `applyDevelopSnapshot`, `deleteDevelopSnapshot` | 3.0–5.0 | ✅ |
| Undo / redo | `LrUndo.undo`, `redo`, `canUndo`, `canRedo` | 6.0 | ✅ |
| Read the history step list | not in the API | — | ❌ (use snapshots) |
| Pause history recording during bulk edits | `catalog:recordDevelopHistory` | 16.0 | ✅ gate |
| Know whether a photo is locked by a background AI job | `getPhotosAvailableForEditing`, `photo:isAvailableForEditing` | 15.3 | ✅ gate |
| Histogram or clipping statistics | only a UI toggle (`showClipping`) | — | ❌ compute from previews |

## Develop: masks and local adjustments

| Capability | API | Since | Status |
|---|---|---|---|
| Create a mask: brush, linear gradient, radial gradient, range (color, luminance, depth), AI (subject, sky, background, objects, people, landscape) | `LrDevelopController.createNewMask(type, subtype)` | 11.0 | ✅ Develop UI path |
| Add / subtract / intersect a component | `addToCurrentMask`, `subtractFromCurrentMask`, `intersectWithCurrentMask` | 11.0 | ✅ |
| List, select, delete, hide, invert, duplicate-and-invert masks and tools | `getAllMasks`, `getSelectedMask`, `selectMask`, `deleteMask`, `toggleHideMask`, `invertMask`, `duplicateAndInvertMask`, `…MaskTool` variants | 11.0 / 11.4 | ✅ |
| Set local adjustment sliders of the selected mask | `setValue("local_Exposure", …)` and other `local_*` params | 11.0 | ✅ *verify param list* |
| Place a gradient or paint a brush stroke at given coordinates | no controller call; geometry may be writable through the mask groups of `applyDevelopSettings` | — | ⚠️ *verify* |
| Delete empty masks in bulk | `catalog:deleteAllEmptyMasks` | 14.0 | ✅ |
| Refresh AI masks after edits | `updateAISettings` (catalog and photo), `needsUpdateAISettings` | 13.3 / 15.3 | ✅ |
| Point color swatches | `addPointColorSwatch`, `updateSelectedPointColorSwatch`, … | 13.2 | ✅ |
| Lens blur: bokeh shape, depth visualization | `setLensBlurBokeh`, `getSelectedLensBlurBokeh`, `toggleLensBlurDepthVisualization` | 13.3 | ✅ |

## Develop: AI and retouching tools

| Capability | API | Since | Status |
|---|---|---|---|
| Denoise, Raw Details, Super Resolution | `setEnhance` (15.3); `toggleEnhance` (14.5, deprecated), `changeDenoiseAmount`, `getEnhancePanelState` | 14.5 / 15.3 | ✅ gate |
| Reflection removal | `toggleReflectionRemoval`, `changeReflectionRemovalAmount`, `changeReflectionRemovalQuality` | 14.5 | ✅ gate |
| Remove distracting people | `detectDistractingPeople`, `applyRemovalOnDetectedDistractingPeople` | 14.5 | ✅ gate |
| Remove tool: heal / clone / content-aware with optional generative AI, list and edit spots | `goToRemove`, `setSelectedSpotType`, `getAllSpots`, `setSelectedSpotParams`, … | 14.1 | ⚠️ edits existing spots; creating a spot at given coordinates *verify* |
| Eye correction | `goToEyeCorrection` | 14.1 | ⚠️ opens the tool only |
| Edit in Photoshop | `editInPhotoshop` | 7.4 | ✅ |
| HDR / panorama merge | not in the API | — | ❌ |

## Organize

| Capability | API | Since | Status |
|---|---|---|---|
| Collections and collection sets: create, rename, move, delete, add/remove photos | `createCollection`, `createCollectionSet`, `LrCollection.*` | 3.0 | ✅ |
| Smart collections: create, read and change rules | `createSmartCollection`, `getSearchDescription`, `setSearchDescription` | 3.0 / 5.0 | ✅ |
| Virtual copies | `catalog:createVirtualCopies` (acts on selection) | 5.0 | ✅ |
| Rotate | `photo:rotateLeft`, `rotateRight` | 7.4 | ✅ |
| Smart previews: build, delete | `buildSmartPreviews`, `photo:deleteSmartPreview` | 5.0 | ✅ |
| Publish services: read services and published collections | `getPublishServices`, `LrPublishService`, `LrPublishedCollection` | 3.0 | ✅ |

## Previews and export

| Capability | API | Since | Status |
|---|---|---|---|
| Rendered JPEG preview at a requested size (with edits) | `photo:requestJpegThumbnail(width, height, callback)` | 5.0 | ✅ *verify speed on large shoots* |
| Export with full control of format, size, color space, sharpening, metadata, renaming, watermark | `LrExportSession` with `LR_*` export settings | 1.3 | ✅ |
| File-naming presets | `LrApplication.filenamePresets`, `photo:getNameViaPreset` | 3.0 | ✅ |
| Open Lightroom's own export dialog | `photo:openExportDialog` | 7.4 | ✅ |

## Interface

| Capability | API | Since | Status |
|---|---|---|---|
| Switch module, show Grid / Loupe / Compare / Survey, zoom, secondary display | `LrApplicationView.*` | 6.0 / 7.4 | ✅ |
| Reveal a Develop panel | `revealPanel`, `revealPanelIfVisible` | 6.0 / 8.1 | ✅ |
| Dialogs, progress bars, notifications | `LrDialogs`, `LrProgressScope` | — | ✅ |
| React to Develop changes made by the user | `addAdjustmentChangeObserver` | 6.0 | ✅ |
| Tethered capture: start, stop, trigger a shot | `LrTether.*` | 6.0 | ✅ |

## Impact on the Safelight plan

- **Possible as planned:** reliable HTTP transport, UUID-based photo addressing,
  batch metadata, natural-language search through `findPhotos` and smart
  collections, previews for vision and contact sheets, exports with delivery
  profiles, GPS writing, copy/paste settings, snapshots, undo.
- **Needs the Develop UI path (one photo at a time):** masks, AI tools,
  slider ranges. Tools must say so, and long sequences must be queued as jobs.
- **Gate by Lightroom version:** batch develop apply and history pause (16.0),
  paste/lock detection/Enhance (15.3), Denoise and other AI removal (14.5),
  remove from catalog and quit (14.3), Remove tool (14.1).
- **Not possible through the API:** deleting or renaming files on disk,
  creating stacks, face tagging, reading the history list, histogram data,
  HDR/panorama merge, reverse geocoding. Safelight either computes these itself
  (histograms from previews) or leaves them out.
- **New opportunities found in the SDK:** driving Lightroom's AI tools
  (Denoise, reflection and people removal), tethered capture, and observing the
  user's own Develop changes (useful for learning their style).
