# Shift and tilt-shift lenses

Background for AI clients working on photos made with a shift lens (Canon TS-E, Nikon
PC-E or PC Nikkor, Laowa shift lenses...), often used for architecture and exhibitions.

- A shift lens moves the image circle instead of tilting the camera: verticals stay
  parallel without leaning back, so these photos often need little or no straightening.
  When they do need it, the correction is usually small.
- The optical centre is then no longer at the centre of the image. Safelight's automatic
  correction models a rotation of the camera around the image centre, so on shifted
  photos it is an approximation. Four guides (two vertical, two horizontal) make the
  correction exact whatever the shift: prefer them.
- Adapted or manual lenses are often named wrongly in Lightroom (the adapter reports
  another lens), and Lightroom's lens profile may then be the wrong one. The user can
  give the real name in Safelight's preferences: Safelight then shows it and finds the
  photos by either name. Do not change lens corrections without asking the user.
- Tilt (rotating the focus plane) changes what is sharp, not the perspective: a sharp
  band across a photo can be intended.
