/**
 * rag/index.ts — Public API barrel
 * 
 * Usage from plugin:
 *   import { search, indexProject, configureEmbedder } from './rag/index.js';
 *   await indexProject(projectRoot, dimension);
 *   const results = await search(query, projectRoot, topK);
 */

export { search, isSearchAvailable } from './search.js';
export { indexProject, IndexResult } from './indexer.js';
export { configure } from './embedder.js';
export { getIndexStats } from './store.js';
export type { SearchResult, EmbeddingConfig } from './types.js';
