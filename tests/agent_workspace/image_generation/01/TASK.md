Test image generation followed by projected `view_image` in this exact order.

1. Call `image_gen.imagegen` exactly once to generate a brand-new image using
   the exact prompt `草坪上一只狗在跑`. Omit both `referenced_image_paths` and
   `num_last_images_to_include`.
2. From the successful image-generation result, obtain the path where Codex
   saved the generated artifact. Call `view_image` exactly once with that exact
   path and detail `original`.
3. Based only on the image returned by `view_image`, describe the scene in one
   concise Chinese sentence.

Do not use a command, browser, direct file read, image conversion, another
image tool, or the generation prompt itself as a substitute for inspecting the
`view_image` result.

If both required tools succeed, reply with exactly one line in this format:

`RESULT:IMAGE_GENERATION_DESCRIPTION|<你的中文图片描述>`

If either required tool fails, reply with only:

`RESULT:IMAGE_GENERATION_FAILED`
