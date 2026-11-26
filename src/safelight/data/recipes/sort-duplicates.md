Help the user keep the best photo of each group of near-duplicates or bursts in the
Lightroom folder "{folder}".

1. Call safelight_status. Lightroom must be connected.
2. Run analyze_photos on the folder (filters {{"folder": "{folder}"}}) to find
   near-duplicates and bursts.
3. For each group, look at it with contact_sheet, then at the candidates with
   preview_photo at a size where details show. Sharpness alone does not choose the best
   frame: look for lens flare, passers-by or people walking into the frame, closed eyes
   and expressions, reflections, a tilted horizon and the framing. In groups marked
   choose_by_eye the frames are about as sharp as each other: the moment decides (a
   face turned to the camera, a gesture, the light on the scene, a figure that tells the
   story).
4. Propose which frame to keep in each group, and why. The user decides: reject nothing
   before they answer.
5. For the frames the user rejects, use set_metadata with pick "rejected", and say why in
   your report ("duplicate of _DSC1234: passer-by", "flare"). Never touch the frames
   under an HDR merge.
6. Safelight never deletes photos: the user removes rejected photos in Lightroom if they
   want to.
7. Report the frames kept and rejected, group by group.
