use std::env;
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::{Context, Result, bail};
use codex_features::Feature;
use codex_login::{AuthHeaders, CodexAuth};
use codex_models_manager::bundled_models_response;
use codex_protocol::config_types::{CollaborationMode, ModeKind, Settings};
use codex_protocol::openai_models::{ModelInfo, ModelsResponse};
use codex_protocol::protocol::{EventMsg, Op, ThreadSettingsOverrides};
use codex_protocol::user_input::UserInput;
use core_test_support::test_codex::test_codex;
use core_test_support::wait_for_event_with_timeout;
use reqwest::header::{AUTHORIZATION, HeaderMap, HeaderValue};
use tempfile::TempDir;
use wiremock::MockServer;

fn cloned_model(slug: &str, comp_hash: &str) -> ModelInfo {
    let models = bundled_models_response().expect("bundled models must parse");
    let mut model = models
        .models
        .into_iter()
        .find(|item| item.slug == "gpt-5.5")
        .or_else(|| {
            bundled_models_response()
                .expect("bundled models must parse")
                .models
                .into_iter()
                .next()
        })
        .expect("bundled model catalog must not be empty");
    model.slug = slug.to_string();
    model.display_name = slug.to_string();
    model.comp_hash = Some(comp_hash.to_string());
    model
}

fn user_turn(model: &str, text: &str) -> Op {
    Op::UserInput {
        items: vec![UserInput::Text {
            text: text.to_string(),
            text_elements: Vec::new(),
        }],
        final_output_json_schema: None,
        responsesapi_client_metadata: None,
        additional_context: Default::default(),
        thread_settings: ThreadSettingsOverrides {
            collaboration_mode: Some(CollaborationMode {
                mode: ModeKind::Default,
                settings: Settings {
                    model: model.to_string(),
                    reasoning_effort: None,
                    developer_instructions: None,
                },
            }),
            ..Default::default()
        },
    }
}

async fn wait_complete(codex: &Arc<codex_core::CodexThread>) -> Result<()> {
    let terminal = wait_for_event_with_timeout(
        codex,
        |event| matches!(event, EventMsg::TurnComplete(_) | EventMsg::Error(_)),
        tokio::time::Duration::from_secs(240),
    )
    .await;
    if let EventMsg::Error(error) = terminal {
        bail!("Codex turn failed: {}", error.message);
    }
    Ok(())
}

fn main() -> Result<()> {
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .thread_stack_size(16 * 1024 * 1024)
        .enable_all()
        .build()?
        .block_on(async_main())
}

async fn async_main() -> Result<()> {
    let scenario = env::var("GPT_RELAY_SCENARIO").context("GPT_RELAY_SCENARIO is required")?;
    if !matches!(scenario.as_str(), "C3" | "C4" | "C5") {
        bail!("Rust harness only supports C3, C4, and C5");
    }
    let proxy_url = env::var("GPT_RELAY_PROXY_URL").context("GPT_RELAY_PROXY_URL is required")?;
    let model = env::var("GPT_RELAY_MODEL").context("GPT_RELAY_MODEL is required")?;
    let codex_home = PathBuf::from(
        env::var("GPT_RELAY_CODEX_HOME").context("GPT_RELAY_CODEX_HOME is required")?,
    );
    let auth_token = env::var("GPT_RELAY_AUTH_TOKEN")
        .unwrap_or_else(|_| "synthetic-codex-backend-auth".to_string());
    let home = Arc::new(TempDir::new_in(&codex_home)?);

    let mut headers = HeaderMap::new();
    headers.insert(
        AUTHORIZATION,
        HeaderValue::from_str(&format!("Bearer {auth_token}"))
            .context("GPT_RELAY_AUTH_TOKEN is not a valid header value")?,
    );
    headers.insert(
        "x-gpt-relay-synthetic-auth",
        HeaderValue::from_static("true"),
    );
    let auth = CodexAuth::Headers(AuthHeaders::new(headers));
    let provider_name = if scenario == "C5" { "Relay" } else { "OpenAI" }.to_string();
    let initial_model = if scenario == "C4" {
        "relay-probe-old".to_string()
    } else {
        model.clone()
    };
    let proxy_url_for_config = proxy_url.clone();
    let model_for_config = model.clone();
    let initial_model_for_config = initial_model.clone();
    let provider_name_for_config = provider_name.clone();
    let scenario_for_config = scenario.clone();
    let mut builder = test_codex()
        .with_home(home)
        .with_auth(auth)
        .with_model(&initial_model)
        .with_config(move |config| {
            config.model_provider.name = provider_name_for_config;
            config.model_provider.base_url = Some(proxy_url_for_config);
            config.model = Some(initial_model_for_config.clone());
            config
                .features
                .enable(Feature::EnableRequestCompression)
                .expect("request compression feature must be mutable");
            config
                .features
                .enable(Feature::ConcurrentReasoningSummaries)
                .expect("reasoning summary feature must be mutable");
            if scenario_for_config == "C4" {
                config
                    .features
                    .enable(Feature::RemoteCompactionV2)
                    .expect("remote compaction v2 feature must be mutable");
                config.model_catalog = Some(ModelsResponse {
                    models: vec![
                        cloned_model("relay-probe-old", "relay-hash-old"),
                        cloned_model(&model_for_config, "relay-hash-current"),
                    ],
                });
            }
        });
    let dummy_server = MockServer::start().await;
    let test = builder.build(&dummy_server).await?;

    test.codex
        .submit(user_turn(
            &initial_model,
            "Reply with exactly RELAY_STAGE_ONE.",
        ))
        .await?;
    wait_complete(&test.codex).await?;

    if scenario == "C4" {
        core_test_support::submit_thread_settings(
            &test.codex,
            ThreadSettingsOverrides {
                model: Some(model.clone()),
                ..Default::default()
            },
        )
        .await?;
        test.codex
            .submit(user_turn(
                &model,
                "Reply with exactly RELAY_STAGE_TWO.",
            ))
            .await?;
        wait_complete(&test.codex).await?;
    }

    println!(
        "{}",
        serde_json::json!({
            "scenario": scenario,
            "provider_name": provider_name,
            "model": model,
            "completed": true
        })
    );
    Ok(())
}
