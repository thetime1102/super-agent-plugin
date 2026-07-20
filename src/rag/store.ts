/**
 * rag/store.ts — Vector index file storage
 * 
 * Stores vector embeddings + metadata as JSON on disk.
 * Lightweight, no DB dependency.
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { VectorIndex, IndexEntry } from './types.js';

const INDEX_VERSION = 1;

/**
 * Get the index file path
 */
export function getIndexPath(projectRoot: string): string {
  return join(projectRoot, '.rag-index.json');
}

/**
 * Load index from disk. Returns null if no index exists.
 */
export function loadIndex(projectRoot: string): VectorIndex | null {
  const path = getIndexPath(projectRoot);
  if (!existsSync(path)) return null;

  try {
    const raw = readFileSync(path, 'utf-8');
    return JSON.parse(raw) as VectorIndex;
  } catch (err) {
    console.error(`[rag] Failed to load index: ${(err as Error).message}`);
    return null;
  }
}

/**
 * Save index to disk
 */
export function saveIndex(projectRoot: string, index: VectorIndex): void {
  const path = getIndexPath(projectRoot);
  const dir = dirname(path);

  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }

  writeFileSync(path, JSON.stringify(index, null, 2), 'utf-8');
}

/**
 * Create a new empty index
 */
export function createIndex(projectRoot: string, dimension: number): VectorIndex {
  return {
    version: INDEX_VERSION,
    indexedAt: new Date().toISOString(),
    projectRoot,
    totalFiles: 0,
    totalChunks: 0,
    dimension,
    entries: [],
  };
}

/**
 * Check if index exists and is fresh (indexed within the last 24h)
 */
export function isIndexFresh(projectRoot: string): boolean {
  const index = loadIndex(projectRoot);
  if (!index) return false;

  const indexedAt = new Date(index.indexedAt).getTime();
  const now = Date.now();
  const hoursSinceIndex = (now - indexedAt) / (1000 * 60 * 60);

  return hoursSinceIndex < 24;
}

/**
 * Get stats about the index
 */
export function getIndexStats(projectRoot: string): { exists: boolean; totalChunks: number; totalFiles: number; indexedAt: string | null } {
  const index = loadIndex(projectRoot);
  if (!index) {
    return { exists: false, totalChunks: 0, totalFiles: 0, indexedAt: null };
  }
  return {
    exists: true,
    totalChunks: index.totalChunks,
    totalFiles: index.totalFiles,
    indexedAt: index.indexedAt,
  };
}
