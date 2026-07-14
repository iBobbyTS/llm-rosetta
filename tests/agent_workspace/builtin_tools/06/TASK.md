Test projected `view_image` and visual recognition using the provided fixture.

1. Call `view_image` exactly once with path
   `fixtures/vision_quadrants.png` and detail `original`.
2. Inspect the returned image and determine the solid color in each quadrant.
3. Do not read the image file, inspect its bytes or metadata, convert it, or use
   another file, command, image, or browser tool.

If and only if the image shows red in the top-left, green in the top-right,
blue in the bottom-left, and yellow in the bottom-right, reply with exactly:

`RESULT:VIEW_IMAGE_RECOGNITION_OK|top_left=red|top_right=green|bottom_left=blue|bottom_right=yellow`

Otherwise reply with only `RESULT:VIEW_IMAGE_RECOGNITION_FAILED`.
