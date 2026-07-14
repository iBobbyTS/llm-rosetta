# Image Generation And Visual Inspection Test

This suite verifies the complete Codex-Rosetta image workflow: a model calls
`image_gen.imagegen`, Codex saves the generated artifact, the same model opens
that exact artifact through projected `view_image`, and the model describes
what it sees. It tests tool selection, Rosetta projection/reconstruction,
Images API execution, saved-path handoff, and visual input delivery. It is not
an image-quality or general vision benchmark.

## Mandatory prerequisites

Do not run this suite unless all of the following are already true for the same
actual upstream model and an equivalent projected `view_image` route and Tool
Profile. The earlier proof may use a different Codex-facing alias when its
tool-mode and image-input path are equivalent:

- projected `view_image` has passed a dedicated transport test such as
  `builtin_tools/04`;
- the actual upstream model supports visual input and is declared with image
  input capability in the Codex model catalog;
- the upstream model has passed a deterministic visual-recognition check such
  as `builtin_tools/06`, or equivalent evidence proves that it can interpret
  the image returned by `view_image`;
- the selected Tool Profile enables `image_gen.imagegen` and contains working
  OpenAI-compatible Images Base URL and Token values.

A text-only model, an unverified `view_image` route, or a route that merely
returns image metadata is not eligible for this test. Record the prerequisite
evidence in `artifacts/evaluation.json` before classifying the run.

## Scenario

- `01`: generate a new image for the exact scene `草坪上一只狗在跑`, take the
  saved artifact path from the image-generation result, call `view_image` on
  that same path, and describe the returned image in Chinese.

The outer test executor (a developer or development agent) makes the final
semantic decision. The description passes only when it clearly states all
three required concepts: a dog, grass or a lawn, and the dog running. Merely
repeating the generation prompt without a successful `view_image` result is a
failure.

## Required Codex configuration

Use a provider whose display name is exactly `OpenAI`, the bundled `Chat
Default` Tool Profile, and a Codex-facing catalog entry with the same tool mode
and image-input capability as the tested visual model. Gateway Logs must prove
the actual upstream model and the separate OpenAI-compatible Images executor.

Follow `EVALUATION.md` and write `artifacts/evaluation.json` for every run.
