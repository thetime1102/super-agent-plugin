#!/usr/bin/env node
/**
 * scripts/reindex.mjs — CLI script to reindex a project
 * 
 * Usage:
 *   node scripts/reindex.mjs <project-root> [--dimension 1536]
 * 
 * Examples:
 *   node scripts/reindex.mjs /path/to/project
 *   node scripts/reindex.mjs /path/to/project --dimension 1536
 */

import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync } from 'node:fs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(__dirname, '..');

// Parse args
const args = process.argv.slice(2);
const projectRoot = args[0];
const dimIdx = args.indexOf('--dimension');
const dimension = dimIdx >= 0 ? parseInt(args[dimIdx + 1], 10) : 1536;

if (!projectRoot) {
  console.error(`
Usage: node scripts/reindex.mjs <project-root> [--dimension 1536]

Arguments:
  project-root    Path to project directory to index (required)
  --dimension     Embedding dimension (default: 1536 for ada-002)

Example:
  node scripts/reindex.mjs /home/user/projects/my-app
  node scripts/reindex.mjs C:/Users/me/projects/app --dimension 1536
`);
  process.exit(1);
}

if (!existsSync(projectRoot)) {
  console.error(`Error: Project root not found: ${projectRoot}`);
  process.exit(1);
}

// Load and run
const { indexProject } = await import('../dist/rag/indexer.js');
const { configure } = await import('../dist/rag/embedder.js');

// Read API key from env
const apiKey = process.env.OPENAI_API_KEY || process.env.EMBEDDING_API_KEY || '';
if (!apiKey) {
  console.error(`
Error: No API key found. Set OPENAI_API_KEY or EMBEDDING_API_KEY environment variable.
Or configure in openclaw.json under plugins.entries.super-agent.config.embeddingApiKey
`);
  process.exit(1);
}

configure({ apiKey, dimension });

console.log(`\n🚀 Starting reindex for: ${projectRoot}`);
console.log(`   Dimension: ${dimension}`);
console.log(`   API: text-embedding-ada-002 (OpenAI-compatible)\n`);

const startTime = Date.now();
const result = await indexProject(projectRoot, dimension, (msg) => {
  console.log(`   ${msg}`);
});

const duration = ((Date.now() - startTime) / 1000).toFixed(1);
const sizeMB = (result.indexSize / 1024 / 1024).toFixed(2);

console.log(`\n✅ Indexing complete!`);
console.log(`   Files: ${result.totalFiles}`);
console.log(`   Chunks: ${result.totalChunks}`);
console.log(`   Index size: ${sizeMB} MB`);
console.log(`   Duration: ${duration}s`);
console.log(`   Embedding dimension: ${dimension}`);
