/**
 * rag/indexer.ts — Code indexer
 * 
 * Scans a project for source files, extracts symbols using Tree-sitter,
 * generates embeddings via API, and stores them in the vector index.
 * 
 * Strategy:
 *   1. Walk project directory for supported source files
 *   2. Parse each file with Tree-sitter to extract symbol declarations
 *   3. Create a "chunk" per symbol (with context: file path, signature, body)
 *   4. Generate embeddings for each chunk via API
 *   5. Save to disk as .rag-index.json
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, extname, sep } from 'node:path';
import { parseFile, isSupportedExt, LANGUAGE_NAMES } from '../parsers/index.js';
import { extractSymbol } from '../extractor.js';
import { embedBatch } from './embedder.js';
import { loadIndex, saveIndex, createIndex, getIndexPath } from './store.js';
import { CodeChunk, VectorIndex, IndexEntry } from './types.js';

// ─── Supported file extensions ────────────────────

const EXTENSIONS = new Set(['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.mts', '.cts', '.py', '.json', '.css']);

// Directories to skip
const SKIP_DIRS = new Set(['node_modules', '.git', 'dist', '.next', 'build', 'coverage', '.openclaw', '__pycache__', '.rag-index.json']);

// ─── File walker ──────────────────────────────────

function walkDir(dir: string, rootDir: string): string[] {
  const files: string[] = [];

  try {
    const entries = readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = join(dir, entry.name);

      if (entry.isDirectory()) {
        if (SKIP_DIRS.has(entry.name) || entry.name.startsWith('.')) continue;
        files.push(...walkDir(fullPath, rootDir));
      } else if (entry.isFile()) {
        const ext = extname(entry.name).toLowerCase();
        if (EXTENSIONS.has(ext)) {
          files.push(fullPath);
        }
      }
    }
  } catch {
    // Permission denied or not found — skip
  }

  return files;
}

// ─── Extract declarations from a file ─────────────

async function extractChunks(filePath: string, rootPath: string): Promise<CodeChunk[]> {
  const chunks: CodeChunk[] = [];
  const relPath = relative(rootPath, filePath).replace(/\\/g, '/');

  try {
    const { source, root, languageId } = await parseFile(filePath);
    const lines = source.split('\n');
    const langName = LANGUAGE_NAMES[languageId] || languageId;

    for (let i = 0; i < root.childCount; i++) {
      let node = root.child(i);
      if (node.type === 'import_statement') continue;

      // Unwrap export
      if (node.type === 'export_statement') {
        const inner = node.firstNamedChild;
        if (!inner) continue;
        node = inner;
      }

      // Only process named declarations
      const nameNode = node.childForFieldName('name');
      if (!nameNode) continue;

      const symbolName = source.substring(nameNode.startIndex, nameNode.endIndex);
      const kind = mapNodeKind(node.type);

      // Get signature from first line
      const lineStart = node.startPosition.row + 1;
      const lineEnd = node.endPosition.row + 1;
      const body = source.substring(node.startIndex, node.endIndex);

      // Content for embedding = relative path + signature + body (first 500 chars)
      const firstLine = lines[node.startPosition.row] || '';
      const signature = firstLine.trim().length > 150
        ? firstLine.trim().substring(0, 150) + '...'
        : firstLine.trim();

      const contentParts = [
        `File: ${relPath}`,
        `Symbol: ${symbolName}`,
        `Kind: ${kind}`,
        `Body:`,
        body.length > 500 ? body.substring(0, 500) + '\n... (truncated)' : body,
      ];
      const content = contentParts.join('\n');

      chunks.push({
        id: `${relPath}::${symbolName}`,
        filePath: relPath,
        symbolName,
        kind,
        signature,
        content,
        lineStart,
        lineEnd,
        language: langName,
      });
    }
  } catch (err) {
    // Skip files that can't be parsed (unsupported, syntax errors)
    console.error(`[rag] Skip ${relPath}: ${(err as Error).message}`);
  }

  return chunks;
}

function mapNodeKind(nodeType: string): string {
  switch (nodeType) {
    case 'class_declaration': return 'class';
    case 'function_declaration':
    case 'generator_function_declaration': return 'function';
    case 'interface_declaration': return 'interface';
    case 'type_alias_declaration': return 'type';
    case 'lexical_declaration': return 'const';
    default: return nodeType;
  }
}

// ─── Main indexer ─────────────────────────────────

export interface IndexResult {
  totalFiles: number;
  totalChunks: number;
  skippedFiles: number;
  duration: number;
  indexSize: number;
  dimension: number;
}

/**
 * Full index: scan, parse, embed, save
 */
export async function indexProject(
  projectRoot: string,
  dimension: number = 1536,
  onProgress?: (msg: string) => void,
): Promise<IndexResult> {
  const log = onProgress || ((msg: string) => { console.log(`[rag] ${msg}`); });
  const startTime = Date.now();

  // Step 1: Walk files
  log('Walking project directory...');
  const allFiles = walkDir(projectRoot, projectRoot);
  log(`Found ${allFiles.length} source files`);

  // Step 2: Extract chunks
  log('Extracting symbols...');
  const allChunks: CodeChunk[] = [];
  let skipped = 0;

  for (const filePath of allFiles) {
    try {
      const chunks = await extractChunks(filePath, projectRoot);
      allChunks.push(...chunks);
    } catch {
      skipped++;
    }
  }

  log(`Extracted ${allChunks.length} symbols from ${allFiles.length - skipped} files (${skipped} skipped)`);

  // Step 3: Generate embeddings
  if (allChunks.length === 0) {
    log('No symbols found — creating empty index');
    const index = createIndex(projectRoot, dimension);
    saveIndex(projectRoot, index);
    return {
      totalFiles: 0,
      totalChunks: 0,
      skippedFiles: skipped,
      duration: Date.now() - startTime,
      indexSize: 0,
      dimension,
    };
  }

  // Batch embeddings: max 20 per request
  const BATCH_SIZE = 20;
  const entries: IndexEntry[] = [];

  for (let i = 0; i < allChunks.length; i += BATCH_SIZE) {
    const batch = allChunks.slice(i, i + BATCH_SIZE);
    log(`Embedding batch ${Math.floor(i / BATCH_SIZE) + 1}/${Math.ceil(allChunks.length / BATCH_SIZE)} (${batch.length} chunks)...`);

    const embeddings = await embedBatch(batch.map(c => c.content));

    for (let j = 0; j < batch.length; j++) {
      entries.push({
        chunk: batch[j],
        embedding: embeddings[j],
      });
    }
  }

  // Step 4: Save index
  log('Saving index...');
  const index: VectorIndex = {
    version: 1,
    indexedAt: new Date().toISOString(),
    projectRoot,
    totalFiles: allFiles.length - skipped,
    totalChunks: allChunks.length,
    dimension,
    entries,
  };
  saveIndex(projectRoot, index);

  const rawSize = Buffer.byteLength(JSON.stringify(index), 'utf-8');
  log(`Index saved: ${(rawSize / 1024 / 1024).toFixed(2)} MB`);

  return {
    totalFiles: allFiles.length - skipped,
    totalChunks: allChunks.length,
    skippedFiles: skipped,
    duration: Date.now() - startTime,
    indexSize: rawSize,
    dimension,
  };
}
