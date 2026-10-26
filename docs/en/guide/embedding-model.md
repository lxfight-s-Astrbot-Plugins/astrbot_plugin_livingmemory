# Embedding Models and BGE-M3

LivingMemory uses both keywords and vectors to recall memories. An embedding model generates those vectors. It does not chat or summarize, but it determines whether a memory with a similar meaning can be found.

## What an embedding model does

An embedding model maps text to a fixed-length numeric vector; for example, BGE-M3 outputs 1024 floats. Texts with closer meanings usually have vectors pointing in similar directions. LivingMemory embeds memory content when writing, embeds the question when searching, and then finds the nearest memories in FAISS.

This leads to two rules:

- Embeddings handle paraphrases, synonyms and cross-language questions; keyword retrieval still provides exact matches.
- Indexing and querying must use the same model and dimensions. Vectors from different models are not comparable, so changing the model requires rebuilding the index.

## Why BGE-M3

[BAAI/bge-m3](https://siliconflow.cn/models/BAAI/bge-m3) is a multilingual text embedding model with strong Chinese semantic recall and support for long inputs. SiliconFlow serves it through an OpenAI-compatible Embeddings API at no charge, making it a practical starting point for LivingMemory. Free pricing, rate limits and availability may change, so use the console as the source of truth.

| Item | Value |
| --- | --- |
| API model ID | `BAAI/bge-m3` |
| Vector dimensions | `1024` |
| Maximum input | 8192 tokens |
| Languages | More than 100 languages |
| SiliconFlow pricing | Listed as free on the model page |

The numeric `modelId` shown on the model page or in an API listing is not the model name to enter here. Put `BAAI/bge-m3` in AstrBot's "Embedding Model" field.

## Configure AstrBot

### 1. Get a SiliconFlow API key

Sign in to the [SiliconFlow console](https://cloud.siliconflow.cn/account/ak) and create an API key. The key is shown once, so store it safely.

### 2. Create an Embedding Provider

Open AstrBot's "Model Providers" page (called "LLM Configuration" in older versions), switch to "Embedding", click "Add Model Provider", and choose **OpenAI Embedding**. Fill in these values:

| Field | Value |
| --- | --- |
| Provider ID | `openai_embedding` (use the exact value shown by AstrBot) |
| API Key | Your SiliconFlow API key |
| API Base URL | `https://api.siliconflow.cn/v1` |
| Embedding Model | `BAAI/bge-m3` |
| Embedding Dimensions | `1024` |
| Embedding Dimensions Mode | `auto` |

AstrBot generates the Provider ID and shows it in the provider list or configuration header. If the name already exists, AstrBot may generate `openai_embedding_1` or another suffix. Keep the generated value and do not confuse it with the model ID `BAAI/bge-m3`.

Save and enable the provider, then click "Test". The test sends one real Embedding API request; success confirms that the key, endpoint, model name and dimensions work.

### 3. Connect LivingMemory

Open the LivingMemory plugin configuration and set `provider_settings.embedding_provider_id` to the Provider ID shown by AstrBot, such as `openai_embedding`. Save and reload the plugin.

After a few conversation turns, run:

```text
/lmem status
/lmem search a keyword you remember
```

If status is healthy and search returns memories, the vector pipeline is working.

## Troubleshooting

- **401 or authentication failure**: Copy the API key again, remove extra spaces, and make sure it belongs to the current SiliconFlow account.
- **404**: The API Base URL should be `https://api.siliconflow.cn/v1`; do not include a specific endpoint path.
- **Dimension mismatch**: BGE-M3 always outputs 1024 dimensions, so set Embedding Dimensions to `1024`. If the old index came from another model, run `/lmem rebuild-index`.
- **429 or rate limiting**: While rebuilding the index, increase `index_rebuild_settings.request_delay` and keep the batch size small.

Changing the embedding model changes the vector space. A provider-fingerprint check triggers a full vector rebuild, or you can run `/lmem rebuild-index` manually. The graph index is rebuilt separately with `/lmem rebuild-graph` when needed. Avoid frequent model changes after choosing one.
