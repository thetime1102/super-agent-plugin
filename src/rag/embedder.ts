/**
 * rag/embedder.ts — API Embedding client
 * 
 * Supports OpenAI-compatible embedding APIs (ada-002, text-embedding-3-small, etc.)
 * Configurable via plugin config.
 * 
 * Default: text-embedding-ada-002 (1536 dimensions, cheap, fast)
 */

import { EmbeddingConfig } from './types.js';

// ─── Default config ───────────────────────────────

const DEFAULT_CONFIG: EmbeddingConfig = {
  apiKey: '',
  model: 'text-embedding-ada-002',
  baseUrl: 'https://api.openai.com/v1',
  dimension: 1536,
};

let _config: EmbeddingConfig = { ...DEFAULT_CONFIG };

export function configure(config: Partial<EmbeddingConfig>): void {
  _config = { ..._config, ...config };
}

export function getConfig(): EmbeddingConfig {
  return { ..._config };
}

// ─── API call ─────────────────────────────────────

interface EmbeddingResponse {
  data: { embedding: number[]; index: number }[];
  usage?: { prompt_tokens: number };
}

/**
 * Embed a single text string
 */
export async function embed(text: string): Promise<number[]> {
  return embedBatch([text]).then(r => r[0]);
}

/**
 * Embed multiple texts in one API call (batch)
 */
export async function embedBatch(texts: string[]): Promise<number[][]> {
  if (texts.length === 0) return [];
  if (!_config.apiKey) {
    throw new Error('Embedding API key not configured. Set embeddingApiKey in plugin config.');
  }

  // Clean texts: truncate to 8000 chars max per input
  const cleaned = texts.map(t => t.slice(0, 8000));

  const url = `${_config.baseUrl}/embeddings`;
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${_config.apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: _config.model,
      input: cleaned,
    }),
  });

  if (!response.ok) {
    const errText = await response.text().catch(() => '');
    throw new Error(`Embedding API error ${response.status}: ${errText}`);
  }

  const result: EmbeddingResponse = await response.json();

  // Sort by index to match input order
  result.data.sort((a, b) => a.index - b.index);

  return result.data.map(item => item.embedding);
}

// ─── Cosine similarity ────────────────────────────

/**
 * Cosine similarity between two vectors (0-1)
 */
export function cosineSimilarity(a: number[], b: number[]): number {
  if (a.length !== b.length) return 0;

  let dotProduct = 0;
  let normA = 0;
  let normB = 0;

  for (let i = 0; i < a.length; i++) {
    dotProduct += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }

  const denom = Math.sqrt(normA) * Math.sqrt(normB);
  return denom === 0 ? 0 : dotProduct / denom;
}
