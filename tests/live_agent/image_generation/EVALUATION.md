# Image Generation And Visual Inspection Evaluation

This file guides the outer developer or development agent. Do not include it
in the tested model's prompt.

## Required evidence

Use all three bounded evidence sources:

1. `artifacts/codex.jsonl` for exit status, thread id, visible tool activity,
   and the final result line.
2. The matching rollout under `codex_home/sessions` for native image-generation
   events, the saved artifact path, the native `view_image` call, and the image
   result delivered back to the model.
3. Gateway Logs for the actual upstream model, model-facing projected calls,
   reconstructed Codex calls, the Images endpoint/executor, and terminal stream
   completion.

Before invoking the model, record credential-free proof that the isolated
`auth.json` came from `/Users/ibobby/.codex-multi-2/auth.json`, its mode is
ChatGPT OAuth, `codex login status` recognizes that mode, and the configured
`codex_rosetta` Provider still uses its isolated Gateway bearer credential.
Do not record token values or copy the auth file into artifacts.

Never put image base64, credentials, full prompts, or complete trace records in
the evaluation artifact.

## Success decision

The run passes only when every condition below is true:

- the mandatory `view_image` and visual-model prerequisites from `README.md`
  are recorded as passed before this scenario runs;
- the model makes exactly one `image_gen.imagegen` call for a brand-new image
  using the exact prompt `草坪上一只狗在跑`;
- Rosetta reconstructs that call into the Codex `image_gen.imagegen` runtime,
  the configured OpenAI-compatible generation endpoint succeeds, and Codex
  saves a generated image artifact;
- the model takes the saved artifact path from that result and makes exactly
  one projected `view_image` call with `detail: "original"` on the same path;
- the native `view_image` result contains image content rather than text-only
  metadata;
- the final line begins with `RESULT:IMAGE_GENERATION_DESCRIPTION|`;
- the outer developer or development agent judges the description to clearly
  include a dog, grass or a lawn, and the dog running.

If tool execution succeeds but the description misses any required concept,
classify the run as `failure` with `semantic_judgment: "failed"`. Harmless
extra prose inside the one-sentence description is not a failure. Any command,
browser, direct file read, image conversion, or alternate image-inspection tool
is a prohibited fallback.

## Required result file

Write `artifacts/evaluation.json` with this shape:

```json
{
  "classification": "success | success with deviations | failure | runner_auth_not_supported",
  "task_id": "01",
  "target_scope": "image_generation_then_view_image",
  "model": "Codex-facing model alias",
  "provider_identity": "codex_rosetta",
  "provider_display_name": "OpenAI",
  "upstream_model": "model proven by Gateway Logs",
  "thread_id": "Codex thread id",
  "rollout_path": "isolated rollout path",
  "process_exit_code": 0,
  "prerequisites": {
    "view_image_transport": "passed",
    "visual_recognition": "passed",
    "vision_capable_model": true,
    "image_generation_profile_configured": true,
    "auth_source": "/Users/ibobby/.codex-multi-2/auth.json",
    "codex_auth_mode": "chatgpt_oauth",
    "codex_login_status_verified": true,
    "provider_request_auth": "experimental_bearer_token",
    "codex_image_generation_auth_gate": "passed | runner_auth_not_supported",
    "evidence": ["bounded references to prior or current prerequisite evidence"]
  },
  "result_prefix_observed": true,
  "failure_marker_observed": false,
  "image_generation_model_facing_available": true,
  "description": "one bounded sentence returned by the tested model",
  "semantic_judgment": "passed | failed | not_reached",
  "semantic_concepts": {
    "dog": true,
    "grass_or_lawn": true,
    "running": true
  },
  "model_facing_calls": ["observed names in order"],
  "native_calls": ["observed native calls in order"],
  "generation_endpoint": "images/generations | not_called",
  "model_request_reached_isolated_gateway": true,
  "images_request_reached_isolated_gateway": true,
  "generated_artifact_path": "credential-free isolated path or null",
  "view_image_used_generated_path": true,
  "successful_image_results": true,
  "prohibited_fallback_calls": 0,
  "gateway_log_evidence": [
    {
      "stage": "bounded Gateway Logs stage",
      "request_id": "request id when available",
      "observation": "short credential-free structural observation"
    }
  ],
  "stream_completed": true,
  "warning": null
}
```
