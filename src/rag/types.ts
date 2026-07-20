/**
 * rag/types.ts — Shared types for RAG system
 */

/** A single code chunk indexed for semantic search */
export interface CodeChunk {
  id: string;
  filePath: string;
  symbolName: string;
  kind: string;     // 'function' | 'class' | 'interface' | 'type' | 'const'
  signature: string; // Short signature for display
  content: string;  // Full body for embedding context
  lineStart: number;
  lineEnd: number;
  language: string; // 'typescript' | 'python' | etc.
}

/** Vector index entry (stored on disk) */
export interface IndexEntry {
  chunk: CodeChunk;
  embedding: number[]; // Float32Array serialized to array
}

/** Complete index file structure */
export interface VectorIndex {
  version: number;
  indexedAt: string;    // ISO date
  projectRoot: string;
  totalFiles: number;
  totalChunks: number;
  dimension: number;    // embedding dimension (e.g. 1536 for ada-002)
  entries: IndexEntry[];
}

/** Search result returned to LLM */
export interface SearchResult {
  filePath: string;
  symbolName: string;
  kind: string;
  signature: string;
  snippet: string;      // First 200 chars of body
  lineStart: number;
  lineEnd: number;
  language: string;
  score: number;        // Cosine similarity 0-1
}

/** Embedding provider config */
export interface EmbeddingConfig {
  apiKey: string;
  model: string;
  baseUrl: string;
  dimension: number;
}
