/**
 * rag/search.ts — Semantic search engine
 * 
 * Query → embed → cosine similarity → ranked results
 */

import { embed, cosineSimilarity } from './embedder.js';
import { loadIndex } from './store.js';
import { SearchResult } from './types.js';

/**
 * Max results to return
 */
const MAX_RESULTS = 10;

/**
 * Search the vector index by semantic similarity
 */
export async function search(
  query: string,
  projectRoot: string,
  topK: number = 5,
): Promise<SearchResult[]> {
  const k = Math.min(Math.max(1, topK), MAX_RESULTS);

  // Step 1: Load index
  const index = loadIndex(projectRoot);
  if (!index || index.entries.length === 0) {
    return [];
  }

  // Step 2: Embed query
  const queryVec = await embed(query);

  // Step 3: Compute similarity for all entries
  const scored = index.entries.map(entry => ({
    entry,
    score: cosineSimilarity(queryVec, entry.embedding),
  }));

  // Step 4: Sort by score descending, take top K
  scored.sort((a, b) => b.score - a.score);
  const top = scored.slice(0, k);

  // Step 5: Map to results
  return top.map(item => ({
    filePath: item.entry.chunk.filePath,
    symbolName: item.entry.chunk.symbolName,
    kind: item.entry.chunk.kind,
    signature: item.entry.chunk.signature,
    snippet: item.entry.chunk.content.length > 200
      ? item.entry.chunk.content.substring(0, 200) + '...'
      : item.entry.chunk.content,
    lineStart: item.entry.chunk.lineStart,
    lineEnd: item.entry.chunk.lineEnd,
    language: item.entry.chunk.language,
    score: Math.round(item.score * 10000) / 10000,
  }));
}

/**
 * Quick check if search is available (index exists)
 */
export function isSearchAvailable(projectRoot: string): boolean {
  const index = loadIndex(projectRoot);
  return index !== null && index.entries.length > 0;
}
