Straighten the photos of an exhibition (prints and panels hung on walls) in the Lightroom
folder "{folder}", the way the user expects.

1. Call safelight_status. Lightroom must be connected. Note the user's color for changed
   photos and whether snapshots are on: Safelight applies both by itself.
2. Find the photos with search_photos, filters {{"folder": "{folder}"}}, and look at them
   with contact_sheet. Only the top photo of a stack is straightened (the frames under an
   HDR merge are skipped).
3. Leave alone the photos the user validated and the photos that already have an Upright
   correction (Safelight skips both): it may be the user's own work. Use
   replace_existing_upright only if the user asks for exactly that.
4. Run straighten_photos with filters {{"folder": "{folder}"}} and check_only: it changes
   nothing and says how many photos it would straighten and which it would skip (already
   corrected, validated, rotated). The photo descriptions do not show this, so do not
   count yourself. Tell the user "up to" that number (a photo with no clear straight line
   is left as it is) and ask before changing anything. If there is nothing to straighten,
   or Safelight is read-only, say so and stop.
5. Run straighten_photos with the same filters, without check_only, and with guides
   "verticals_and_frontal".
6. Check every result:
   - look at the straightened photos with contact_sheet;
   - for photos with warnings, and any that still look wrong, call straighten_lines and
     choose the guides yourself: the long edges of the hung works, that is the top and
     bottom of panels and the edges of prints and frames. Take one vertical on each side
     of the photo, plus the top and bottom edges of one work facing the camera when there
     is one: with four guides the correction is exact.
   - never choose shadows under panels, lines printed inside the photos, ceiling rails or
     lights that are not on the wall, or short segments; nor the edges of prints standing
     on a shelf or ledge: they lean back, so they are not vertical. When a view has mostly
     such prints and few true verticals (wall corners, door frames), the automatic
     correction turns it wrongly: leave it to the user;
   - call straighten_photos again with chosen_lines. For photos listed in left_to_user,
     tell the user: they draw the guides in Lightroom (Transform panel, Guided).
   - a photo Safelight made worse and no choice of lines can fix: put it back with
     undo_straighten, never by changing its transform yourself. It gets back its settings
     from before, its Geometry field says "To straighten by hand" (so Safelight leaves it
     alone next time) and it goes to the app's list of photos to check.
7. Thin white or black borders on HDR merges come from merging without Lightroom's Auto
   Crop. Do not crop them unless the user asks.
8. Report how many photos were straightened, which ones need the user's eye, and that
   they carry the user's color. Suggest that the user ticks Validated (Metadata panel,
   Safelight set) on the photos they approve: Safelight then never changes them again.
