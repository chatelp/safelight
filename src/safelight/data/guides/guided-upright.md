# Lightroom's Guided Upright, and how Safelight uses it

Background for AI clients that straighten photos with Safelight.

## In Lightroom

- Guided Upright (Transform panel, `PerspectiveUpright` 5) corrects perspective from 2
  to 4 lines the user draws on the photo: lines meant to be vertical or horizontal.
- When guides are drawn or moved in Lightroom, it computes its own correction. With two
  vertical guides only, it shears the image: horizontals stay as they were, so right
  angles change. With two vertical and two horizontal guides, the correction is fully
  set by the guides.
- A correction written by Safelight stays as written until someone touches the guides
  in Lightroom.

## What Safelight does

- straighten_photos finds long straight lines on a preview rendered without any
  correction, keeps up to four as guides, and writes the guides plus a correction
  computed as a rotation of the camera: walls and prints keep their proportions.
- With two vertical and two horizontal guides, the correction is exact (like
  Lightroom's), with the proportions of the camera rotation.
- The automatic lines are sometimes wrong: shadows under panels, lines printed inside
  photos, ceiling rails, short segments. straighten_lines shows every line found,
  numbered, so that you can choose the right ones (chosen_lines).
- When the verticals you choose disagree with the automatic ones by more than about
  half a degree, the photo is left to the user (left_to_user).
- Corners left empty by a correction are not cropped.

## Good guides

- The long edges of things that really are vertical or horizontal and face the camera:
  door frames, panel edges, the edges of prints and frames.
- One vertical on each side of the photo, far apart. Two verticals close together give
  an unreliable correction (Safelight refuses them).
- For exhibitions: the top and bottom of one work facing the camera as horizontals.
